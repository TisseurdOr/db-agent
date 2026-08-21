"""动态上下文工程测试 —— 检索式 few-shot + 值级索引。零 API 成本。

- 种子样例逐条在真实 SQLite 上执行（不调 LLM，不调 embedding）
- few-shot 检索不可用时必须降级为空串，不能挡主流程
- 值级索引只对低基数 TEXT 列生效
"""

import sqlite3

import harness.context.sql_examples as sql_examples
from harness.context.sql_examples import SEED_EXAMPLES, format_examples, get_sql_fewshot
from harness.context.schema_discovery import profile_column_values, _VALUE_PROFILE_MAX
from db.seed import DB_PATH


# ═══ 1. 种子样例质量 ═══

def test_seed_examples_are_valid_select():
    """种子样例必须全是 SELECT——样例库里混进写操作会教坏模型。"""
    assert len(SEED_EXAMPLES) >= 5
    for ex in SEED_EXAMPLES:
        assert ex["question"].strip()
        assert ex["sql"].strip().upper().startswith("SELECT")


def test_seed_examples_execute_on_real_db():
    """每条种子 SQL 必须能在真实库上跑通且有结果——字段名错的样例比没有更有害。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        for ex in SEED_EXAMPLES:
            rows = conn.execute(ex["sql"]).fetchall()
            assert rows, f"种子样例查询结果为空: {ex['question']}"
    finally:
        conn.close()


# ═══ 2. few-shot 格式化与降级 ═══

def test_format_examples():
    assert format_examples([]) == ""
    text = format_examples([{"question": "各部门订单额", "sql": "SELECT 1"}])
    assert "Q: 各部门订单额" in text
    assert "SQL: SELECT 1" in text
    assert "已验证" in text  # 提示模型这些样例可信任


def test_fewshot_degrades_without_embedding_key(monkeypatch):
    """没配 embedding key 时返回空串，绝不抛异常——few-shot 是增强不是依赖。"""
    monkeypatch.setattr(sql_examples, "_store", None)  # 重置单例
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    assert get_sql_fewshot("各部门的订单总金额") == ""


# ═══ 3. 值级索引 ═══

def test_profile_low_cardinality_text_column():
    """orders.status 是低基数 TEXT 列 → 返回真实取值。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        values = profile_column_values(conn, "orders", "status", "TEXT")
        assert values, "orders.status 应命中值级索引"
        assert len(values) <= _VALUE_PROFILE_MAX
        assert "completed" in values
    finally:
        conn.close()


def test_profile_skips_non_text_and_high_cardinality():
    conn = sqlite3.connect(DB_PATH)
    try:
        # 数值列不做值索引
        assert profile_column_values(conn, "orders", "total", "REAL") == []
        # 高基数 TEXT 列（created_at 几十个不同时间戳）不做值索引
        assert profile_column_values(conn, "orders", "created_at", "TEXT") == []
        # 不存在的表/列：静默返回空，不抛异常
        assert profile_column_values(conn, "no_such_table", "x", "TEXT") == []
    finally:
        conn.close()
