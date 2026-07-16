"""测试分两层：
1. 单元测试（默认）——不调 API，测 Tool / 安全规则，秒级跑完
2. 集成测试（可选）——真调 API，需要 ANTHROPIC_API_KEY，较慢较贵

注意：集成测试调 agent.py 的 agent_loop（非 streaming 版本）。
这是故意的——测试不需要看 streaming 效果，非 streaming 版本更容易断言返回值。
而实际 CLI（main.py）走 streaming_agent，两者共享同一个 agent loop 核心逻辑
（cache_control, _execute_tool, 错误处理），只是输出方式不同。
"""
import os
import pytest
from dotenv import load_dotenv

from db.seed import init_db
from tools.schema import list_tables, describe_table, get_schema_summary
from tools.query import run_query
from tools.analysis import analyze_results, compare_periods
from tools.knowledge import search_knowledge_base, save_to_memory, read_memory

load_dotenv()


@pytest.fixture(autouse=True)
def fresh_db():
    """每个测试前重置数据库，保证数据一致。"""
    init_db(reset=True)


# ─── 第 1 层：单元测试（不需要 API Key）───────────────────────────

def test_list_tables_unit():
    result = list_tables()
    assert "departments" in result["tables"]
    assert "orders" in result["tables"]


def test_describe_table_unit():
    result = describe_table("orders")
    names = [c["name"] for c in result["columns"]]
    assert "total" in names
    assert "dept_id" in names


def test_describe_missing_table():
    """测试白名单校验——不存在的表名应返回结构化错误。"""
    result = describe_table("inventory")
    assert "error" in result
    # 新错误格式：error + message + suggestion + hint（lesson 0008 标准）
    assert result["error"] is True
    assert "message" in result
    assert "suggestion" in result


def test_get_schema_summary_unit():
    """一次性拿到所有表和字段的摘要。扩数据后 ≥4 张表。"""
    result = get_schema_summary()
    assert result["table_count"] >= 4  # departments, employees, products, orders + user_memory
    table_names = [t["name"] for t in result["tables"]]
    assert "departments" in table_names
    assert "orders" in table_names
    assert "employees" in table_names


def test_run_query_select():
    result = run_query("SELECT name FROM departments ORDER BY id")
    assert result["count"] >= 3  # 扩数据后 ≥3 个部门
    assert result["rows"][0]["name"] == "销售部"


def test_run_query_rejects_write():
    result = run_query("DROP TABLE orders")
    assert "error" in result
    assert "SELECT" in result["error"]


def test_analyze_results_ranking():
    rows = [
        {"name": "销售部", "sales": 380000},
        {"name": "市场部", "sales": 414000},
        {"name": "研发部", "sales": 98000},
    ]
    result = analyze_results(rows, "sales", "name", "上周销售额排名")
    assert result["ranking"][0]["label"] == "市场部"
    assert result["ranking"][0]["rank"] == 1
    assert "chart_suggestion" in result


def test_compare_periods_growth():
    """同比/环比: 市场部增长 50%，研发部下滑 20%。"""
    p1 = [
        {"dept": "销售部", "total": 100000},
        {"dept": "市场部", "total": 80000},
        {"dept": "研发部", "total": 50000},
    ]
    p2 = [
        {"dept": "销售部", "total": 110000},   # +10%
        {"dept": "市场部", "total": 120000},   # +50%
        {"dept": "研发部", "total": 40000},    # -20%
    ]
    result = compare_periods("1月", "2月", p1, p2, "dept", "total")
    assert result["total_change_pct"] == 17.4  # (270000-230000)/230000
    assert any(g["label"] == "市场部" for g in result["top_growers"])
    assert any(d["label"] == "研发部" for d in result["top_decliners"])


def test_compare_periods_empty():
    result = compare_periods("1月", "2月", [], [], "x", "y")
    assert result.get("error") is True


def test_search_knowledge_base_match():
    result = search_knowledge_base("提成比例")
    assert result["count"] > 0
    assert any("提成" in r["title"] for r in result["results"])


def test_search_knowledge_base_no_match():
    result = search_knowledge_base("火星移民政策")
    assert result["count"] == 0
    assert result.get("hint") is not None


