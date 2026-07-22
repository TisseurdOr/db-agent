"""0023 多 Agent 编排: Router + DataQuality + SQL + Analysis via LangGraph.

用法:
    from multi_agent.orchestrator import MultiAgentRunner
    # 必须用 create() 工厂方法（异步初始化 SQLite 连接）
    runner = await MultiAgentRunner.create(client, enable_data_quality=True)
    answer = await runner.run("对比华东和华南的销售趋势")
    # 首次调用自动注入 DataQuality，后续跳过
"""

import json
import os
import time
from pathlib import Path

import aiosqlite
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import RunnableConfig
from langchain_core.messages import AIMessage
from anthropic import Anthropic

from multi_agent.state import MultiAgentState
from multi_agent.agents import (
    sql_agent, analysis_agent, strategy_agent,
    data_quality_agent, ROUTER_PROMPT, route_override,
)
from multi_agent.base import is_agent_timeout
from multi_agent.guardrails import guard_input, guard_output
from multi_agent.cache import RouterCache
from utils.llm import extract_text
from utils.tracer import TraceContext

# Checkpointer 数据库路径。
# 图每执行完一个节点，自动把 state 写进这个 SQLite 文件。
# 同一 thread_id 的后续调用从这个文件恢复 state（messages 累积、results 保留）。
# 必须用 AsyncSqliteSaver：graph.ainvoke 走 async checkpoint API，同步版不兼容。
# 开发用 SQLite；生产可换 PostgresSaver。
CHECKPOINT_DB = Path(__file__).resolve().parent.parent / "db" / "agent_state.db"


def _fmt_time(seconds: float) -> str:
    """格式化耗时: <1s 显示 ms, >=1s 显示 s。"""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"


async def _run_agent_with_timeout(agent, client, task, model, trace_span, agent_name: str) -> tuple[str, dict]:
    """包装 agent.run()：检测超时，打日志，返回 (result, usage)。"""
    result, usage = await agent.run(client, task, model=model, verbose=True)
    if is_agent_timeout(result):
        trace_span.error = f"{agent_name} 超过最大轮数"
        print(f"⚠️ {agent_name} 超过最大轮数，结果不可用。请缩小查询范围后重试。")
    return result, usage


# ═══════════════════════════════════════════════════════════════════════════════
# 节点函数
#
# 每个节点 = 图里的一个执行单元，接收 state 返回部分更新。
#
# 为什么用 configurable 而不是 state 传 client/model：
#   Anthropic SDK 的 client 对象不能 JSON 序列化。如果放进 state，
#   Checkpointer 写盘时会崩（TypeError: Type is not msgpack serializable）。
#   configurable 是 LangGraph 专门留给"不可序列化对象"的通道——
#   它随每次调用注入节点，但不被 Checkpointer 持久化。
#   runner.run() 负责把 client 和 model 塞进 configurable。
# ═══════════════════════════════════════════════════════════════════════════════

