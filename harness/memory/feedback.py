# rag/feedback.py — 自学习回流：成功 SQL → 样例库
#
# 升级路线第 3 项：系统从"只出不进"变成闭环。
#   读路径：get_sql_fewshot（第 2 项已落地）
#   写路径：本模块——从成功执行 / HITL 批准中提取 SQL，写回样例库
#
# 质量门（宁缺毋滥——脏样例比没有更有害）：
#   1. 必须是 SELECT / WITH（写操作永不入库）
#   2. Agent 结果不能是超时垃圾文本
#   3. 文本里不能带 SQL 执行错误标记
#   4. 提取出的 SQL 必须能在真实库上跑通（可选硬校验）
#   5. 环境变量 AUTO_LEARN_SQL=0 可关掉自动回流
#   6. 涉及敏感列（薪资/手机/证件/账户等）的 SQL 不自动回流（审计）

from __future__ import annotations

import os
import re
import sqlite3

from db.seed import DB_PATH
from harness.context.sql_examples import record_sql_example
from harness.orchestration.multi.base import is_agent_timeout

# 优先从 markdown 代码块取；其次取 SELECT…; 到分号为止
_CODE_BLOCK_RE = re.compile(r"```(?:sql)?\s*((?:WITH|SELECT)\b[\s\S]+?)```", re.IGNORECASE)
_SQL_STMT_RE = re.compile(r"(?is)\b((?:WITH|SELECT)\b[\s\S]+?;)")
# 无分号时：取到空行或明显的中文叙述开头
_SQL_LOOSE_RE = re.compile(
    r"(?is)\b((?:WITH|SELECT)\b.+?)(?=\n\s*\n|\n结果|\n查询|\n首次|\n重写|\n>|\n#|\Z)"
)


# 敏感列审计——命中即跳过回流，防止敏感数据进 few-shot 库
_SENSITIVE_PATTERNS = [
    (r"(?i)\bsalary\b", "薪资"),
    (r"(?i)\bphone\b|\bmobile\b", "手机号"),
    (r"(?i)\b(id_card|idno|identity|passport|证件)\b", "证件号"),
    (r"(?i)\b(bank|account_no|card_no|卡号)\b", "银行账户"),
    (r"(?i)\b(password|pwd|secret)\b", "密码/密钥"),
    (r"(?i)\bemail\b", "邮箱"),
    (r"(?i)\b(address|住址)\b", "住址"),
]


def is_sensitive_sql(sql: str) -> tuple[bool, str]:
    """检查 SQL 是否涉及敏感列。返回 (是否敏感, 命中的敏感项)。"""
    for pattern, label in _SENSITIVE_PATTERNS:
        if re.search(pattern, sql):
            return True, label
    return False, ""


def extract_sql(text: str) -> str | None:
    """从 Agent 输出文本中提取最后一条 SELECT/WITH。提取不到返回 None。"""
    if not text:
        return None

    candidates: list[str] = []
    candidates.extend(_CODE_BLOCK_RE.findall(text))
    candidates.extend(_SQL_STMT_RE.findall(text))
    if not candidates:
        candidates.extend(_SQL_LOOSE_RE.findall(text))
    if not candidates:
        return None

    sql = candidates[-1].strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql)
    sql = re.sub(r"\s*```$", "", sql)
    # 去掉尾部中文/说明行（自愈叙述常粘在 SQL 后面）
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        # 纯中文叙述行（无 SQL 关键字）截断
        if lines and re.match(r"^[\u4e00-\u9fff]", stripped) and not re.search(
            r"\b(SELECT|FROM|WHERE|JOIN|GROUP|ORDER|LIMIT|AND|OR|AS)\b", stripped, re.I
        ):
            break
        lines.append(line)
    sql = "\n".join(lines).strip().rstrip(";")
    if not sql.upper().lstrip().startswith(("SELECT", "WITH")):
        return None
    if len(sql) < 20:
        return None
    return sql


def sql_executes(sql: str, db_path: str = DB_PATH) -> bool:
    """在真实库上 dry-run：只允许 SELECT/WITH，能执行且不抛错才算可回流。"""
    cleaned = sql.strip().upper()
    if not cleaned.startswith(("SELECT", "WITH")):
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(sql).fetchmany(1)
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def should_learn(question: str, result_text: str, sql: str | None = None) -> bool:
    """质量门：全部通过才允许写回样例库。

    自愈场景会在文本里留下 'no such column' 等字样——只要最终抽出的
    SQL 能在真实库上跑通，就允许回流；纯错误 JSON / 超时一律拒绝。
    """
    if os.getenv("AUTO_LEARN_SQL", "1") in ("0", "false", "False"):
        return False
    if not question or not question.strip():
        return False
    if is_agent_timeout(result_text or ""):
        return False
    stripped = (result_text or "").strip()
    # 纯工具错误载荷（没有自然语言成功叙述）
    if stripped.startswith("{") and '"error"' in stripped.lower():
        return False
    if "用户拒绝了该查询" in stripped or "需要管理员审批" in stripped:
        return False
    sql = sql or extract_sql(result_text or "")
    if not sql:
        return False
    # 审计：敏感列 SQL 不入样例库
    sensitive, label = is_sensitive_sql(sql)
    if sensitive:
        print(f"   🚫 自学习: 跳过敏感 SQL 回流（{label}）")
        return False
    return sql_executes(sql)


def learn_from_success(
    question: str,
    result_text: str = "",
    sql: str | None = None,
    source: str = "auto",
) -> bool:
    """成功路径回流。返回是否写入样例库。

    source 约定：
      auto  — SQL Agent 正常跑通后自动回流
      hitl  — 用户批准敏感查询后回流（人工背书，权重更高的语义）
      user  — 用户显式纠正/确认（预留给 CLI）
    """
    sql = sql or extract_sql(result_text)
    if not should_learn(question, result_text, sql):
        return False
    ok = record_sql_example(question.strip(), sql, source=source)
    if ok:
        print(f"   📥 自学习: 回流样例 [{source}] {question[:40]}…")
    return ok


def learn_from_hitl(question: str, sql: str) -> bool:
    """HITL 批准专用入口——SQL 已知，跳过从文本提取。"""
    if not sql or not sql.strip().upper().startswith(("SELECT", "WITH")):
        return False
    # HITL 场景 result_text 用空串绕过错误标记检查，但仍做执行校验
    if os.getenv("AUTO_LEARN_SQL", "1") in ("0", "false", "False"):
        return False
    if not sql_executes(sql):
        return False
    sensitive, label = is_sensitive_sql(sql)
    if sensitive:
        print(f"   🚫 自学习: HITL 批准但含敏感列（{label}），不入样例库")
        return False
    ok = record_sql_example(question.strip(), sql.strip().rstrip(";"), source="hitl")
    if ok:
        print(f"   📥 自学习: HITL 批准回流 {question[:40]}…")
    return ok
