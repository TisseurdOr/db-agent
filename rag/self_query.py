# rag/self_query.py — Self-Query RAG（课程 0016 第 1 节）
#
# 课程原文: "基础 RAG 只有一个输入——向量。Self-Query 让 LLM 先把自然语言拆成
# 语义部分（走向量）和过滤条件（走元数据）。"
#
# 流程对照课程示例:
#   用户: "2025年北京办公室的销售数据"
#   基础 RAG: embed(整句) → 向量检索 → 可能返回 2024/上海的数据（无强制过滤）
#   Self-Query:
#     parse_self_query() → {semantic_query: "销售数据", filters: {year: "2025"}}
#     然后 semantic_query 走向量 + filters 走 Chroma metadata where → 精准命中
#
# 本文件实现了课程的三个步骤:
#   Step 1: LLM 拆解查询   → parse_self_query()
#   Step 2: 向量检索+元数据过滤 → self_query_retrieve() 前半段
#   Step 3: 结果太少时降级重试  → self_query_retrieve() 后半段
#
# 与项目的集成点:
#   - tools/knowledge.py 的 search_memory Tool 是入口
#   - VectorMemory.recall() 有 primitive 的 metadata 过滤（memory_type/user_id）
#   - Self-Query 的价值在于 LLM 自动抽取 year/memory_type，用户不需要手动指定

import json
import os
import re

from utils.llm import extract_text, logger

# ─── 领域限定 ──────────────────────────────────────────────────
# 课程没有这部分——因为课程例子是通用文档检索（year/office/doc_type）。
# 我们把过滤字段限定为 memory 系统实际写入的字段，和 VectorMemory.remember 对齐。
# 如果后续加新字段（如 customer_id），在这里加白名单即可。

_ALLOWED_FILTER_KEYS = frozenset({"memory_type", "year"})
_VALID_MEMORY_TYPES = frozenset({"conversation", "preference", "entity", "note"})


# ─── 课程 Step 1: LLM 拆解查询 ─────────────────────────────────
#
# 课程原文:
#   query_parts = llm.extract_filters("2025年北京办公室的销售数据")
#   → {
#       "semantic_query": "销售数据",
#       "filters": {"year": "2025", "office": "北京", "doc_type": "sales_report"}
#     }
#
# 我们的 Prompt 做了两处适配:
#   1. 过滤字段换成项目实际字段（memory_type, year）
#   2. 加了"没有明确线索就不要填"——避免 LLM 强行编造不存在的过滤条件

SELF_QUERY_PROMPT = """从用户查询中提取: (1) 用于语义检索的核心问题 (2) 用于精确过滤的条件。

支持的过滤字段（没有明确线索就不要填）:
- memory_type: conversation | preference | entity | note
- year: 四位年份字符串，如 "2026"

返回 JSON 格式:
{{
  "semantic_query": "只保留核心语义部分，去掉已知的过滤条件",
  "filters": {{"memory_type": "conversation", "year": "2026"}}
}}

用户查询: {query}
只返回 JSON，不要其他内容。"""


# ─── 工具函数 ──────────────────────────────────────────────────

def _strip_json_fence(text: str) -> str:
    """去掉 ```json ... ``` 包裹。

    课程没提这个——但实际 LLM 经常在 JSON 外加 markdown fence，
    直接 json.loads 会崩。这是工程实践 vs 课程示例的差距。
    """
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    return text