async def node_router(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Router: 分析用户 query，输出 JSON 执行计划。

    从 state["messages"] 取最近 6 条对话历史，和当前 query 一起发给 LLM。
    这样 Router 能识别"刚才问了什么"等元问题——
    看到历史里上一轮问了"有哪些表"，就知道这不是数据查询。
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("router", "分析意图")

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    router_cache = config["configurable"].get("_router_cache")

    # 硬规则优先：闲聊 / 元问题 / 纯制度查询不依赖 LLM（也避免脏缓存）
    override = route_override(state["query"])
    router_usage = {"input_tokens": 0, "output_tokens": 0, "turns": 0}
    cached_plan = None

    if override is not None:
        plan = override
        span.task = "硬规则覆盖" if plan else "无需数据查询"
        if not plan:
            trace.finish_span(span, router_usage)
            print(trace.print_progress(span))
            return {"plan": [], "next": "done"}
    else:
        # 查缓存：同样 query 之前解析过，直接复用 plan，省一次 LLM 调用（~250t）
        cached_plan = router_cache.get(state["query"]) if router_cache else None

        if cached_plan is not None:
            plan_data = {"plan": cached_plan}
            span.task = f"缓存命中 ({router_cache.hit_rate})"
        else:
            # 从 state["messages"] 取最近 6 条，转成 Anthropic 格式的对话
            recent = [m for m in state.get("messages", [])[-6:]]
            router_msgs = []
            for m in recent:
                role = getattr(m, "type", None)
                content = getattr(m, "content", "")
                if role == "human":
                    router_msgs.append({"role": "user", "content": str(content)[:300]})
                elif role == "ai":
                    router_msgs.append({"role": "assistant", "content": str(content)[:300]})
            router_msgs.append({"role": "user", "content": state["query"]})

            resp = client.messages.create(
                model=model,
                max_tokens=300,
                system=ROUTER_PROMPT,
                messages=router_msgs,
            )
            router_usage = {"input_tokens": 0, "output_tokens": 0, "turns": 1}
            if hasattr(resp, "usage") and resp.usage:
                router_usage["input_tokens"] = resp.usage.input_tokens or 0
                router_usage["output_tokens"] = resp.usage.output_tokens or 0

            text = extract_text(resp, context="router")
            try:
                plan_data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                plan_data = {"plan": [{"agent": "sql", "task": state["query"]}]}

        plan = plan_data.get("plan", [])
        if not plan:
            # 空 plan：真闲聊就结束；否则兜底 sql（避免空白回复）
            # 注意：不要用 len>4 —— 「你好，你能做什么」长度很长但仍是闲聊
            query_text = state["query"].strip()
            if any(m in query_text.lower() for m in (
                "你好", "您好", "hi", "hello", "你能做什么", "你会什么", "你是谁",
            )):
                span.task = "无需数据查询"
                trace.finish_span(span, router_usage)
                print(trace.print_progress(span))
                return {"plan": [], "next": "done"}
            plan = [{"agent": "sql", "task": query_text}]
            span.task = "空 plan → 兜底 sql"

    # 缓存写入：仅 LLM 路径；硬规则不写缓存（避免污染）
    if override is None and cached_plan is None and router_cache is not None and plan:
        router_cache.set(state["query"], plan)

    # DataQuality 首次注入：在 plan 最前面插入 DQ 检查，先扫库再查数。
    if state.get("_inject_dq") and not any(s["agent"] == "data_quality" for s in plan):
        plan.insert(0, {
            "agent": "data_quality",
            "task": "检查数据库整体数据质量：表行数、日期连续性、NULL比例、异常值。输出事实报告，不做业务判断。",
        })
    plan_names = " → ".join(s["agent"] for s in plan)
    span.task = plan_names
    trace.finish_span(span, router_usage)
    print(trace.print_progress(span))
    return {"plan": plan, "next": plan[0]["agent"]}


async def node_data_quality(state: MultiAgentState, config: RunnableConfig) -> dict:
    """DataQuality Agent: 扫一遍数据质量（NULL 比例、日期连续性、异常值）。"""
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == "data_quality")
    span = trace.start_span("data_quality", task[:60])
    print(f"⏳ DataQuality: {task[:60]}...")
    result, usage = await _run_agent_with_timeout(data_quality_agent, client, task, model, span, "DataQuality")
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ DataQuality ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    results = {**state.get("results", {}), "data_quality": result}
    return _next_step(state, results, "data_quality")


async def node_sql(state: MultiAgentState, config: RunnableConfig) -> dict:
    """SQL Agent: 查数据库（list_tables / describe_table / run_query）。"""
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == "sql")
    span = trace.start_span("sql", task[:60])
    print(f"⏳ SQL Agent: {task[:60]}...")
    result, usage = await _run_agent_with_timeout(sql_agent, client, task, model, span, "SQL")
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ SQL Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    results = {**state.get("results", {}), "sql": result}
    return _next_step(state, results, "sql")


