"""MultiAgentRunner：图的执行器（checkpoint 初始化 / run / resume / aclose）。

从原 orchestrator.py 拆分（职责：编排的"入口"）。
"""

import os
import re
import time
from pathlib import Path

import aiosqlite
from anthropic import Anthropic
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from harness.constraints.guardrails import guard_input
from harness.observation.cost import estimate_tokens_cost
from harness.observation.opik_tracing import (
    annotate_opik,
    capture_opik_trace_id_for_graph,
    flush_opik,
    get_current_opik_trace_id,
    opik_tag_guard,
    opik_tag_hitl,
    wrap_langgraph,
)
from harness.observation.ops_metrics import record_elapsed, record_query
from harness.observation.tracer import TraceContext
from harness.orchestration.multi.agent_names import AGENT_SQL
from harness.orchestration.multi.cache import RouterCache
from harness.orchestration.multi.graph import build_multi_agent_graph
from harness.orchestration.multi.state import agent_config
from harness.orchestration.multi.task_system import TaskManager

# Checkpointer 数据库路径。
# 图每执行完一个节点，自动把 state 写进这个 SQLite 文件。
# 同一 thread_id 的后续调用从这个文件恢复 state（messages 累积、results 保留）。
# 必须用 AsyncSqliteSaver：graph.ainvoke 走 async checkpoint API，同步版不兼容。
# 开发用 SQLite；生产可换 PostgresSaver。


