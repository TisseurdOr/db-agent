"""Guardrails — 三层安全护栏。

每层返回 (passed: bool, message: str):
- passed=True  → 放行，message 是空串
- passed=False → 拦截，message 是拦截原因（直接返回给用户）

设计原则：
1. 纯规则匹配，不调 LLM——零延迟、零 token 成本
2. 误拦可以调（regex 比 LLM 容易调参），漏拦比误拦更危险
3. 三层独立，互不依赖——关掉任何一层不影响其他
"""

import re
from typing import Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# Layer 1: 输入护栏 — 在 LLM 调用之前拦住恶意输入
# ═══════════════════════════════════════════════════════════════════════════════

# prompt injection 的常见特征模式
# 攻击者会尝试覆盖 system prompt，让 Agent 做不该做的事
INJECTION_PATTERNS = [
    # 直接指令覆盖
    r"ignore\s+(your\s+)?(previous|above|all|system)\s+(instructions?|prompts?|rules?)",
    r"you\s+are\s+now\s+(a\s+)?",
    r"forget\s+(everything|all|your)\s+(you\s+)?(know|learned)",
    r"new\s+system\s+prompt",
    # 角色扮演攻击
    r"pretend\s+(you\s+are|to\s+be)",
    r"act\s+as\s+(if\s+you\s+are|a\s+different)",
    r"DAN\s+mode|developer\s+mode|god\s+mode",
    # 输出操纵
    r"you\s+must\s+(say|output|reply|respond|answer)\s+(only|exactly)",
    r"do\s+not\s+(say|mention|tell|reveal)",
    # 越狱通用模式
    r"jailbreak|bypass|override\s+(the\s+)?(system|safety|rules?)",
]

# 编译一次，复用。不区分大小写。
_INJECTION_RES = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# 输入最大长度：超过这个长度大概率不是正常的数据分析问题
MAX_INPUT_LENGTH = 2000


def guard_input(query: str) -> Tuple[bool, str]:
    """输入护栏：检查 query 是否安全。

    检查项（按优先级）：
    1. 空输入
    2. 超长输入（>2000 字符）
    3. prompt injection 特征

    Returns:
        (True, "")  → 放行
        (False, "拦截原因") → 拦截，原因直接返回给用户
    """
    # 1. 空输入
    if not query or not query.strip():
        return False, "输入为空，请输入你的问题。"

    # 2. 超长输入——可能是把整篇文档塞进来做注入
    if len(query) > MAX_INPUT_LENGTH:
        return False, (
            f"输入过长（{len(query)} 字符，上限 {MAX_INPUT_LENGTH}）。"
            "请精简你的问题后重试。"
        )

    # 3. prompt injection 特征匹配
    for i, pattern in enumerate(_INJECTION_RES):
        if pattern.search(query):
            return False, "输入包含不安全的指令模式，已被拦截。"

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 2: SQL 护栏 — 阻止危险数据库操作
# ═══════════════════════════════════════════════════════════════════════════════

# 危险的 SQL 关键字——即使在 SELECT 子查询里出现也要拦截
DANGEROUS_SQL_KEYWORDS = [
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER",
    "TRUNCATE", "CREATE", "REPLACE",
]

# 危险的多语句分隔符
MULTI_STATEMENT_MARKERS = [";--", ";\n", "/*"]

# 系统表前缀——防止枚举数据库结构
SYSTEM_TABLE_PREFIXES = ["sqlite_", "pg_", "information_schema", "sys."]


