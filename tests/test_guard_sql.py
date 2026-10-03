"""guard_sql 单测 — 直接验证 SQL 安全过滤器的拦截能力。

为什么需要这个文件：
- guard_sql 是系统最后一道安全闸（写操作 / 多语句 / 系统表）。
- 之前只有全链路用例（output-003 / edge-005）间接测「AI 会不会拒绝」，
  但「过滤器本身拦不拦得住」没有自动化保障——像买了灭火器从不检查。
- 这里是纯函数测试：零 token、零 API，每次 commit 都能跑。

用法:
    uv run pytest tests/test_guard_sql.py -v
"""

import pytest

from harness.constraints.guardrails import guard_sql

# ── 必须拦截的危险 SQL ──

@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders; DROP TABLE orders",      # 多语句注入
    "SELECT * FROM orders;\nDELETE FROM orders",    # 多语句（换行分隔）
    "DROP TABLE orders",
    "DROP DATABASE demo",
    "DELETE FROM orders",
    "INSERT INTO orders VALUES (1, 2)",
    "UPDATE orders SET total = 0",
    "ALTER TABLE orders ADD COLUMN x TEXT",
    "TRUNCATE TABLE orders",
    "CREATE TABLE hack (id INT)",
    "SELECT * FROM sqlite_master",                  # 系统表枚举
    "SELECT * FROM pg_catalog.pg_tables",
])
def test_guard_sql_blocks_dangerous(sql: str):
    passed, reason = guard_sql(sql)
    assert not passed, f"应拦截: {sql}"
    assert reason, "拦截必须有原因"


# ── 必须放行的正常 SQL ──

@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders",
    "select name, total from orders where status = 'completed'",
    "SELECT d.name, SUM(o.total) FROM orders o JOIN departments d ON o.dept_id = d.id GROUP BY d.name",
    "SELECT COUNT(*) FROM employees",
    "SELECT 1; -- 只读注释不算多语句",
    "SELECT * FROM orders /* 单条 SELECT 里的注释块是安全的 */",
    "SELECT dropped_count FROM orders",  # 词边界：dropped ≠ DROP
    # 假阳性回归：危险词只出现在字符串字面量 / 引号标识符 / 注释里，不应拦截
    "SELECT * FROM orders WHERE status = 'update'",
    "SELECT * FROM orders WHERE note LIKE '%delete%'",
    "SELECT * FROM orders WHERE reason = 'drop table'",
    'SELECT "update" FROM orders',                   # 双引号标识符
    "SELECT * FROM orders; /* DROP TABLE orders */",  # 分号后仅注释，非第二条语句
])
def test_guard_sql_allows_safe(sql: str):
    passed, reason = guard_sql(sql)
    assert passed, f"不应拦截: {sql} → {reason}"


# ── 边界情况 ──

def test_empty_sql_blocked():
    passed, _ = guard_sql("")
    assert not passed


def test_semicolon_with_comment_allowed():
    # 分号后跟 -- 注释：SQLite 里是单语句 + 注释，不算多语句
    passed, reason = guard_sql("SELECT 1; -- trailing comment")
    assert passed, reason


def test_uppercase_handling():
    # 大小写不敏感
    passed, _ = guard_sql("drop table orders")
    assert not passed
    passed, _ = guard_sql("SELECT * FROM orders")
    assert passed
