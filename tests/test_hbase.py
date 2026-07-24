"""测试 HBase 内存模拟引擎: run_hbase + _seed_hbase_store。"""

import pytest
from tools.hbase import (
    run_hbase, _seed_hbase_store, _HBASE_STORE, _HBASE_META, _HBASE_ROW_ORDER,
    _HBASE_DISABLED, _apply_prefix_filter, _parse_simple_filter,
)


@pytest.fixture(autouse=True)
def reset_store():
    """每个测试前重置内存存储。"""
    _HBASE_STORE.clear()
    _HBASE_META.clear()
    _HBASE_ROW_ORDER.clear()
    _HBASE_DISABLED.clear()
    _seed_hbase_store()


# ═══════════════════════════════════════════════════════════════════════════════
# list
# ═══════════════════════════════════════════════════════════════════════════════

def test_list_tables():
    """list 返回所有表名。"""
    result = run_hbase(operation="list", table_name="")
    assert "tables" in result
    assert "orders" in result["tables"]
    assert "user_profile" in result["tables"]
    assert "product_catalog" in result["tables"]


# ═══════════════════════════════════════════════════════════════════════════════
# scan
# ═══════════════════════════════════════════════════════════════════════════════

def test_scan_orders():
    """scan orders 表返回行数据。"""
    result = run_hbase(operation="scan", table_name="orders", limit=10)
    assert result["operation"] == "scan"
    assert result["count"] == 10
    assert len(result["rows"]) == 10
    row = result["rows"][0]
    assert "row_key" in row
    assert "columns" in row
    assert "cf:total" in row["columns"]


def test_scan_with_range():
    """scan 支持 start_row / stop_row 范围过滤。"""
    result = run_hbase(
        operation="scan", table_name="orders",
        start_row="order_005", stop_row="order_010", limit=50,
    )
    row_keys = [r["row_key"] for r in result["rows"]]
    assert all(rk >= "order_005" for rk in row_keys)
    assert all(rk < "order_010" for rk in row_keys)


def test_scan_with_filter():
    """scan 支持简单过滤器表达式。"""
    result = run_hbase(
        operation="scan", table_name="orders",
        filter_expr="cf:status = 'completed'", limit=50,
    )
    for row in result["rows"]:
        assert row["columns"]["cf:status"] == "completed"


def test_scan_truncation():
    """scan 超过 limit 时标记 truncated。"""
    result = run_hbase(operation="scan", table_name="orders", limit=5)
    assert result["count"] == 5
    assert result["truncated"] is True
    assert result["scanned_rows"] > 5


