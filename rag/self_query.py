import json

SELF_QUERY_PROMPT = """从用户查询中提取: (1) 用于语义检索的核心问题 (2) 用于精确过滤的条件。

支持的过滤字段: year, office, doc_type(query/report/hr_doc), department

返回 JSON 格式:
{
  "semantic_query": "只保留核心语义部分，去掉已知的过滤条件",
  "filters": {"year": "2025", "office": "北京"}
}

用户查询: {query}
只返回 JSON，不要其他内容。"""

async def self_query_retrieve(query: str, vector_db, llm_client):
    """Self-Query: LLM 提取过滤条件 + 向量检索。"""
    # Step 1: LLM 拆解查询
    resp = llm_client.messages.create(
        model="claude-haiku-3-5",
        max_tokens=200,
        messages=[{"role": "user", "content": SELF_QUERY_PROMPT.format(query=query)}],
    )
    parts = json.loads(resp.content[0].text)

    # Step 2: 语义向量检索 + 元数据硬过滤
    query_vec = embed(parts["semantic_query"])
    results = vector_db.search(
        query_vec,
        top_k=5,
        where=parts.get("filters", {}),
    )

    # Step 3: 如果结果太少，放宽过滤重试
    if len(results) < 3 and parts.get("filters"):
        results = vector_db.search(query_vec, top_k=5)  # 不做过滤

    return results, parts