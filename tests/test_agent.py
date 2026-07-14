"""测试分两层：
1. 单元测试（默认）——不调 API，测 Tool / 安全规则，秒级跑完
2. 集成测试（可选）——真调 Claude，需要 ANTHROPIC_API_KEY，较慢较贵
"""
import os
import pytest
from dotenv import load_dotenv

from db.seed import init_db
from tools.schema import list_tables, describe_table
from tools.query import run_query
from tools.analysis import analyze_results

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
    result = describe_table("inventory")
    assert "error" in result


def test_run_query_select():
    result = run_query("SELECT name FROM departments ORDER BY id")
    assert result["count"] == 3
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


# ─── 第 2 层：集成测试（需要真 API，默认跳过）─────────────────────

requires_api = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="需要 ANTHROPIC_API_KEY 才能跑集成测试",
)


@pytest.fixture
def agent_deps():
    import agent
    from anthropic import Anthropic
    from tools.schema import LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL
    from tools.query import RUN_QUERY_TOOL
    from tools.analysis import ANALYZE_RESULTS_TOOL

    agent.TOOLS = [LIST_TABLES_TOOL, DESCRIBE_TABLE_TOOL, RUN_QUERY_TOOL, ANALYZE_RESULTS_TOOL]
    agent.TOOL_HANDLERS = {
        "list_tables": list_tables,
        "describe_table": describe_table,
        "run_query": run_query,
        "analyze_results": analyze_results,
    }
    prompt = open("prompts/system_prompt.md", encoding="utf-8").read()
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return client, prompt


@requires_api
@pytest.mark.asyncio
async def test_agent_list_tables(agent_deps):
    """用户问有哪些表 → Agent 应提到 departments / orders"""
    from agent import agent_loop
    client, prompt = agent_deps
    result = await agent_loop(client, "数据库里有哪些表？", prompt)
    text = result.lower()
    assert "departments" in text
    assert "orders" in text


@requires_api
@pytest.mark.asyncio
async def test_agent_simple_query(agent_deps):
    """用户问销售额 → Agent 探索表结构 → 写 SQL → 返回结果"""
    from agent import agent_loop
    client, prompt = agent_deps
    result = await agent_loop(client, "销售部的总销售额是多少？", prompt)
    assert "销售部" in result
    assert any(c.isdigit() for c in result)


@requires_api
@pytest.mark.asyncio
async def test_agent_unknown_table(agent_deps):
    from agent import agent_loop
    client, prompt = agent_deps
    result = await agent_loop(client, "查一下 inventory 表的数据", prompt)
    assert "不存在" in result or "没有" in result or "找不到" in result


@requires_api
@pytest.mark.asyncio
async def test_agent_non_query(agent_deps):
    from agent import agent_loop
    client, prompt = agent_deps
    result = await agent_loop(client, "你好，你能做什么？", prompt)
    assert len(result) > 0


@requires_api
@pytest.mark.asyncio
async def test_agent_rejects_write(agent_deps):
    from agent import agent_loop
    client, prompt = agent_deps
    result = await agent_loop(client, "帮我把 orders 表删了", prompt)
    assert "不能" in result or "不允许" in result or "拒绝" in result or "只读" in result
