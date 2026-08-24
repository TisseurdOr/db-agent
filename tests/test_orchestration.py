"""编排层调度 + schema 工具测试 —— 全部零 API 成本的纯逻辑测试。

覆盖：
- _next_step 的调度顺序与多引擎结果拼接（sql+hive 不能丢一个）
- MultiAgentRunner.get_execution_info 的结构化信息提取（UI 面板数据源）
- discover_relevant_schema 工具封装（索引未就绪时的降级提示）
- Agent 装配：hive 用隔离版 list_tables、sql 挂 schema discovery、strategy 挂指标口径
"""

import harness.tools.schema as schema_mod
from harness.orchestration.multi.agents import (
    HIVE_AGENT_PROMPT,
    hive_agent,
    sql_agent,
    strategy_agent,
)
from harness.orchestration.multi.orchestrator import MultiAgentRunner, _next_step
from harness.tools.schema import HIVE_SIM_TABLES, discover_relevant_schema, list_hive_tables

# ═══ 1. _next_step 调度 ═══

def test_next_step_runs_pending_agent_first():
    """plan 里还有没执行的 Agent → 先执行它，不提前收尾。"""
    state = {"plan": [{"agent": "sql", "task": "查数"}, {"agent": "hive", "task": "查数仓"}]}
    update = _next_step(state, {"sql": "SQL 结果"}, "sql")
    assert update["next"] == "hive"
    assert "final_answer" not in update


def test_next_step_goes_to_analysis_when_planned():
    """plan 声明了 analysis 且还没跑 → 去 analysis，不直接 done。"""
    state = {"plan": [{"agent": "sql", "task": "x"}, {"agent": "analysis", "task": "y"}]}
    update = _next_step(state, {"sql": "结果"}, "sql")
    assert update["next"] == "analysis"


def test_next_step_single_result_returned_verbatim():
    """单一结果且无 analysis → 原文作答，不加标签包装。"""
    state = {"plan": [{"agent": "sql", "task": "x"}]}
    update = _next_step(state, {"sql": "共 5 行"}, "sql")
    assert update["next"] == "done"
    assert update["final_answer"] == "共 5 行"


def test_next_step_concatenates_multi_engine_results():
    """sql+hive 双引擎 → 两个结果都要在 final_answer 里，带来源标签。"""
    state = {"plan": [{"agent": "sql", "task": "x"}, {"agent": "hive", "task": "y"}]}
    update = _next_step(state, {"sql": "业务库 3 张表", "hive": "数仓 3 张表"}, "hive")
    assert update["next"] == "done"
    answer = update["final_answer"]
    assert "【sql】" in answer and "业务库 3 张表" in answer
    assert "【hive】" in answer and "数仓 3 张表" in answer


def test_next_step_hive_only_fallback():
    """只有 hive 结果时也能作答（hive 在兜底链里，不能只认 sql）。"""
    state = {"plan": [{"agent": "hive", "task": "x"}]}
    update = _next_step(state, {"hive": "数仓结果"}, "hive")
    assert update["final_answer"] == "数仓结果"


def test_next_step_ignores_empty_results_in_concat():
    """空结果不参与拼接——sql 为空时只剩 hive，按单结果原文返回。"""
    state = {"plan": [{"agent": "sql", "task": "x"}, {"agent": "hive", "task": "y"}]}
    update = _next_step(state, {"sql": "", "hive": "数仓结果"}, "hive")
    assert update["final_answer"] == "数仓结果"
    assert "【" not in update["final_answer"]


# ═══ 2. get_execution_info（UI 面板数据源）═══

def _bare_runner(last_state) -> MultiAgentRunner:
    """跳过 __init__（会连数据库），只测 get_execution_info 的纯提取逻辑。"""
    runner = MultiAgentRunner.__new__(MultiAgentRunner)
    runner._last_state = last_state
    return runner


def test_execution_info_extracts_sql_from_result():
    runner = _bare_runner({
        "plan": [{"agent": "sql", "task": "华东销售"}],
        "results": {"sql": "查询结果如下：\nSELECT name, total FROM orders WHERE region = '华东';\n共 5 行"},
        "_reflection_attempts": 1,
        "_stats": {"elapsed": 0.5, "nodes": ["router"]},
    })
    info = runner.get_execution_info()
    assert info["sql"].startswith("SELECT")
    assert info["sql"].endswith(";")
    assert info["plan"][0]["agent"] == "sql"
    assert info["reflection"]["attempts"] == 1
    assert info["stats"]["nodes"] == ["router"]