CHECKPOINT_DB = Path(__file__).resolve().parents[3] / "db" / "agent_state.db"
class MultiAgentRunner:
    """多 Agent 执行器：封装图的构造、Checkpointer 初始化、state 注入。

    必须用工厂方法构造（不能用 MultiAgentRunner(...)）：
        runner = await MultiAgentRunner.create(client, model="deepseek-chat")

    原因：AsyncSqliteSaver 需要 await 来初始化数据库连接和建表，
    __init__ 是同步的做不到。create() 是 async classmethod，可以 await。
    """

    # ── 实例属性声明（create() 用 object.__new__ 构造，pyright 需要类级注解）──
    client: object
    graph: object
    model: str
    enable_data_quality: bool
    router_cache: object
    task_manager: object
    thread_id: str
    checkpointer: object
    checkpoint_db: object
    _redis_cm: object
    _conn: object
    _current_config: dict | None
    _last_state: dict | None
    _dq_done: bool
    _dq_time: float


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
        checkpoint_redis_url: str | None = None,
        checkpoint_prefix: str | None = None,
    ) -> "MultiAgentRunner":
        """异步工厂方法：初始化 SQLite 连接 + Checkpointer + 编译图。

        Args:
            client: Anthropic SDK 客户端（不可序列化，走 configurable 注入）
            model: LLM 模型名
            enable_data_quality: 首次查询是否自动注入 DataQuality
            checkpoint_db: Checkpointer 数据库路径（默认 db/agent_state.db）
            thread_id: 会话标识——同一 thread_id 共享 messages 历史
            checkpoint_redis_url: 显式指定 Redis checkpoint URL（覆盖 REDIS_URL 环境变量）
            checkpoint_prefix: Redis checkpoint key 前缀（测试隔离用）
        """
        self = object.__new__(cls)
        self.client = client
        self.model = model
        self.enable_data_quality = enable_data_quality
        self._dq_done = False       # 首次查询后置 True，后续不再注入 DQ
        self._dq_time = 0.0         # 上次 DQ 执行时间戳（epoch 秒）
        self.router_cache = RouterCache(max_size=100)
        self.task_manager = TaskManager()
        self.thread_id = thread_id
        self._last_state = None  # 最近一次 graph 执行完成后的 state（供 UI 读取中间结果）

        # 状态外置（可选）：配置 REDIS_URL 后 checkpoint 存 Redis（多实例共享 + TTL 自动清理）。
        # 未配置 / Redis 不可用时自动降级回 SQLite——不阻断启动。
        self._redis_cm = None
        self.checkpointer = None
        redis_url = (checkpoint_redis_url or os.getenv("REDIS_URL", "")).strip()
        if redis_url:
            try:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                ttl_minutes = int(os.getenv("REDIS_CHECKPOINT_TTL_MINUTES", "1440"))
                saver_kwargs: dict = {"ttl": {"default_ttl": ttl_minutes}}  # 24h 自动过期，防无限膨胀
                if checkpoint_prefix:
                    saver_kwargs["checkpoint_prefix"] = checkpoint_prefix
                cm = AsyncRedisSaver.from_conn_string(redis_url, **saver_kwargs)
                self.checkpointer = await cm.__aenter__()
                await self.checkpointer.setup()
                self._redis_cm = cm
                self.checkpoint_db = redis_url
                print(f"📌 Checkpointer: Redis（{redis_url}，TTL {ttl_minutes} 分钟）")
            except Exception as e:
                print(f"⚠️ Redis Checkpointer 初始化失败（{type(e).__name__}: {e}），降级到 SQLite")
                self._redis_cm = None
                self.checkpointer = None

        if self.checkpointer is None:
            db_path = Path(checkpoint_db) if checkpoint_db else CHECKPOINT_DB
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(db_path))
            self.checkpointer = AsyncSqliteSaver(self._conn)
            await self.checkpointer.setup()  # 建 checkpoints 表
            self.checkpoint_db = db_path

        self.graph = wrap_langgraph(
            build_multi_agent_graph(checkpointer=self.checkpointer)
        )
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
        """执行一次查询（带运维指标记录：耗时 / 成败）。"""
        import time as _t
        _t0 = _t.monotonic()
        _ok = False
        try:
            _result = await self._run_impl(query, recalled_memories, conversation_summary)
            _ok = True
            return _result
        finally:
            record_query(succeeded=_ok)
            record_elapsed(_t.monotonic() - _t0)

    async def _run_impl(self, query: str, recalled_memories: str = "", conversation_summary: str = "") -> str:
        """执行一次多 Agent 查询，返回 final_answer 文本。

        如果遇到 HITL 审批中断，返回 {"__interrupt__": True, "data": {...}}。
        调用方检测到此标记后展示 SQL 给用户，确认后调 resume() 恢复执行。

        Args:
            query: 用户输入的自然语言问题
            recalled_memories: pre-turn 向量召回的记忆文本（main.py 预处理后传入）
            conversation_summary: ConversationManager 压缩的早期对话摘要（注入 node_analysis）
        """
        # 每个请求创建一个 TraceContext——跟着 configurable 在节点间流转
        trace = TraceContext(query, thread_id=self.thread_id)
        annotate_opik(metadata={
            "recalled_memories_present": bool(recalled_memories),
            "conversation_summary_present": bool(conversation_summary),
        })

        # Layer 1 输入护栏：在 LLM 调用前拦截恶意输入，零 token 成本
        passed, reason = guard_input(query)
        if not passed:
            print(f"🚫 输入护栏拦截: {reason}")
            opik_tag_guard("input", reason)
            trace.set_blocked("input", reason)
            trace.save()
            flush_opik()
            return reason

        self._current_config = {
            "configurable": {
                "thread_id": self.thread_id,
                "_client": self.client,
                "_model": self.model,
                "_trace": trace,
                "_router_cache": self.router_cache,
                "_task_manager": self.task_manager,
            }
        }
        state = {
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
            "_reflection_attempts": 0,
            "_replan_attempts": 0,
            "_replan_feedback": "",
            "_skip_confidence": False,
            "_skip_reflection": False,
        }

        try:
            result = await self.graph.ainvoke(state, config=self._current_config)
        except GraphInterrupt:
            # 旧版 LangGraph: interrupt() 抛异常，graph 在敏感 SQL 处暂停
            result = None

        # 检查是否被 interrupt 暂停（新/旧版本兼容）
        snapshot = await self.graph.aget_state(self._current_config)
        if snapshot and snapshot.interrupts:
            # 暂停时也保存 state——这样 HITL 场景下调用方（如 eval_runner）
            # 能通过 get_execution_info() 读到 plan，不必去翻 trace 文件
            self._last_state = dict(snapshot.values or {})
            interrupt_data = snapshot.interrupts[0].value if snapshot.interrupts else {}
            hitl_type = interrupt_data.get("type") if isinstance(interrupt_data, dict) else None
            pause_meta = {"hitl_type": hitl_type} if hitl_type else {}
            if isinstance(interrupt_data, dict) and "confidence" in interrupt_data:
                pause_meta["confidence"] = interrupt_data.get("confidence")
            opik_tag_hitl("pause", **{k: v for k, v in pause_meta.items() if v is not None})
            # trace 暂不存盘——等 resume() 完成后一起写
            return {"__interrupt__": True, "data": interrupt_data}

        if result is None:
            return "抱歉，无法回答。"

        # 保存 state 供 UI 读取中间结果
        self._last_state = result

        # 所有节点运行完，落盘 trace。
        # finished_at 由 trace.save() 用 time.time() 补——之前在这里手动赋
        # time.monotonic()，和 started_at 的 time.time() 基准不同，算出负耗时。
        filepath = trace.save()
        print(f"📊 {trace.summary()}  → {filepath}")
        self._annotate_turn_cost(trace)
        flush_opik()

        return result.get("final_answer", "抱歉，无法回答。")

    def _annotate_turn_cost(self, trace: TraceContext) -> None:
        """Annotate Opik with per-turn token cost + local_trace_id + opik_trace_id."""
        cost = estimate_tokens_cost(
            trace.total_input_tokens,
            trace.total_output_tokens,
            model=self.model,
        )
        oid = get_current_opik_trace_id() or capture_opik_trace_id_for_graph(
            getattr(self, "graph", None)
        )
        if oid:
            trace.opik_trace_id = oid
        meta = {**cost, "local_trace_id": trace.trace_id}
        if oid:
            meta["opik_trace_id"] = oid
        annotate_opik(metadata=meta, tags=["turn_complete"])
        # If Opik context already closed (e.g. streaming ainvoke), update via tracer
        if oid and getattr(self, "graph", None) is not None:
            try:
                tracer = getattr(self.graph, "_opik_tracer", None)
                if tracer is not None:
                    created = tracer.created_traces()
                    for t in reversed(created or []):
                        if getattr(t, "id", None) == oid:
                            t.update(metadata=meta, tags=["turn_complete"])
                            break
            except Exception:
                pass

    async def resume(self, approved: bool = True, clarified_query: str = "") -> str:
        """HITL 审批后恢复 graph 执行。

        Args:
            approved: True = 用户批准，继续执行敏感查询（confidence_gate 场景）
            clarified_query: 用户对澄清问题的回复（clarify 场景）
        """
        opik_tag_hitl("resume", approved=bool(approved))
        resume_value = {"approved": approved}
        if clarified_query:
            resume_value["clarified_query"] = clarified_query

        trace = agent_config(self._current_config).get("_trace")
        result = await self.graph.ainvoke(
            Command(resume=resume_value),
            config=self._current_config,
        )
        self._last_state = result
        if trace:
            filepath = trace.save()  # finished_at 由 save() 统一补，避免时钟基准混用
            print(f"📊 {trace.summary()}  → {filepath}")
            self._annotate_turn_cost(trace)
            flush_opik()
        return result.get("final_answer", "抱歉，无法回答。")

    def get_execution_info(self) -> dict:
        """返回最近一次执行的结构化信息（供 UI / eval_runner 展示）。

        从 _last_state 提取：Router 分派方式、执行计划、SQL、Reflection 结果。
        tokens 从内存中的 TraceContext 读取（不依赖 trace 文件落盘，
        HITL 暂停时也能拿到已累计的数字）。
        """
        state = self._last_state or {}
        results = state.get("results", {})

        # 提取 SQL（从 sql agent 的结果中）
        sql_text = ""
        sql_result = results.get(AGENT_SQL, "")
        if sql_result:
            sql_match = re.search(r'(SELECT|WITH)\s.+?(?:;|$)', sql_result, re.IGNORECASE | re.DOTALL)
            sql_text = sql_match.group(0).strip() if sql_match else ""

        # plan 里的 agent 名列表（eval 的 agent_in_plan / agent_not_in_plan 断言用）
        plan = state.get("plan", []) or []
        plan_agents = [s.get("agent", "") for s in plan if s.get("agent")]

        # token 统计：从内存 trace 读（interrupt 后 resume 前也有值）
        # 防御性读取：未执行过 run() 的 runner（或测试桩）可能没有 _current_config
        tokens = 0
        config = getattr(self, "_current_config", None) or {}
        trace = config.get("configurable", {}).get("_trace")
        if trace is not None:
            tokens = int(getattr(trace, "total_input_tokens", 0) or 0) + int(
                getattr(trace, "total_output_tokens", 0) or 0
            )

        return {
            "plan": plan,
            "plan_agents": plan_agents,
            "tokens": tokens,
            "sql": sql_text,
            "reflection": {
                "attempts": state.get("_reflection_attempts", 0),
            },
            "stats": state.get("_stats", {}),
        }

    async def aclose(self) -> None:
        """关闭 Checkpointer 连接（Redis 或 SQLite）。main.py quit 时调用。"""
        flush_opik()
        if self._redis_cm is not None:
            await self._redis_cm.__aexit__(None, None, None)
        elif hasattr(self, "_conn"):
            await self._conn.close()