def test_scan_empty_table():
    """scan 空表返回空结果。"""
    _HBASE_STORE["empty_t"] = {}
    _HBASE_META["empty_t"] = ["cf"]
    _HBASE_ROW_ORDER["empty_t"] = []
    result = run_hbase(operation="scan", table_name="empty_t")
    assert result["count"] == 0
    assert result["rows"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# get
# ═══════════════════════════════════════════════════════════════════════════════

def test_get_existing_row():
    """get 存在行键返回单行数据。"""
    result = run_hbase(operation="get", table_name="orders", row_key="order_001")
    assert result["count"] == 1
    assert result["rows"][0]["row_key"] == "order_001"
    assert "cf:total" in result["rows"][0]["columns"]


def test_get_missing_row():
    """get 不存在行键返回 hint。"""
    result = run_hbase(operation="get", table_name="orders", row_key="nonexistent")
    assert result["count"] == 0
    assert "hint" in result


# ═══════════════════════════════════════════════════════════════════════════════
# count
# ═══════════════════════════════════════════════════════════════════════════════

def test_count_orders():
    """count 返回表行数。"""
    result = run_hbase(operation="count", table_name="orders")
    assert result["count"] == 30


def test_count_with_filter():
    """count 支持 filter 过滤计数。"""
    result = run_hbase(
        operation="count", table_name="orders",
        filter_expr="cf:status = 'completed'",
    )
    assert result["count"] > 0
    assert result["count"] <= 30


# ═══════════════════════════════════════════════════════════════════════════════
# put / delete
# ═══════════════════════════════════════════════════════════════════════════════

def test_put_hitl_blocked():
    """put 在 graph 上下文外被 HITL 拦截。"""
    result = run_hbase(
        operation="put", table_name="orders",
        row_key="order_001", column="cf:note", value="test note",
    )
    assert result.get("error") is True
    assert "审批" in result.get("message", "")


def test_delete_hitl_blocked():
    """delete 在 graph 上下文外被 HITL 拦截。"""
    result = run_hbase(
        operation="delete", table_name="orders",
        row_key="order_001", column="cf:status",
    )
    assert result.get("error") is True
    assert "审批" in result.get("message", "")


def test_drop_hitl_blocked():
    """drop 在 graph 上下文外被 HITL 拦截。"""
    result = run_hbase(operation="drop", table_name="orders")
    assert result.get("error") is True
    assert "审批" in result.get("message", "")


def test_truncate_hitl_blocked():
    """truncate 在 graph 上下文外被 HITL 拦截。"""
    result = run_hbase(operation="truncate", table_name="orders")
    assert result.get("error") is True
    assert "审批" in result.get("message", "")


def test_put_in_graph_with_hitl_approved():
    """put 在 graph 内触发 HITL → 审批通过 → 数据写入 → get 验证。"""
    import asyncio
    from typing import TypedDict
    from langgraph.graph import StateGraph, END
    from langgraph.types import Command
    from langgraph.checkpoint.memory import InMemorySaver

    class PutState(TypedDict):
        done: bool

    def put_node(state: PutState):
        result = run_hbase(
            operation="put", table_name="orders",
            row_key="order_001", column="cf:note", value="HITL_INTEGRATION_OK",
        )
        if "error" not in result:
            return {"done": True}
        return {"done": False}

    async def run_cycle():
        builder = StateGraph(PutState)
        builder.add_node("put", put_node)
        builder.set_entry_point("put")
        builder.add_edge("put", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        config = {"configurable": {"thread_id": "test-hitl-put-001"}}
        await graph.ainvoke({"done": False}, config)

        # interrupt 应已触发
        snapshot = await graph.aget_state(config)
        assert snapshot.interrupts, "应有 pending interrupt"

        # 审批通过，恢复执行
        await graph.ainvoke(Command(resume={"approved": True}), config)
        assert _HBASE_STORE["orders"]["order_001"]["cf:note"] == "HITL_INTEGRATION_OK"

        # get 验证
        get_result = run_hbase(operation="get", table_name="orders", row_key="order_001")
        assert get_result["rows"][0]["columns"]["cf:note"] == "HITL_INTEGRATION_OK"

    asyncio.run(run_cycle())


def test_delete_in_graph_with_hitl_approved():
    """delete 在 graph 内触发 HITL → 审批通过 → 行被删除。"""
    import asyncio
    from typing import TypedDict
    from langgraph.graph import StateGraph, END
    from langgraph.types import Command
    from langgraph.checkpoint.memory import InMemorySaver

    # 直接写入一行（绕过 HITL，模拟已存在数据）
    _HBASE_STORE["orders"]["order_temp"] = {"cf:x": "1"}
    _HBASE_ROW_ORDER["orders"].append("order_temp")
    assert "order_temp" in _HBASE_STORE["orders"]

    class DelState(TypedDict):
        done: bool

    def delete_node(state: DelState):
        result = run_hbase(
            operation="delete", table_name="orders", row_key="order_temp",
        )
        if "error" not in result:
            return {"done": True}
        return {"done": False}

    async def run_cycle():
        builder = StateGraph(DelState)
        builder.add_node("delete", delete_node)
        builder.set_entry_point("delete")
        builder.add_edge("delete", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        config = {"configurable": {"thread_id": "test-hitl-delete-001"}}
        await graph.ainvoke({"done": False}, config)

        snapshot = await graph.aget_state(config)
        assert snapshot.interrupts, "应有 pending interrupt"

        await graph.ainvoke(Command(resume={"approved": True}), config)
        assert "order_temp" not in _HBASE_STORE["orders"]

    asyncio.run(run_cycle())


# ═══════════════════════════════════════════════════════════════════════════════
# 错误处理
# ═══════════════════════════════════════════════════════════════════════════════

def test_invalid_operation():
    """不支持的操作返回 error + 可用操作列表。"""
    result = run_hbase(operation="join", table_name="orders")
    assert result.get("error") is True
    assert "suggestion" in result


def test_missing_table():
    """不存在的表返回 error。"""
    result = run_hbase(operation="scan", table_name="nonexistent")
    assert result.get("error") is True


def test_get_missing_row_key():
    """get 缺少 row_key 返回 error。"""
    result = run_hbase(operation="get", table_name="orders")
    assert result.get("error") is True


def test_put_missing_column():
    """put 缺少 column 返回 error。"""
    result = run_hbase(operation="put", table_name="orders", row_key="r1", value="v")
    assert result.get("error") is True


def test_seed_idempotent():
    """_seed_hbase_store 幂等——重复调用不重复插入。"""
    count1 = _HBASE_STORE["orders"]["order_001"]["cf:total"]
    _seed_hbase_store()
    count2 = _HBASE_STORE["orders"]["order_001"]["cf:total"]
    assert count1 == count2


# ═══════════════════════════════════════════════════════════════════════════════
# DDL: create / desc / disable / enable / drop / truncate
# ═══════════════════════════════════════════════════════════════════════════════

def test_create_table():
    """create 创建新表，指定列族。"""
    result = run_hbase(
        operation="create", table_name="test_tbl",
        column_families="cf1,cf2",
    )
    assert result["operation"] == "create"
    assert "test_tbl" in _HBASE_STORE
    assert _HBASE_META["test_tbl"] == ["cf1", "cf2"]


def test_create_duplicate_table():
    """create 已存在的表返回 error。"""
    result = run_hbase(
        operation="create", table_name="orders",
        column_families="cf",
    )
    assert result.get("error") is True


def test_create_missing_column_families():
    """create 缺少 column_families 返回 error。"""
    result = run_hbase(operation="create", table_name="new_t")
    assert result.get("error") is True


def test_desc_table():
    """desc 返回表结构。"""
    result = run_hbase(operation="desc", table_name="orders")
    assert result["operation"] == "desc"
    assert result["table"] == "orders"
    assert "cf" in result["column_families"]
    assert result["row_count"] == 30
    assert result["disabled"] is False


def test_desc_nonexistent_table():
    """desc 不存在的表返回 error。"""
    result = run_hbase(operation="desc", table_name="no_such_table")
    assert result.get("error") is True


def test_disable_table():
    """disable 禁用表。"""
    result = run_hbase(operation="disable", table_name="orders")
    assert result["operation"] == "disable"
    desc = run_hbase(operation="desc", table_name="orders")
    assert desc["disabled"] is True


def test_enable_table():
    """enable 启用已禁用的表。"""
    run_hbase(operation="disable", table_name="orders")
    result = run_hbase(operation="enable", table_name="orders")
    assert result["operation"] == "enable"
    desc = run_hbase(operation="desc", table_name="orders")
    assert desc["disabled"] is False


def test_drop_table():
    """drop 在 graph 上下文外被 HITL 拦截（而非执行删表）。"""
    run_hbase(
        operation="create", table_name="to_drop",
        column_families="cf",
    )
    run_hbase(operation="disable", table_name="to_drop")
    result = run_hbase(operation="drop", table_name="to_drop")
    assert result.get("error") is True
    assert "审批" in result.get("message", "")
    # 表未被删除（被 HITL 拦截）
    assert "to_drop" in _HBASE_STORE


def test_drop_without_disable():
    """drop 未禁用的表返回 error。"""
    result = run_hbase(operation="drop", table_name="orders")
    assert result.get("error") is True


def test_truncate_table():
    """truncate 在 graph 上下文外被 HITL 拦截。"""
    run_hbase(
        operation="create", table_name="to_trunc",
        column_families="cf",
    )
    run_hbase(operation="disable", table_name="to_trunc")
    result = run_hbase(operation="truncate", table_name="to_trunc")
    assert result.get("error") is True
    assert "审批" in result.get("message", "")


def test_truncate_without_disable():
    """truncate 未禁用的表返回 error。"""
    result = run_hbase(operation="truncate", table_name="orders")
    assert result.get("error") is True


# ═══════════════════════════════════════════════════════════════════════════════
# filter parsing
# ═══════════════════════════════════════════════════════════════════════════════

def test_filter_eq():
    fn = _parse_simple_filter("cf:status = 'completed'")
    assert fn is not None
    assert fn({"cf:status": "completed"}) is True
    assert fn({"cf:status": "pending"}) is False


def test_filter_neq():
    fn = _parse_simple_filter("cf:status != 'cancelled'")
    assert fn({"cf:status": "completed"}) is True
    assert fn({"cf:status": "cancelled"}) is False


def test_filter_gt():
    fn = _parse_simple_filter("cf:total > 1000")
    assert fn({"cf:total": "5000"}) is True
    assert fn({"cf:total": "500"}) is False


def test_filter_missing_column():
    fn = _parse_simple_filter("cf:nonexist = 'x'")
    assert fn({"cf:other": "y"}) is False


def test_apply_prefix_filter():
    rows = [("order_001", {}), ("order_002", {}), ("user_001", {})]
    filtered = _apply_prefix_filter(rows, "order_")
    assert len(filtered) == 2