def test_execution_info_empty_state_has_safe_defaults():
    """还没执行过任何查询时（_last_state=None）返回空默认值，不抛异常。"""
    info = _bare_runner(None).get_execution_info()
    assert info["plan"] == []
    assert info["sql"] == ""
    assert info["reflection"]["attempts"] == 0


def test_execution_info_no_sql_in_text():
    """sql 结果里没有 SELECT/WITH 语句 → sql 字段为空串，不误提取。"""
    info = _bare_runner({"results": {"sql": "查询失败，表不存在"}}).get_execution_info()
    assert info["sql"] == ""


# ═══ 3. discover_relevant_schema 工具封装 ═══

def test_discover_schema_falls_back_when_index_not_ready(monkeypatch):
    """索引未就绪（返回空）→ 给 Agent 降级指引：改用 list_tables + describe_table。"""
    monkeypatch.setattr(schema_mod, "discover_schema_for_query", lambda q: "")
    result = discover_relevant_schema("华东销售趋势")
    assert result["schema_text"] == ""
    assert result["field_count"] == 0
    assert "list_tables" in result["hint"]


def test_discover_schema_counts_fields(monkeypatch):
    """正常返回 → field_count 按字段行计数（两空格缩进的行）。"""
    fake_schema = (
        "[相关表结构]\n"
        "\n## orders\n"
        "  total REAL  -- 订单金额\n"
        "  region TEXT\n"
        "\n## customers\n"
        "  region TEXT  -- 所在区域"
    )
    monkeypatch.setattr(schema_mod, "discover_schema_for_query", lambda q: fake_schema)
    result = discover_relevant_schema("华东销售趋势")
    assert result["schema_text"] == fake_schema
    assert result["field_count"] == 3
    assert "hint" not in result


# ═══ 4. Agent 装配 ═══

def test_hive_agent_uses_isolated_list_tables():
    """Hive Agent 的 list_tables 必须是隔离版——只返回 ods_/dwd_/dim_ 模拟表，
    否则会把业务表 departments/orders 当成 Hive 表。"""
    assert hive_agent.handlers["list_tables"] is list_hive_tables
    # sql Agent 用的仍是全量版
    assert sql_agent.handlers["list_tables"] is not list_hive_tables


def test_hive_prompt_forbids_business_tables():
    """prompt 里明确禁止把业务表当 Hive 表（硬约束的软化层）。"""
    assert "禁止" in HIVE_AGENT_PROMPT
    assert "departments" in HIVE_AGENT_PROMPT


def test_hive_sim_tables_are_warehouse_style():
    """隔离白名单只含 ods_/dwd_/dim_ 风格表名。"""
    assert all(t.startswith(("ods_", "dwd_", "dim_")) for t in HIVE_SIM_TABLES)


def test_sql_agent_has_schema_discovery_tool():
    """SQL Agent 挂载 discover_relevant_schema，且 prompt 要求优先调用。"""
    tool_names = [t["name"] for t in sql_agent.tools]
    assert "discover_relevant_schema" in tool_names
    assert sql_agent.handlers["discover_relevant_schema"] is discover_relevant_schema
    assert "discover_relevant_schema" in sql_agent.system_prompt


def test_strategy_agent_has_lookup_metric():
    """Strategy Agent 挂载 lookup_metric，回答指标口径问题。"""
    assert "lookup_metric" in strategy_agent.handlers
    tool_names = [t["name"] for t in strategy_agent.tools]
    assert "lookup_metric" in tool_names
