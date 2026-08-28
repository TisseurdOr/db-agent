"""测试分两层：
1. 单元测试——不调 API，测 Tool / 安全规则，秒级跑完
2. 集成测试——调 agent_loop + 真实 Tool handler（真实 SQLite / 权限），
   LLM 用脚本化 fake（返回预设 tool_use / text），离线可跑、零 API 成本

注意：集成测试调 agent.py 的 agent_loop（非 streaming 版本）。
这是故意的——测试不需要看 streaming 效果，非 streaming 版本更容易断言返回值。
而实际 CLI（main.py）走 streaming_agent，两者共享同一个 agent loop 核心逻辑
（cache_control, _execute_tool, 错误处理），只是输出方式不同。
"""
from types import SimpleNamespace

import pytest

from db.seed import init_db
from harness.tools.analysis import analyze_results, compare_periods
from harness.tools.knowledge import read_memory, save_to_memory, search_knowledge_base
from harness.tools.query import run_query
from harness.tools.schema import describe_table, get_schema_summary, list_tables


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


def test_search_knowledge_base_vector_semantic():
    """向量索引就绪时走向量语义检索（analyst 无 docs_filter，看全部）。"""
    import harness.tools.knowledge as kb
    from harness.tools.knowledge import build_knowledge_base_index
    from tests.fake_embedding import fake_embedding

    build_knowledge_base_index(embed_fn=fake_embedding)
    try:
        result = search_knowledge_base("提成比例", top_k=3)
        assert result["count"] > 0
        assert any("提成" in r["title"] for r in result["results"])
    finally:
        kb._kb_memory.drop()
        kb._kb_memory = None


def test_search_knowledge_base_filter_docs(monkeypatch):
    """viewer 的 docs_filter 过滤掉技术文档类，只留产品手册/销售制度。"""
    import harness.tools.knowledge as kb
    from harness.tools.knowledge import build_knowledge_base_index
    from tests.fake_embedding import fake_embedding

    monkeypatch.setenv("AGENT_USER", "viewer")
    build_knowledge_base_index(embed_fn=fake_embedding)
    try:
        result = search_knowledge_base("HBase scan 命令", top_k=10)
        assert result["count"] > 0
        assert all(r["category"] in ("产品手册", "销售制度") for r in result["results"])
    finally:
        kb._kb_memory.drop()
        kb._kb_memory = None


def test_save_and_read_memory():
    """存一条偏好 → 读出来验证。"""
    save_to_memory("用户偏好按降序排列查询结果", memory_type="preference")
    result = read_memory(memory_type="preference", limit=5)
    assert result["count"] >= 1
    contents = [m["content"] for m in result["memories"]]
    assert any("降序" in c for c in contents)


# ─── 第 2 层：集成测试（离线，脚本化 fake LLM + 真实工具实现）──────


class _FakeTextBlock:
    """模拟 Anthropic 响应的 text block。"""
    type = "text"

    def __init__(self, text: str):
        self.text = text

    def to_dict(self):
        return {"type": "text", "text": self.text}


class _FakeToolUseBlock:
    """模拟 Anthropic 响应的 tool_use block。"""
    type = "tool_use"

    def __init__(self, name: str, input_: dict, id: str = "toolu_test_1"):
        self.id = id
        self.name = name
        self.input = input_

    def to_dict(self):
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


class _FakeMessages:
    """脚本化 messages.create：按调用顺序返回预设 response。

    agent_loop 每轮调一次 client.messages.create：先给 tool_use（执行真实
    handler），再给最终 text（结束循环）。测试用脚本精确控制 Agent 行为，
    同时 tool 执行走真实实现（真实 SQLite / 权限 / 错误处理），零网络零成本。
    """

    def __init__(self, script):
        self._script = [s if isinstance(s, list) else [s] for s in script]
        self.calls = []

    def create(self, **kwargs):
        blocks = self._script.pop(0) if self._script else [_FakeTextBlock("")]
        self.calls.append(blocks)
        return SimpleNamespace(content=blocks)


class _FakeClient:
    """够用的假 Anthropic client——只实现 agent_loop 用到的 messages.create。"""

    def __init__(self, script):
        self.messages = _FakeMessages(script)