def guard_sql(sql: str) -> Tuple[bool, str]:
    """SQL 护栏：检查 SQL 语句是否安全。

    检查项：
    1. 必须以 SELECT 开头（run_query 里已经做了，这里再兜底）
    2. 不能包含写操作关键字（DROP / DELETE / INSERT / UPDATE / ALTER 等）
    3. 不能是多语句（分号后跟第二条语句）
    4. 不能查系统表

    run_query() 已经做了第 1 项，这个函数做更全面的检查。
    在 run_query 执行前调用，双重保险。

    Returns:
        (True, "")  → 安全
        (False, "拦截原因") → 危险
    """
    sql_upper = sql.strip().upper()

    # 1. 必须以 SELECT 开头
    if not sql_upper.startswith("SELECT"):
        return False, "只允许 SELECT 查询。"

    # 2. 写操作关键字——用词边界匹配，避免误拦（如 DROPPED 不会命中）
    for keyword in DANGEROUS_SQL_KEYWORDS:
        if re.search(rf"\b{keyword}\b", sql_upper):
            return False, f"SQL 包含危险操作 {keyword}，已被拦截。只允许只读查询。"

    # 3. 多语句检测——分号后跟非空白字符
    semicolons = [i for i, c in enumerate(sql) if c == ";"]
    for pos in semicolons:
        remaining = sql[pos + 1:].strip()
        if remaining and not remaining.startswith("--"):
            return False, "不允许执行多条 SQL 语句。"

    # 4. 系统表检测
    for prefix in SYSTEM_TABLE_PREFIXES:
        if prefix in sql.lower():
            return False, f"不允许查询系统表（{prefix}...）。"

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 3: 输出护栏 — 防止敏感信息泄露
# ═══════════════════════════════════════════════════════════════════════════════

# 常见 PII 模式
PII_PATTERNS = [
    # 手机号（中国大陆）
    (re.compile(r"1[3-9]\d{9}"), "手机号"),
    # 身份证号（18位）
    (re.compile(r"\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]"), "身份证号"),
    # 邮箱
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "邮箱地址"),
    # 银行卡号（16-19位数字）
    (re.compile(r"\b\d{16,19}\b"), "疑似银行卡号"),
]

# 输出最大长度——防止异常输出撑爆终端
MAX_OUTPUT_LENGTH = 8000

# 系统提示泄露检测——防止 Agent 把自己的 system prompt 吐出来
SYSTEM_LEAK_PATTERNS = [
    r"system\s*prompt",
    r"你是一个.{0,20}(Agent|助手|机器人)",
    r"your\s+(instructions?|system\s+prompt)",
]


def guard_output(text: str) -> Tuple[bool, str]:
    """输出护栏：检查 Agent 输出是否安全。

    检查项：
    1. 空输出
    2. 超长输出
    3. PII 泄露（手机号/身份证/邮箱/银行卡）
    4. system prompt 泄露

    PII 检测不拦截——只标记警告。因为业务数据可能合法包含这些格式。
    system prompt 泄露检测——直接拦截，这是明确的越狱信号。

    Returns:
        (True, "")  → 安全
        (False, "拦截原因") → 不安全
    """
    # 1. 空输出
    if not text or not text.strip():
        return False, "Agent 未产生有效输出。"

    # 2. 超长输出
    if len(text) > MAX_OUTPUT_LENGTH:
        return False, (
            f"输出过长（{len(text)} 字符），已被截断。请缩小查询范围后重试。"
        )

    # 3. 检查 system prompt 泄露——这是明确的安全事故
    for pattern in SYSTEM_LEAK_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False, "输出包含敏感系统信息，已被拦截。"

    # 4. PII 检测——只警告，不拦截
    #    业务数据中合法出现 PII（如订单里的客户手机号）不应该被拦，
    #    但开发阶段打印警告提醒开发者注意。
    found_pii = []
    for pattern, label in PII_PATTERNS:
        if pattern.search(text):
            found_pii.append(label)
    if found_pii:
        # 用 print 打印警告——不阻断输出，只提醒
        print(f"  ⚠️ 输出中包含疑似敏感信息: {', '.join(found_pii)}")

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════════
# 便捷函数：一次跑三道护栏
# ═══════════════════════════════════════════════════════════════════════════════

def full_guard(query: str, sql: str = "", output: str = "") -> Tuple[bool, str, str]:
    """跑三道护栏，返回 (是否全过, 拦截原因, 拦截位置)。

    用于快速集成——调用方只需判断第一个返回值。

    Args:
        query: 用户输入
        sql: Agent 生成的 SQL（可选）
        output: Agent 生成的输出（可选）

    Returns:
        (passed, reason, guard_name)
        guard_name 是 "input" / "sql" / "output"，表示在哪一层被拦
    """
    passed, reason = guard_input(query)
    if not passed:
        return False, reason, "input"

    if sql:
        passed, reason = guard_sql(sql)
        if not passed:
            return False, reason, "sql"

    if output:
        passed, reason = guard_output(output)
        if not passed:
            return False, reason, "output"

    return True, "", ""