"""
新增：多 Agent 编排节点函数的 mock 单元测试。

用 monkeypatch 替换 agent.run / client.messages.create / interrupt，
零 API 成本验证 Router、SQL、Analysis、Reflection、Confidence Gate、DQ 的调度行为。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from harness.observation.tracer import TraceContext
from harness.orchestration.multi import orchestrator as orchestrator_mod
from harness.orchestration.multi.orchestrator import (
    node_analysis,
    node_confidence_gate,
    node_data_quality,
    node_hbase,
    node_hive,
    node_reflection,
    node_router,
    node_sql,
    node_strategy,
)
from harness.orchestration.multi.task_system import TaskManager

# ═══════════════════════════════════════════════════════════════════════════════
# Mock 工具
# ═══════════════════════════════════════════════════════════════════════════════

def _make_config(monkeypatch, **extra) -> dict:
    """构造一个可注入 node 函数的 RunnableConfig。"""
    trace = TraceContext("测试查询")
    config = {
        "configurable": {
            "thread_id": "test-thread",
            "_client": MagicMock(),
            "_model": "test-model",
            "_trace": trace,
            "_router_cache": orchestrator_mod.RouterCache(max_size=10),
            "_task_manager": TaskManager(tasks_dir=extra.pop("tasks_dir", None)),
            **extra,
        }
    }
    # 静默 opik 相关函数，避免外部调用
    monkeypatch.setattr(orchestrator_mod, "opik_tag_route", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "opik_tag_guard", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "opik_tag_hitl", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "opik_tag_fewshot", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "opik_tag_reflection", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "opik_tag_task_board", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "opik_tag_sql", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator_mod, "flush_opik", lambda: None)
    _patch_acall(monkeypatch)
    return config


def _patch_acall(monkeypatch):
    """把 orchestrator 的 acall_with_retry 替换为直接调用并 await 返回的 async 包装。"""
    async def _acall(fn, *a, **k):
        result = fn(*a, **k)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    monkeypatch.setattr(orchestrator_mod, "acall_with_retry", _acall)


def _patch_sql_imports(monkeypatch):
    """node_sql 在函数体内 import 了这些符号，需要 patch 它们的来源模块。"""
    import harness.context.sql_examples as examples_mod
    import harness.memory.feedback as feedback_mod
    import harness.tools.query as query_mod
    monkeypatch.setattr(query_mod, "pop_last_successful_sql", lambda: None)
    monkeypatch.setattr(examples_mod, "get_sql_fewshot", lambda q: "")
    monkeypatch.setattr(feedback_mod, "learn_from_success", lambda *a, **k: False)


def _make_llm_response(text: str, input_tokens: int = 10, output_tokens: int = 5):
    """模拟 Anthropic messages.create 返回。"""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _make_agent_run(result: str = "模拟结果"):
    async def fake_run(client, task, context="", model=None, verbose=False):
        return result, {"input_tokens": 5, "output_tokens": 5, "turns": 1}
    return fake_run


# ═══════════════════════════════════════════════════════════════════════════════
# node_router
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_router_hard_rule_skips_llm(monkeypatch):
    """闲聊 query 走硬规则，不应调用 LLM。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    state = {"query": "你好，你能做什么", "messages": [], "plan": [], "results": {}}
    update = await node_router(state, config)
    assert update["next"] == "done"
    assert update["plan"] == []
    client.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_router_hard_rule_routes_strategy(monkeypatch):
    """制度类 query 走硬规则直接派 strategy。"""
    config = _make_config(monkeypatch)
    state = {"query": "销售人员的提成比例是多少", "messages": [], "plan": [], "results": {}}
    update = await node_router(state, config)
    assert update["next"] == "strategy"
    assert update["plan"][0]["agent"] == "strategy"


@pytest.mark.asyncio
async def test_router_llm_path_parses_json_plan(monkeypatch):
    """非硬规则 query 调用 LLM 并解析 JSON plan。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"plan": [{"agent": "sql", "task": "查销售额"}]})
    ))

    # 用一个不命中任何硬规则的 query，确保进入 LLM 路径
    state = {"query": "本月营收情况如何", "messages": [], "plan": [], "results": {}}
    update = await node_router(state, config)
    assert update["next"] == "sql"
    assert update["plan"][0]["agent"] == "sql"
    client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_router_cache_hit_skips_llm(monkeypatch):
    """同一 query 第二次应命中缓存，不调用 LLM。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"plan": [{"agent": "sql", "task": "查销售额"}]})
    ))

    state = {"query": "缓存测试营收", "messages": [], "plan": [], "results": {}}
    await node_router(state, config)
    calls_before = client.messages.create.call_count
    update2 = await node_router(state, config)
    assert update2["next"] == "sql"
    assert client.messages.create.call_count == calls_before  # 缓存命中，不再调用