def _sanitize_filters(raw: dict) -> dict:
    """白名单校验过滤条件。

    为什么需要这个: LLM 输出不可控——它可能编造不存在的字段名
    （如 office、doc_type）或给 memory_type 填无效值。
    白名单确保只放行我们元数据里实际存在的字段。
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if key not in _ALLOWED_FILTER_KEYS or value is None or value == "":
            continue
        if key == "memory_type":
            v = str(value).strip().lower()
            if v in _VALID_MEMORY_TYPES:
                out["memory_type"] = v
        elif key == "year":
            out["year"] = str(value).strip()
    return out


def _build_where(filters: dict, user_id: str | None) -> dict | None:
    """合并业务 filters + user_id 为 Chroma where 条件。

    多条件用 $and 连接，单条件直接返回。
    Chroma 的 where 语法和 SQL WHERE 不同——这是 Chroma 特有的适配层。
    """
    clauses = []
    if user_id:
        clauses.append({"user_id": user_id})
    for k, v in (filters or {}).items():
        clauses.append({k: v})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


# ─── 课程核心: parse_self_query ─────────────────────────────────
#
# 对应课程 Step 1 的完整实现:
#   resp = llm_client.messages.create(...)
#   parts = json.loads(resp.content[0].text)
#   → {"semantic_query": "...", "filters": {...}}
#
# 课程之外加的工程保护:
#   1. extract_text() 替代 content[0].text — 兼容带思考模型
#   2. try/except 包裹 json.loads — 解析失败退化为普通 recall
#   3. _strip_json_fence + _sanitize_filters — 清洗 LLM 输出

def parse_self_query(query: str, llm_client) -> dict:
    """LLM 拆解查询 → {semantic_query, filters}。

    解析失败时整句当作 semantic_query，filters 为空——退化为普通向量检索。
    """
    fallback = {"semantic_query": query, "filters": {}}
    if not query or not query.strip():
        return fallback

    try:
        resp = llm_client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": SELF_QUERY_PROMPT.format(query=query),
            }],
        )
        text = extract_text(resp, context="self_query")
        if not text.strip():
            logger.warning("self_query: LLM 返回空，退回原始 query")
            return fallback

        parts = json.loads(_strip_json_fence(text))
        semantic = (parts.get("semantic_query") or query).strip() or query
        filters = _sanitize_filters(parts.get("filters") or {})
        logger.info(
            "self_query 拆解: query=%r → semantic=%r filters=%s",
            query, semantic, filters,
        )
        return {"semantic_query": semantic, "filters": filters}
    except (json.JSONDecodeError, TypeError, AttributeError, KeyError) as e:
        logger.warning("self_query 解析失败 (%s)，退回原始 query", e)
        return fallback


# ─── 课程核心: self_query_retrieve（组合检索入口）───────────────
#
# 对应课程三步完整流程:
#   Step 1: parts = parse_self_query(query)   → LLM 拆解
#   Step 2: results = vector_db.search(        → 语义向量 + 元数据硬过滤
#             query_vec,
#             top_k=5,
#             where=parts["filters"])
#   Step 3: if len(results) < 3:               → 降级重试
#             results = vector_db.search(query_vec, top_k=5)  # 不过滤
#
# 课程原文: "如果结果太少，放宽过滤重试"
# 我们的阈值设为 < 2（而非课程的 < 3），因为 memory 库体量较小。

async def self_query_retrieve(
    query: str,
    vector_memory,
    llm_client,
    top_k: int = 5,
    user_id: str = "default",
    memory_type: str = None,
) -> tuple[list[dict], dict]:
    """Self-Query 检索：拆解 → 向量检索 + 元数据过滤 → 必要时降级。

    Args:
        query: 用户自然语言
        vector_memory: VectorMemory 实例（需有 embed / search）
        llm_client: Anthropic 兼容 client
        top_k: 返回条数
        user_id: 租户过滤；None 表示不过滤 user_id
        memory_type: Tool 显式传入时覆盖 LLM 抽取的 memory_type

    Returns:
        (results, parts)
        results: [{text, score, metadata}, ...]
        parts: {semantic_query, filters}  — 课程 Step 1 的输出，透传给 search_memory Tool 用于日志
    """
    # Step 1: LLM 拆解查询（课程核心）
    parts = parse_self_query(query, llm_client)
    filters = dict(parts["filters"])
    # Tool 显式传了 memory_type 时，覆盖 LLM 抽取的值
    if memory_type:
        filters["memory_type"] = memory_type
        parts["filters"] = filters

    semantic = parts["semantic_query"]
    query_vec = vector_memory.embed(semantic)[0]

    # Step 2: 语义向量检索 + 元数据硬过滤（课程核心）
    # 注意: 排除 user_id——它单独合并进 where，不作为业务 filters 参与降级判断
    business_filters = {k: v for k, v in filters.items() if k != "user_id"}
    where = _build_where(business_filters, user_id)

    try:
        results = vector_memory.search(query_vec, top_k=top_k, filters=where)
    except Exception as e:
        logger.info("self_query: 带过滤检索失败 (%s)，降级", e)
        results = []

    # Step 3: 降级重试——结果太少时放宽过滤（课程原文）
    if len(results) < 2 and business_filters:
        logger.info(
            "self_query: 过滤结果过少 (%d)，去掉业务 filters 重试",
            len(results),
        )
        try:
            results = vector_memory.search(
                query_vec,
                top_k=top_k,
                filters=_build_where({}, user_id),
            )
        except Exception as e:
            logger.info("self_query: 降级检索也失败 (%s)", e)
            results = vector_memory.search(query_vec, top_k=top_k, filters=None)

    return results, parts
