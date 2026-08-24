"""SQL 置信度评分 —— 生成 SQL 后让 LLM 自评质量，低分触发 HITL 复核。

借鉴 OpenChatBI 的 confidence_gate 思路：
1. LLM 用 6 项检查标准为生成的 SQL 打分
2. 低于阈值 → 暂停并请用户确认
3. 高于阈值 → 直接执行

设计动机：
"SQL 生成后不是直接执行，而是先过置信度门——LLM 自评 6 项检查，
低于阈值自动暂停让用户确认。借鉴 OpenChatBI 的 confidence gate
思路，但融合进自己的 HITL 审批框架。"

6 项检查标准（来自 OpenChatBI）：
1. 表名是否正确（是否在 Schema 白名单中）
2. 列名是否正确（是否在表的 describe 结果中）
3. JOIN 条件是否合理（是否用了正确的关联字段）
4. WHERE 条件是否语义匹配用户问题
5. 聚合函数是否恰当（COUNT/SUM/AVG/MAX/MIN）
6. 是否有潜在的性能问题（缺少 LIMIT、无索引列过滤等）
"""

import json
import re

CONFIDENCE_PROMPT = """你是 SQL 代码审查专家。审查以下生成的 SQL，按 6 项标准打分（每项 0-1 分）。

【审查上下文】
- 用户问题: {query}
- 相关表结构: {schema_context}
- 生成的 SQL: {sql}

【6 项评分标准】
1. table_correct: 表名是否都在提供的表结构中？（0=有不在的表, 1=全在）
2. column_correct: 列名是否都在对应表的字段中？（0=有不存在的列, 1=全在）
3. join_valid: JOIN 条件是否使用了正确的关联字段？（0=明显错误的 JOIN, 1=正确）
4. semantic_match: WHERE/HAVING 条件是否语义匹配用户问题？（0=不匹配, 1=匹配）
5. aggregation_correct: 聚合函数（COUNT/SUM/AVG 等）使用是否正确？（0=用错, 1=正确）
6. safe_execution: 是否有 LIMIT、是否有潜在的全表扫描风险？（0=无 LIMIT 且无索引过滤, 1=安全）

输出格式（只输出 JSON）:
{{"scores": {{"table_correct": 1, "column_correct": 0.8, "join_valid": 1, "semantic_match": 0.9, "aggregation_correct": 1, "safe_execution": 0}}, "confidence": 0.78, "explanation": "safe_execution 扣分：没有 LIMIT 子句"}}

confidence = 六项平均分。只输出 JSON，不要其他文字。"""


def parse_confidence_result(text: str) -> dict:
    """解析 LLM 返回的置信度 JSON。失败时返回保守默认值。"""
    try:
        # 提取 JSON 块
        match = re.search(r'\{[\s\S]*\}', text)
        if not match:
            return {"confidence": 0.5, "explanation": "无法解析置信度评分", "scores": {}}
        data = json.loads(match.group())
        return {
            "confidence": data.get("confidence", 0.5),
            "explanation": data.get("explanation", ""),
            "scores": data.get("scores", {}),
        }
    except (json.JSONDecodeError, KeyError):
        return {"confidence": 0.5, "explanation": "置信度解析失败，默认中等", "scores": {}}


def should_pause(confidence: float, threshold: float = 0.7) -> bool:
    """置信度低于阈值时暂停，触发 HITL 审批。"""
    return confidence < threshold


def format_confidence_report(result: dict) -> str:
    """生成给人看的置信度报告。"""
    if not result.get("scores"):
        return "置信度评分不可用"

    labels = {
        "table_correct": "表名正确性",
        "column_correct": "列名正确性",
        "join_valid": "JOIN 合理性",
        "semantic_match": "语义匹配度",
        "aggregation_correct": "聚合正确性",
        "safe_execution": "执行安全性",
    }

    lines = ["SQL 置信度评估:"]
    for key, label in labels.items():
        score = result["scores"].get(key, "?")
        bar = "█" * int(float(score) * 10) if isinstance(score, (int, float)) else "?"
        lines.append(f"  {label}: {bar} {score}")
    lines.append(f"\n综合置信度: {result['confidence']:.2f}")
    if result.get("explanation"):
        lines.append(f"说明: {result['explanation']}")

    return "\n".join(lines)
