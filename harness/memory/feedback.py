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
#   7. 语义门（可选，配 KIMI_API_KEY 生效）：跨模型 judge 验"SQL 是否真的回答了问题"

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
    """在真实库上 dry-run：只允许 SELECT/WITH，能执行且返回非空结果才算可回流。

    空结果也算失败——枚举值猜错（如 status='active' 而真实是 'completed'）会
    跑通但返回 0 行，这类 SQL 不该作为 few-shot 参照回流。
    """
    cleaned = sql.strip().upper()
    if not cleaned.startswith(("SELECT", "WITH")):
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(sql).fetchmany(1)
            return bool(row)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


# 语义门：用独立模型（Kimi）验"SQL 是否真的回答了问题"。形式合法 ≠ 语义正确，
# 问"销售额"却算了"数量"这类答非所问，正则/执行校验都发现不了。
_SEMANTIC_JUDGE_PROMPT = """你是 SQL 语义审查员。判断这条 SQL 是否真正回答了用户的问题。

注意：不是判断语法对不对（语法已经验证过了），而是判断语义对不对——查的表、过滤条件、聚合方式、分组维度，是否符合问题的意图。

常见错误：
- 问"销售额"却算了"数量"
- 问"各产品销量"却按月份分组
- 问 A 地区却查了 B 地区

只输出 JSON，不要任何其他文字：
{"correct": true/false, "reason": "一句话说明"}
"""


def _parse_semantic_verdict(text: str) -> bool | None:
    """解析 judge 返回的 JSON，取 correct 字段。解析失败返回 None（降级放行）。"""
    m = re.search(r'"correct"\s*:\s*(true|false)', text, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower() == "true"


def semantic_verify(question: str, sql: str) -> bool:
    """语义门（可选）：判断 SQL 是否真的回答了问题。

    优先级：
    1. **Jev**（若配置）——noul 返回**校准概率**，比"让生成模型给个是/否"更稳且更省；
    2. Kimi 跨模型 judge（原路径，规避同源偏袒，六维线 5.3）；
    3. 都没配 / 失败 → 返回 True（跳过语义门，退回形式-only 质量门）。
    """
    # 1) 优先 Jev：用校准概率判断"这条 SQL 是否回答了用户问题"
    try:
        from harness.jev_client import decide_sync, extract_probability
        from harness.jev_client import is_enabled as jev_enabled

        if jev_enabled():
            jr = decide_sync(
                f"用户问题：{question}\n生成的 SQL：\n{sql}",
                [{
                    "id": "answers_question",
                    "type": "boolean",
                    "prompt": "这条 SQL 是否真的回答了用户的问题？（答非所问=否）",
                }],
            )
            prob = extract_probability(jr, "answers_question")
            if prob is not None:
                return prob >= 0.5
    except Exception:
        pass  # Jev 失败 → 继续走 Kimi

    api_key = os.getenv("KIMI_API_KEY", "")
    if not api_key:
        return True
    try:
        from anthropic import Anthropic

        from harness.observation.llm import extract_text

        client = Anthropic(
            api_key=api_key,
            base_url=os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/anthropic"),
        )
        resp = client.messages.create(
            model=os.getenv("KIMI_MODEL", "kimi-k2.5"),
            max_tokens=512,
            system=_SEMANTIC_JUDGE_PROMPT,
            messages=[{"role": "user", "content": f"用户问题: {question}\nSQL: {sql}"}],
        )
        text = extract_text(resp, context="semantic_gate") or ""
        verdict = _parse_semantic_verdict(text)
        if verdict is False:
            return False
        return True  # 明确正确，或解析失败 → 降级放行
    except Exception:
        return True


def should_learn(
    question: str, result_text: str, sql: str | None = None, *, force: bool = False
) -> bool:
    """质量门：全部通过才允许写回样例库。

    自愈场景会在文本里留下 'no such column' 等字样——只要最终抽出的
    SQL 能在真实库上跑通，就允许回流；纯错误 JSON / 超时一律拒绝。

    force=True 跳过 AUTO_LEARN_SQL 环境门——评测学习阶段在事实断言
    （expected）通过后显式写入，此时正确性已由外部验证，无需再靠
    "能执行"这种事后信号。其余质量门（SELECT-only / 敏感列 / 真实库跑通）始终生效。
    """
    if not force and os.getenv("AUTO_LEARN_SQL", "1") in ("0", "false", "False"):
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
    if not sql_executes(sql):
        return False
    # 语义门（可选）：force=True（评测学习，正确性已由 expected 事实断言验证）
    # 跳过这刀；普通自动回流用独立模型 Kimi 验"SQL 是否真的回答了问题"。
    if not force and not semantic_verify(question.strip(), sql):
        print(f"   🚫 自学习: 语义门拒绝（SQL 未正确回答问题）: {question[:40]}…")
        return False
    return True


def learn_from_success(
    question: str,
    result_text: str = "",
    sql: str | None = None,
    source: str = "auto",
    *,
    force: bool = False,
) -> bool:
    """成功路径回流。返回是否写入样例库。

    source 约定：
      auto  — SQL Agent 正常跑通后自动回流
      hitl  — 用户批准敏感查询后回流（人工背书，权重更高的语义）
      user  — 用户显式纠正/确认（预留给 CLI）

    force=True 供评测学习阶段使用：该样例的正确性已由 expected 事实断言
    验证过，跳过 AUTO_LEARN_SQL 环境门直接写入（质量门仍生效）。
    """
    sql = sql or extract_sql(result_text)
    if not sql or not should_learn(question, result_text, sql, force=force):
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
