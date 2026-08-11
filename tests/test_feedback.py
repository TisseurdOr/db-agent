"""自学习回流测试 —— 质量门 + 提取 + 写路径。零 API 成本。"""

import pytest

from rag.feedback import extract_sql, should_learn, learn_from_success, learn_from_hitl, sql_executes
import rag.feedback as feedback
import rag.sql_examples as sql_examples


GOOD_RESULT = """
查询成功。订单总金额如下：

```sql
SELECT d.name AS 部门, SUM(o.total) AS 订单总金额
FROM orders o JOIN departments d ON o.dept_id = d.id
GROUP BY d.name ORDER BY 订单总金额 DESC
```

财务部最高。
"""

HEALED_RESULT = """
首次执行失败: no such column: total_amount
重写后执行成功:
SELECT SUM(total) FROM orders
结果: 13490700
"""


def test_extract_sql_takes_last_select():
    """自愈场景：取最后一条成功 SQL，不是中间失败的。"""
    sql = extract_sql(HEALED_RESULT)
    assert sql is not None
    assert "total_amount" not in sql
    assert "SUM(TOTAL)" in sql.upper().replace(" ", "")


def test_extract_sql_rejects_garbage():
    assert extract_sql("") is None
    assert extract_sql("(Agent 在 8 轮内未完成)") is None
    assert extract_sql("SELECT 1") is None  # 太短


def test_should_learn_rejects_errors_and_timeout():
    q = "各部门订单额"
    assert not should_learn(q, "(Agent 在 8 轮内未完成)")
    assert not should_learn(q, '{"error": "no such column: x", "retryable": true}')
    assert not should_learn(q, "用户拒绝了该查询")
    assert not should_learn("", GOOD_RESULT)


def test_should_learn_accepts_healed_result():
    """自愈文本里有 no such column，但最终 SQL 跑通 → 仍应回流。"""
    assert should_learn("订单总金额", HEALED_RESULT)


def test_should_learn_accepts_valid_success():
    assert should_learn("各部门的订单总金额是多少", GOOD_RESULT)


def test_should_learn_respects_env_kill_switch(monkeypatch):
    monkeypatch.setenv("AUTO_LEARN_SQL", "0")
    assert not should_learn("各部门的订单总金额是多少", GOOD_RESULT)


def test_sql_executes_validates_against_real_db():
    assert sql_executes("SELECT COUNT(*) FROM orders")
    assert not sql_executes("SELECT no_such_col FROM orders")
    assert not sql_executes("DROP TABLE orders")  # 语法上不是 SELECT 执行也会失败；SELECT 校验在上层


def test_learn_from_success_calls_record(monkeypatch):
    calls = []
    monkeypatch.setattr(feedback, "record_sql_example", lambda q, s, source="user": calls.append((q, s, source)) or True)
    assert learn_from_success("各部门的订单总金额是多少", GOOD_RESULT, source="auto")
    assert len(calls) == 1
    assert calls[0][2] == "auto"
    assert "SUM" in calls[0][1].upper()


def test_learn_from_success_skips_bad(monkeypatch):
    calls = []
    monkeypatch.setattr(feedback, "record_sql_example", lambda *a, **k: calls.append(1) or True)
    assert not learn_from_success("x", "(Agent 在 8 轮内未完成)")
    assert calls == []


def test_learn_from_hitl(monkeypatch):
    calls = []
    monkeypatch.setattr(feedback, "record_sql_example", lambda q, s, source="user": calls.append((q, s, source)) or True)
    sql = "SELECT name, salary FROM employees LIMIT 5"
    assert learn_from_hitl("查一下员工工资", sql)
    assert calls[0][2] == "hitl"


def test_learn_from_hitl_rejects_non_select(monkeypatch):
    calls = []
    monkeypatch.setattr(feedback, "record_sql_example", lambda *a, **k: calls.append(1) or True)
    assert not learn_from_hitl("删数据", "DELETE FROM employees")
    assert calls == []


def test_record_sql_example_degrades_without_key(monkeypatch):
    """写路径也必须降级——没 embedding key 时返回 False，不抛异常。"""
    monkeypatch.setattr(sql_examples, "_store", None)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    assert sql_examples.record_sql_example("q", "SELECT 1") is False


def test_run_query_captures_successful_sql_for_learning():
    """工具层捕获成功 SQL——Agent 回答不带 SELECT 时回流仍能拿到真 SQL。"""
    from tools.query import run_query, pop_last_successful_sql

    pop_last_successful_sql()  # 清空
    run_query("SELECT COUNT(*) AS n FROM orders", user_id="dba")
    sql = pop_last_successful_sql()
    assert sql is not None
    assert "COUNT" in sql.upper()
    assert pop_last_successful_sql() is None  # 取出即清空


def test_run_query_error_does_not_capture_sql():
    from tools.query import run_query, pop_last_successful_sql

    pop_last_successful_sql()
    run_query("SELECT no_such_col FROM orders", user_id="dba")
    assert pop_last_successful_sql() is None