@pytest.fixture
def agent_deps():
    """构建集成测试所需的 agent 配置（工具 handler 全为真实实现）。

    用 agent_loop（非 streaming）——测试不需要看 streaming 效果，
    但核心逻辑（cache_control、Tool 调用、错误处理）和 streaming_agent 一致。
    LLM 用脚本化 fake：每轮返回预设的 tool_use / text，不走网络。
    """

    def _build(script):
        from harness.context.system_prompt import build_system_prompt
        from harness.tools.analysis import (
            ANALYZE_RESULTS_TOOL,
            COMPARE_PERIODS_TOOL,
            analyze_results,
            compare_periods,
        )
        from harness.tools.knowledge import read_memory, save_to_memory, search_knowledge_base
        from harness.tools.query import RUN_QUERY_TOOL, run_query
        from harness.tools.schema import (
            DESCRIBE_TABLE_TOOL,
            GET_SCHEMA_SUMMARY_TOOL,
            LIST_TABLES_TOOL,
            describe_table,
            get_schema_summary,
            list_tables,
        )

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
        client = _FakeClient(script)
        return client, prompt, tools, handlers

    return _build


@pytest.mark.asyncio
async def test_agent_list_tables(agent_deps):
    """用户问有哪些表 → Agent 调 list_tables（真实）→ 回答提到 departments / orders"""
    from harness.orchestration.single.agent import agent_loop

    client, prompt, tools, handlers = agent_deps([
        [_FakeToolUseBlock("list_tables", {})],
        [_FakeTextBlock("数据库里有这些表：departments、employees、products、customers、orders。")],
    ])
    result = await agent_loop(client, "数据库里有哪些表？", prompt, tools=tools, handlers=handlers)
    text = result.lower()
    assert "departments" in text
    assert "orders" in text
    # 确认 Agent 确实先调了工具、再出最终回答（两轮 LLM 调用）
    assert len(client.messages.calls) == 2
    assert client.messages.calls[0][0].type == "tool_use"
    assert client.messages.calls[1][0].type == "text"


@pytest.mark.asyncio
async def test_agent_simple_query(agent_deps):
    """用户问销售额 → Agent 写 SQL（真实 run_query 执行）→ 返回结果"""
    from harness.orchestration.single.agent import agent_loop

    sql = (
        "SELECT d.name, SUM(o.total) AS total FROM orders o "
        "JOIN departments d ON o.dept_id = d.id "
        "WHERE d.name = '销售部' GROUP BY d.name"
    )
    client, prompt, tools, handlers = agent_deps([
        [_FakeToolUseBlock("run_query", {"sql": sql})],
        [_FakeTextBlock("销售部的总销售额是 ¥2,347,300。")],
    ])
    result = await agent_loop(client, "销售部的总销售额是多少？", prompt, tools=tools, handlers=handlers)
    assert "销售部" in result
    assert any(c.isdigit() for c in result)
    # run_query 真实执行，返回的 SQL 确实查到了数据
    assert client.messages.calls[0][0].type == "tool_use"


@pytest.mark.asyncio
async def test_agent_unknown_table(agent_deps):
    """查不存在的表 → describe_table（真实）返回结构化错误 → Agent 如实报告"""
    from harness.orchestration.single.agent import agent_loop

    client, prompt, tools, handlers = agent_deps([
        [_FakeToolUseBlock("describe_table", {"table": "inventory"})],
        [_FakeTextBlock("inventory 表不在白名单中，无法查询，请先核对表名。")],
    ])
    result = await agent_loop(client, "查一下 inventory 表的数据", prompt, tools=tools, handlers=handlers)
    assert "不存在" in result or "没有" in result or "找不到" in result or "不在" in result


@pytest.mark.asyncio
async def test_agent_non_query(agent_deps):
    """闲聊 → 无 Tool 调用，直接返回文本"""
    from harness.orchestration.single.agent import agent_loop

    client, prompt, tools, handlers = agent_deps([
        [_FakeTextBlock("你好！我是 db-agent，可以帮你查询数据库、分析数据并给出建议。")],
    ])
    result = await agent_loop(client, "你好，你能做什么？", prompt, tools=tools, handlers=handlers)
    assert len(result) > 0
    # 闲聊场景不应触发任何工具
    assert len(client.messages.calls) == 1


@pytest.mark.asyncio
async def test_agent_rejects_write(agent_deps):
    """写操作 → run_query（真实）在工具层拦截 → Agent 如实告知只读"""
    from harness.orchestration.single.agent import agent_loop

    client, prompt, tools, handlers = agent_deps([
        [_FakeToolUseBlock("run_query", {"sql": "DELETE FROM orders"})],
        [_FakeTextBlock("我不允许执行写操作，只支持只读 SELECT 查询。")],
    ])
    result = await agent_loop(client, "帮我把 orders 表删了", prompt, tools=tools, handlers=handlers)
    assert "不能" in result or "不允许" in result or "拒绝" in result or "只读" in result
    # 真实 run_query 确实拒绝了非 SELECT
    assert len(client.messages.calls) == 2