async def node_strategy(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Strategy Agent: 查公司制度文档（search_knowledge_base）。"""
    client = config["configurable"]["_client"]
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    task = next(s["task"] for s in state["plan"] if s["agent"] == "strategy")
    span = trace.start_span("strategy", task[:60])
    print(f"⏳ Strategy Agent: {task[:60]}...")
    result, usage = await _run_agent_with_timeout(strategy_agent, client, task, model, span, "Strategy")
    trace.finish_span(span, usage, error=span.error)
    print(f"✅ Strategy Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")
    results = {**state.get("results", {}), "strategy": result}
    return _next_step(state, results, "strategy")


async def node_analysis(state: MultiAgentState, config: RunnableConfig) -> dict:
    """Analysis Agent: 综合所有中间结果 + 记忆，生成最终回答。

    注入 Analysis Agent 上下文的四层信息（按优先级）：
    1. 早期摘要（_conversation_summary）—— ConversationManager 压缩的超窗口对话
    2. 向量记忆（_recalled_memories）—— 跨会话的长期记忆
    3. 最近对话（messages）—— Checkpointer 累积的本轮消息
    4. 中间结果（results）—— 上游 Agent 的执行输出
    """
    trace = config["configurable"].get("_trace") or TraceContext(state.get("query", ""))
    span = trace.start_span("analysis", "综合分析")

    client = config["configurable"]["_client"]
    model = config["configurable"].get("_model", os.getenv("ANTHROPIC_MODEL", "deepseek-chat"))
    context_parts = []
    # Layer 1: 早期对话摘要——ConversationManager 将超窗口消息压缩为摘要
    if state.get("_conversation_summary"):
        context_parts.insert(0, f"[早期对话摘要]\n{state['_conversation_summary']}")
    # Layer 2: 向量记忆——跨会话语义召回
    if state.get("_recalled_memories"):
        context_parts.insert(0, f"[历史相关对话]\n{state['_recalled_memories']}")
    # Layer 3: 最近对话——Checkpointer 累积的 messages
    recent = []
    for m in state.get("messages", [])[-6:]:
        role = "用户" if getattr(m, "type", "") == "human" else "助手"
        content = str(getattr(m, "content", ""))[:500]
        if content:
            recent.append(f"{role}: {content}")
    if recent:
        context_parts.insert(0, f"[最近对话]\n" + "\n".join(recent))
    # Layer 4: 上游 Agent 的中间结果
    for name, text in state.get("results", {}).items():
        context_parts.append(f"[{name} Agent 结果]\n{text}")

    # 检测上游结果是否有超时——如果有，在 context 里加提示
    for name, text in state.get("results", {}).items():
        if is_agent_timeout(text):
            context_parts.append(f"[警告] 上游 {name} Agent 超过最大轮数未完成，其结果为无效文本，请忽略并告知用户重试。")

    print(f"⏳ Analysis Agent: 综合分析中...")
    result, usage = await analysis_agent.run(
        client,
        task=state["query"],
        context="\n\n".join(context_parts),
        model=model,
    )
    if is_agent_timeout(result):
        span.error = "Analysis 超过最大轮数"
        print(f"⚠️ Analysis 超过最大轮数")
    print(f"✅ Analysis Agent ({_fmt_time(span.elapsed)} · {span.total_tokens}t · {usage['turns']}轮)")

    # Layer 3 输出护栏：检查 PII 泄露、system prompt 泄露、异常输出
    passed, reason = guard_output(result)
    if not passed:
        print(f"🚫 输出护栏拦截: {reason}")
        # finish span before set blocked
        trace.finish_span(span, usage, error = reason)
        trace.set_blocked("output", reason)
        
        return {
            "final_answer": reason,
            "messages": [AIMessage(content=reason)],
        }
    # 处理超时情况，正常情况是NONE，超时才传参进span
    trace.finish_span(span, usage, error=span.error)

    # 把最终回答写回 messages —— Checkpointer 自动持久化，
    # 下一轮 Router 和 Analysis 就能从 messages 里看到这轮说了什么。
    return {
        "final_answer": result,
        "messages": [AIMessage(content=result)],
    }


def _next_step(state: MultiAgentState, results: dict, current: str) -> dict:
    """调度核心：对比 plan 和已执行的 Agent，决定下一个节点。

    逻辑：
    1. plan 中还有没执行的 agent → 路由到下一个
    2. plan 里声明了 analysis 且还没跑 → 再去 analysis
    3. 否则直接用上游结果当 final_answer（不要无脑追加 analysis）
       —— 否则「销售部有多少员工」也会被分析师包装成「抱歉，我无法查库」
    """
    plan = state["plan"]
    executed = set(results.keys())               # 已打卡的 Agent 名
    pending = [s for s in plan if s["agent"] not in executed]  # 还没执行的

    if pending:
        return {"results": results, "next": pending[0]["agent"]}

    plan_agents = {s["agent"] for s in plan}
    if "analysis" in plan_agents and "analysis" not in executed:
        return {"results": results, "next": "analysis"}

    # plan 已跑完且不需要 analysis：优先用 sql/strategy 原文作答
    answer = (
        results.get("sql")
        or results.get("strategy")
        or results.get("data_quality")
        or "\n\n".join(results.values())
    )
    return {"results": results, "next": "done", "final_answer": answer}


# ── 路由函数 ──

def edge_router(state: MultiAgentState) -> str:
    """条件边路由：读 state["next"]，决定下一站。

    返回的字符串必须是 targets dict 的 key（"sql"、"analysis"、"done" 等）。
    不能直接返回 END（"__end__"）——targets 里没有这个 key 会抛 KeyError。
    通过 targets["done"] → END 间接映射。
    """
    return state.get("next") or "done"


# ── 构建 Graph ──

def build_multi_agent_graph(checkpointer=None):
    """组装多 Agent 编排图。

    拓扑：
    __start__ → router → (条件边) → sql/strategy/data_quality → analysis → END

    所有非 analysis 节点都挂同一套条件边（edge_router + targets），
    执行完后由 _next_step 写 state["next"]，edge_router 读 next 做路由。

    checkpointer 参数：传入 AsyncSqliteSaver 等实例后，图每步自动存盘。
    """
    builder = StateGraph(MultiAgentState)

    builder.add_node("router", node_router)
    builder.add_node("data_quality", node_data_quality)
    builder.add_node("sql", node_sql)
    builder.add_node("strategy", node_strategy)
    builder.add_node("analysis", node_analysis)

    builder.set_entry_point("router")

    # targets: edge_router 返回值 → LangGraph 节点名的映射
    targets = {
        "data_quality": "data_quality",
        "sql": "sql",
        "strategy": "strategy",
        "analysis": "analysis",
        "done": END,
    }

    builder.add_conditional_edges("router", edge_router, targets)
    builder.add_conditional_edges("data_quality", edge_router, targets)
    builder.add_conditional_edges("sql", edge_router, targets)
    builder.add_conditional_edges("strategy", edge_router, targets)
    builder.add_edge("analysis", END)

    return builder.compile(checkpointer=checkpointer)


# ═══════════════════════════════════════════════════════════════════════════════
# 高层封装：给 main.py 用的 Runner
# ═══════════════════════════════════════════════════════════════════════════════

class MultiAgentRunner:
    """多 Agent 执行器：封装图的构造、Checkpointer 初始化、state 注入。

    必须用工厂方法构造（不能用 MultiAgentRunner(...)）：
        runner = await MultiAgentRunner.create(client, model="deepseek-chat")

    原因：AsyncSqliteSaver 需要 await 来初始化数据库连接和建表，
    __init__ 是同步的做不到。create() 是 async classmethod，可以 await。
    """

    def __init__(self, *args, **kwargs):
        raise TypeError(
            "请使用 `runner = await MultiAgentRunner.create(...)`，"
            "不要直接 MultiAgentRunner(...)。"
        )

    @classmethod
    async def create(
        cls,
        client: Anthropic,
        model: str = "deepseek-chat",
        enable_data_quality: bool = True,
        checkpoint_db: Path | str | None = None,
        thread_id: str = "default-session",
    ) -> "MultiAgentRunner":
        """异步工厂方法：初始化 SQLite 连接 + Checkpointer + 编译图。

        Args:
            client: Anthropic SDK 客户端（不可序列化，走 configurable 注入）
            model: LLM 模型名
            enable_data_quality: 首次查询是否自动注入 DataQuality
            checkpoint_db: Checkpointer 数据库路径（默认 db/agent_state.db）
            thread_id: 会话标识——同一 thread_id 共享 messages 历史
        """
        self = object.__new__(cls)
        self.client = client
        self.model = model
        self.enable_data_quality = enable_data_quality
        self._dq_done = False       # 首次查询后置 True，后续不再注入 DQ
        self._dq_time = 0.0         # 上次 DQ 执行时间戳（epoch 秒）
        self.router_cache = RouterCache(max_size=100)
        self.thread_id = thread_id

        db_path = Path(checkpoint_db) if checkpoint_db else CHECKPOINT_DB
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(db_path))
        self.checkpointer = AsyncSqliteSaver(self._conn)
        await self.checkpointer.setup()  # 建 checkpoints 表
        self.graph = build_multi_agent_graph(checkpointer=self.checkpointer)
        self.checkpoint_db = db_path
        return self

    def _should_inject_dq(self) -> bool:
        """DataQuality 时效控制：首次扫描后 1 小时内跳过。

        关闭开关（--no-dq）→ 永远 False。
        首次 DQ 执行后记录时间，1 小时内不再触发。
        超时后允许重新扫描（数据可能已变化）。
        """
        if not self.enable_data_quality:
            return False
        if self._dq_done:
            elapsed = time.time() - self._dq_time
            if elapsed < 3600:    # 1 小时内跳过
                return False
            self._dq_done = False  # 超时，允许重新扫
        self._dq_done = True
        self._dq_time = time.time()
        return True

    async def run(self, query: str, recalled_memories: str = "", conversation_summary: str = "") -> str:
        """执行一次多 Agent 查询，返回 final_answer 文本。

        Args:
            query: 用户输入的自然语言问题
            recalled_memories: pre-turn 向量召回的记忆文本（main.py 预处理后传入）
            conversation_summary: ConversationManager 压缩的早期对话摘要（注入 node_analysis）
        """
        # 每个请求创建一个 TraceContext——跟着 configurable 在节点间流转
        trace = TraceContext(query)

        # Layer 1 输入护栏：在 LLM 调用前拦截恶意输入，零 token 成本
        passed, reason = guard_input(query)
        if not passed:
            print(f"🚫 输入护栏拦截: {reason}")
            trace.set_blocked("input", reason)
            trace.save()
            return reason

        # configurable: 放不可序列化的对象（client / trace）和只读配置
        config = {
            "configurable": {
                "thread_id": self.thread_id,
                "_client": self.client,
                "_model": self.model,
                "_trace": trace,
                "_router_cache": self.router_cache,
            }
        }
        result = await self.graph.ainvoke({
            "query": query,
            "messages": [{"role": "user", "content": query}],
            "_recalled_memories": recalled_memories,
            "_conversation_summary": conversation_summary,
            "_inject_dq": self._should_inject_dq(),
            "plan": [],
            "results": {},
            "final_answer": "",
            "next": "",
            "_stats": {},
        }, config=config)

        # 所有节点运行完，落盘 trace
        trace.finished_at = time.monotonic()
        filepath = trace.save()
        print(f"📊 {trace.summary()}  → {filepath}")

        return result.get("final_answer", "抱歉，无法回答。")

    async def aclose(self) -> None:
        """关闭 SQLite 连接。main.py quit 时调用，避免 event loop 关闭后报错。"""
        await self._conn.close()