def test_save_and_read_memory():
    """存一条偏好 → 读出来验证。"""
    save_to_memory("用户偏好按降序排列查询结果", memory_type="preference")
    result = read_memory(memory_type="preference", limit=5)
    assert result["count"] >= 1
    contents = [m["content"] for m in result["memories"]]
    assert any("降序" in c for c in contents)


# ─── 第 2 层：集成测试（需要真 API，默认跳过）─────────────────────

requires_api = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="需要 ANTHROPIC_API_KEY 才能跑集成测试",
)


@pytest.fixture
def agent_deps():
    """构建集成测试所需的 agent 配置。
    用 agent_loop（非 streaming）——测试不需要看 streaming 效果，
    但核心逻辑（cache_control、Tool 调用、错误处理）和 streaming_agent 一致。
    """
    from anthropic import Anthropic
    from tools.schema import (
        LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, GET_SCHEMA_SUMMARY_TOOL,
        list_tables, describe_table, get_schema_summary,
    )
    from tools.query import RUN_QUERY_TOOL, run_query
    from tools.analysis import (
        ANALYZE_RESULTS_TOOL, analyze_results,
        COMPARE_PERIODS_TOOL, compare_periods,
    )
    from tools.knowledge import search_knowledge_base, save_to_memory, read_memory
    from prompts.system_prompt import build_system_prompt

    tools = [
        LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, GET_SCHEMA_SUMMARY_TOOL,
        RUN_QUERY_TOOL, ANALYZE_RESULTS_TOOL, COMPARE_PERIODS_TOOL,
        search_knowledge_base.tool_schema, save_to_memory.tool_schema, read_memory.tool_schema,
    ]
    handlers = {
        "list_tables": list_tables,
        "describe_table": describe_table,
        "get_schema_summary": get_schema_summary,
        "run_query": run_query,
        "analyze_results": analyze_results,
        "compare_periods": compare_periods,
        "search_knowledge_base": search_knowledge_base,
        "save_to_memory": save_to_memory,
        "read_memory": read_memory,
    }
    prompt = build_system_prompt(db_type="sqlite", user_role="测试工程师")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return client, prompt, tools, handlers


@requires_api
@pytest.mark.asyncio
async def test_agent_list_tables(agent_deps):
    """用户问有哪些表 → Agent 应提到 departments / orders"""
    from agent import agent_loop
    client, prompt, tools, handlers = agent_deps
    result = await agent_loop(client, "数据库里有哪些表？", prompt, tools=tools, handlers=handlers)
    text = result.lower()
    assert "departments" in text
    assert "orders" in text


@requires_api
@pytest.mark.asyncio
async def test_agent_simple_query(agent_deps):
    """用户问销售额 → Agent 探索表结构 → 写 SQL → 返回结果"""
    from agent import agent_loop
    client, prompt, tools, handlers = agent_deps
    result = await agent_loop(client, "销售部的总销售额是多少？", prompt, tools=tools, handlers=handlers)
    assert "销售部" in result
    assert any(c.isdigit() for c in result)


@requires_api
@pytest.mark.asyncio
async def test_agent_unknown_table(agent_deps):
    from agent import agent_loop
    client, prompt, tools, handlers = agent_deps
    result = await agent_loop(client, "查一下 inventory 表的数据", prompt, tools=tools, handlers=handlers)
    assert "不存在" in result or "没有" in result or "找不到" in result


@requires_api
@pytest.mark.asyncio
async def test_agent_non_query(agent_deps):
    from agent import agent_loop
    client, prompt, tools, handlers = agent_deps
    result = await agent_loop(client, "你好，你能做什么？", prompt, tools=tools, handlers=handlers)
    assert len(result) > 0


@requires_api
@pytest.mark.asyncio
async def test_agent_rejects_write(agent_deps):
    from agent import agent_loop
    client, prompt, tools, handlers = agent_deps
    result = await agent_loop(client, "帮我把 orders 表删了", prompt, tools=tools, handlers=handlers)
    assert "不能" in result or "不允许" in result or "拒绝" in result or "只读" in result