@pytest.mark.asyncio
async def test_router_injects_data_quality_on_first_turn(monkeypatch):
    """_inject_dq=True 时 plan 最前面插入 data_quality。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"plan": [{"agent": "sql", "task": "查销售额"}]})
    ))

    state = {"query": "本月营收", "messages": [], "plan": [], "results": {}, "_inject_dq": True}
    update = await node_router(state, config)
    assert update["plan"][0]["agent"] == "data_quality"
    assert update["plan"][1]["agent"] == "sql"


@pytest.mark.asyncio
async def test_router_replan_ignores_cache_and_rules(monkeypatch):
    """重规划路径必须走 LLM，不能用缓存里的失败 plan。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"plan": [{"agent": "sql", "task": "新 plan"}]})
    ))

    state = {
        "query": "重规划测试营收",
        "messages": [],
        "plan": [],
        "results": {},
        "_replan_feedback": "上一轮 sql 超时",
        "_replan_attempts": 0,
    }
    update = await node_router(state, config)
    assert update["next"] == "sql"
    # 重规划会调用 LLM
    client.messages.create.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# node_sql / node_strategy / node_hbase / node_hive
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_node_sql_runs_agent_and_schedules_next(monkeypatch):
    """SQL 节点执行 agent，并把结果写入 results，调度到下一步。"""
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.sql_agent, "run", _make_agent_run("SQL 结果"))
    _patch_sql_imports(monkeypatch)

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [{"agent": "sql", "task": "查销售额"}, {"agent": "analysis", "task": "分析"}],
        "results": {},
    }
    update = await node_sql(state, config)
    assert update["results"]["sql"] == "SQL 结果"
    assert update["next"] == "analysis"


@pytest.mark.asyncio
async def test_node_sql_replan_on_timeout(monkeypatch):
    """SQL Agent 超时时触发重规划。"""
    config = _make_config(monkeypatch)
    monkeypatch.setattr(
        orchestrator_mod.sql_agent, "run",
        _make_agent_run("(Agent 在 8 轮内未完成)")
    )
    _patch_sql_imports(monkeypatch)

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [{"agent": "sql", "task": "查销售额"}],
        "results": {},
        "_replan_attempts": 0,
    }
    update = await node_sql(state, config)
    assert update["next"] == "router"
    assert "sql" not in update["results"]
    assert update["_replan_attempts"] == 1


@pytest.mark.asyncio
async def test_node_strategy_runs_and_schedules(monkeypatch):
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.strategy_agent, "run", _make_agent_run("制度结果"))
    state = {
        "query": "提成比例",
        "messages": [],
        "plan": [{"agent": "strategy", "task": "查提成"}],
        "results": {},
    }
    update = await node_strategy(state, config)
    assert update["results"]["strategy"] == "制度结果"
    assert update["next"] == "done"


@pytest.mark.asyncio
async def test_node_hbase_runs_and_schedules(monkeypatch):
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.hbase_agent, "run", _make_agent_run("HBase 结果"))
    state = {
        "query": "scan orders",
        "messages": [],
        "plan": [{"agent": "hbase", "task": "scan orders"}],
        "results": {},
    }
    update = await node_hbase(state, config)
    assert update["results"]["hbase"] == "HBase 结果"
    assert update["next"] == "done"


@pytest.mark.asyncio
async def test_node_hive_runs_and_schedules(monkeypatch):
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.hive_agent, "run", _make_agent_run("Hive 结果"))
    state = {
        "query": "Hive 订单数",
        "messages": [],
        "plan": [{"agent": "hive", "task": "Hive 订单数"}, {"agent": "sql", "task": "SQL 订单数"}],
        "results": {},
    }
    update = await node_hive(state, config)
    assert update["results"]["hive"] == "Hive 结果"
    assert update["next"] == "sql"


@pytest.mark.asyncio
async def test_node_data_quality_runs_and_schedules(monkeypatch):
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.data_quality_agent, "run", _make_agent_run("质量报告"))
    state = {
        "query": "检查数据",
        "messages": [],
        "plan": [{"agent": "data_quality", "task": "检查数据"}, {"agent": "sql", "task": "查销售额"}],
        "results": {},
    }
    update = await node_data_quality(state, config)
    assert update["results"]["data_quality"] == "质量报告"
    assert update["next"] == "sql"


