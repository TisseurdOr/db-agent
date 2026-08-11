"""编排层调度 + schema 工具测试 —— 全部零 API 成本的纯逻辑测试。

覆盖：
- _next_step 的调度顺序与多引擎结果拼接（sql+hive 不能丢一个）
- MultiAgentRunner.get_execution_info 的结构化信息提取（UI 面板数据源）
- discover_relevant_schema 工具封装（索引未就绪时的降级提示）
- Agent 装配：hive 用隔离版 list_tables、sql 挂 schema discovery、strategy 挂指标口径
"""

import tools.schema as schema_mod
from multi_agent.orchestrator import _next_step, MultiAgentRunner
from multi_agent.agents import sql_agent, hive_agent, strategy_agent, HIVE_AGENT_PROMPT
from tools.schema import list_hive_tables, discover_relevant_schema, HIVE_SIM_TABLES


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
