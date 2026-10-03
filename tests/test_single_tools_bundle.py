"""回归：single 模式号称"一次挂全量 tools"，必须真的覆盖 multi 的能力。

此前 single 缺了 discover_relevant_schema（SQL agent 的"优先调用"工具）和
4 个数仓工具（query_warehouse 等），导致同样的问题在 single 模式答不了。
"""

from harness.orchestration.single.tools_bundle import TOOL_HANDLERS, TOOLS


def test_every_tool_has_a_handler():
    names = [t["name"] for t in TOOLS]
    missing = [n for n in names if n not in TOOL_HANDLERS]
    assert not missing, f"以下工具没有注册 handler: {missing}"


def test_single_mode_covers_schema_linking_and_warehouse():
    names = {t["name"] for t in TOOLS}
    required = {
        "discover_relevant_schema",      # Schema Linking（multi 的 SQL agent 有）
        "list_warehouse_tables",         # Olist 数仓（multi 的 hive agent 有）
        "describe_warehouse_table",
        "query_warehouse",
        "query_period_comparison",
    }
    assert required <= names, f"single 模式缺少: {required - names}"


def test_discover_relevant_schema_degrades_without_embedding(monkeypatch):
    """没配 embedding 时应返回 hint，而不是抛 KeyError。"""
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    from harness.tools.schema import discover_relevant_schema
    result = discover_relevant_schema("员工薪资")
    assert result.get("error") is None
    assert "list_tables" in (result.get("hint") or "")