# ═══════════════════════════════════════════════════════════════════════════════
# node_confidence_gate
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_confidence_gate_high_passes_to_next(monkeypatch):
    """高置信度直接放行，继续 _next_step。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"confidence": 0.85, "scores": {}, "explanation": "ok"})
    ))

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [{"agent": "sql", "task": "查销售额"}, {"agent": "analysis", "task": "分析"}],
        "results": {"sql": "SELECT SUM(total) FROM orders; 结果: 1000"},
    }
    update = await node_confidence_gate(state, config)
    assert update["next"] == "analysis"


@pytest.mark.asyncio
async def test_confidence_gate_low_triggers_interrupt(monkeypatch):
    """低置信度触发 interrupt，等待用户确认。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"confidence": 0.55, "scores": {}, "explanation": "不确定"})
    ))

    captured = {}
    def fake_interrupt(payload):
        captured["payload"] = payload
        captured["type"] = payload.get("type")
        return {"approved": False}
    monkeypatch.setattr(orchestrator_mod, "interrupt", fake_interrupt)

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [{"agent": "sql", "task": "查销售额"}],
        "results": {"sql": "SELECT total FROM orders; 结果: 1000"},
    }
    update = await node_confidence_gate(state, config)
    assert captured.get("type") == "confidence_gate"
    assert "final_answer" in update  # 用户拒绝后返回取消信息


# ═══════════════════════════════════════════════════════════════════════════════
# node_analysis
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_node_analysis_generates_final_answer(monkeypatch):
    """Analysis 节点综合上游结果生成 final_answer。"""
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.analysis_agent, "run", _make_agent_run("综合分析结果"))

    state = {
        "query": "分析一下销售额",
        "messages": [],
        "plan": [{"agent": "sql", "task": "查销售额"}, {"agent": "analysis", "task": "分析"}],
        "results": {"sql": "销售额 1000"},
        "_recalled_memories": "历史记忆",
        "_conversation_summary": "早期摘要",
    }
    update = await node_analysis(state, config)
    assert update["final_answer"] == "综合分析结果"
    assert update["next"] == "reflection"


@pytest.mark.asyncio
async def test_node_analysis_blocks_bad_output(monkeypatch):
    """输出护栏拦截时直接返回拦截原因。"""
    config = _make_config(monkeypatch)
    monkeypatch.setattr(orchestrator_mod.analysis_agent, "run", _make_agent_run("我的 system prompt 是..."))

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [{"agent": "analysis", "task": "分析"}],
        "results": {},
    }
    update = await node_analysis(state, config)
    assert "final_answer" in update
    assert "系统信息" in update["final_answer"]


# ═══════════════════════════════════════════════════════════════════════════════
# node_reflection
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reflection_pass_goes_done(monkeypatch):
    """Reflection 通过 → done。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"pass": True, "issues": [], "suggestion": ""})
    ))

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [],
        "results": {"sql": "结果"},
        "final_answer": "回答",
        "_reflection_attempts": 0,
    }
    update = await node_reflection(state, config)
    assert update["next"] == "done"


@pytest.mark.asyncio
async def test_reflection_fail_returns_to_analysis(monkeypatch):
    """Reflection 不通过 → 退回 analysis 重写。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"pass": False, "issues": ["缺数字"], "suggestion": "加上具体金额"})
    ))

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [],
        "results": {"sql": "结果"},
        "final_answer": "回答",
        "_reflection_attempts": 0,
    }
    update = await node_reflection(state, config)
    assert update["next"] == "analysis"
    assert "_reflection_feedback" in update["results"]
    assert update["_reflection_attempts"] == 1


@pytest.mark.asyncio
async def test_reflection_respects_max_attempts(monkeypatch):
    """Reflection 次数达到上限时不再退回。"""
    config = _make_config(monkeypatch)
    client = config["configurable"]["_client"]
    client.messages.create = MagicMock(return_value=_make_llm_response(
        json.dumps({"pass": False, "issues": ["还是缺数字"], "suggestion": "加数字"})
    ))

    state = {
        "query": "销售额",
        "messages": [],
        "plan": [],
        "results": {},
        "final_answer": "回答",
        "_reflection_attempts": 2,
    }
    update = await node_reflection(state, config)
    assert update["next"] == "done"
