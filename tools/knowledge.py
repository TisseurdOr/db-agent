"""知识库检索 + 用户记忆 + 向量记忆 Tool。

用 @tool 装饰器写的版本——对比之前手动写 JSON Schema dict 的版本：
  之前: 每个 Tool ~30 行（schema dict 25 行 + 函数 5 行），参数名改一处改三处
  现在: 每个 Tool ~15 行（装饰器 1 行 + docstring 8 行 + 函数体 6 行），
        改参数只改函数签名，schema 自动跟。

装饰器做的事（tools/__init__.py）：
  1. type hints → JSON Schema 类型映射
  2. docstring → 每个参数生成 description
  3. 有默认值的参数 → 不放入 required
  4. 函数名 → Tool name
"""

import sqlite3
from tools import tool

# 向量记忆 / LLM——由 main.py 注入（模块级单例，避免 Tool 参数里传对象）
_vector_memory = None
_llm_client = None


def set_vector_memory(vm):
    """注入 VectorMemory 实例。main.py 初始化后调用。"""
    global _vector_memory
    _vector_memory = vm


def set_llm_client(client):
    """注入 LLM client，供 search_memory 的 Self-Query 拆解使用。"""
    global _llm_client
    _llm_client = client


# ─── 模拟知识库文档 ───────────────────────────────────────────
# Phase 3.3 换 ChromaDB 向量检索，这套文档结构不变。

def _ensure_user_memory_table(conn):
    """读取 db/user_memory.sql 确保表结构存在（课程 0017：独立 schema 文件）。"""
    import os
    sql_path = os.path.join(os.path.dirname(__file__), "..", "db", "user_memory.sql")
    with open(sql_path) as f:
        conn.executescript(f.read())


_KNOWLEDGE_BASE = {
    "销售提成制度": (
        "销售部提成 = 订单金额 × 提成比例。"
        "提成比例按产品类型：软件类 8%，硬件类 5%，服务类 3%。"
        "季度销售额超过 50 万的销售代表，提成比例上浮 2 个百分点。"
    ),
    "员工福利政策": (
        "正式员工享有五险一金、补充商业保险、年度体检。"
        "入职满 1 年享有 10 天带薪年假，满 3 年 15 天，满 5 年 20 天。"
        "每月交通补贴 500 元，通讯补贴 300 元。加班餐补：工作日超 2h 补 50 元。"
    ),
    "产品定价说明": (
        "企业版 SaaS 年费 50,000 元，专业版 20,000 元，基础版 5,000 元。"
        "定制开发 4,000 元/人天，紧急 6,000 元/人天。技术咨询 30,000 元起。"
    ),
    "2026年公司战略": (
        "Q1: SaaS 3.0 上线。Q2: 拓展华东市场，新增 50 客户，营收增长 20%。"
        "Q3: 启动 AI 功能开发，招 5 名 AI 工程师。全年: 营收 5000 万，净利率 15%。"
    ),
}


# ─── Tool 定义 ────────────────────────────────────────────────
# 关键设计意图:
# - search_memory: 查"Agent 经历过什么"（跨会话记忆）—— Self-Query + 向量检索
# - run_query:    查"数据库里有什么"（结构化实时数据）— tools/query.py
# - search_knowledge_base: 查"公司知道什么"（静态知识文档）


@tool(description=(
    "搜索公司知识库（规章制度、产品政策、战略文档）。"
    "当用户问公司的提成怎么算、年假多少天、产品怎么定价等非数据库查询时调用。"
    "不要用此 Tool 查销售数据、订单——那些在数据库里，用 run_query。"
    "返回 {results: [{title, content, score}], count}；无匹配时 hint 建议换关键词。"
))
def search_knowledge_base(query: str, top_k: int = 3) -> dict:
    """query: 自然语言搜索词，如 '提成'、'年假'
    top_k: 返回条数，默认 3"""
    if not query.strip():
        return {"results": [], "count": 0, "hint": "搜索词为空"}

    def score(text: str) -> float:
        q, t = query.lower(), text.lower()
        if q in t:
            return 10.0 + len(q)
        hits = sum(1 for kw in q.split() if kw in t)
        return hits / max(len(q.split()), 1) * 5

    scored = []
    for title, content in _KNOWLEDGE_BASE.items():
        s = score(title) * 1.5 + score(content)
        if s > 0:
            scored.append({"title": title, "content": content, "score": round(s, 1)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    results = scored[:top_k]

    return {
        "results": results, "count": len(results),
        "hint": None if results else "没有匹配的文档，试试换个关键词",
    }


@tool(description=(
    "将重要信息存入用户记忆。当用户表达偏好（'以后按降序排'）、"
    "做决策、或产生值得记住的洞察时调用。跨会话可通过 read_memory 找回。"
    "返回 {stored: true, memory_id: N}。"
))
def save_to_memory(content: str, memory_type: str = "note", user_id: str = "default") -> dict:
    """content: 要记忆的内容，完整描述方便以后检索
    memory_type: preference(偏好) / insight(洞察) / note(备注)
    user_id: 用户标识，默认 'default'"""
    from db.seed import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_user_memory_table(conn)
        cur = conn.execute(
            "INSERT INTO user_memory (user_id, memory_type, content) VALUES (?,?,?)",
            (user_id, memory_type, content))
        conn.commit()
        return {"stored": True, "memory_id": cur.lastrowid}
    except Exception as e:
        return {"error": True, "message": str(e)}
    finally:
        conn.close()


@tool(description=(
    "读取用户记忆。用户说'上次'、'之前'、'我的偏好'时调用。"
    "按时间倒序返回。返回 {memories: [{id, memory_type, content, created_at}], count}。"
))
def read_memory(memory_type: str = "all", limit: int = 10, user_id: str = "default") -> dict:
    """memory_type: preference / insight / note / all（不过滤）
    limit: 返回条数，默认 10
    user_id: 用户标识，默认 'default'"""
    from db.seed import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_user_memory_table(conn)
        if memory_type == "all":
            cur = conn.execute(
                "SELECT * FROM user_memory WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit))
        else:
            cur = conn.execute(
                "SELECT * FROM user_memory WHERE user_id=? AND memory_type=? ORDER BY created_at DESC LIMIT ?",
                (user_id, memory_type, limit))
        mems = [dict(r) for r in cur.fetchall()]
        if mems:
            ids = [m["id"] for m in mems]
            conn.execute(
                f"UPDATE user_memory SET access_count=access_count+1 WHERE id IN ({','.join('?'*len(ids))})", ids)
            conn.commit()
        return {"memories": mems, "count": len(mems)}
    except Exception as e:
        return {"error": True, "message": str(e)}
    finally:
        conn.close()


@tool(description=(
    "搜索 Agent 的长期对话记忆（Self-Query + 向量语义检索）。"
    "当用户提到'上次'、'之前'、'我记得'、'历史'等引用过去对话的关键词时调用。"
    "也适合用户问模糊的问题、需要从历史中找到相关上下文时。"
    "内部会先拆解语义部分与过滤条件（memory_type/year），再检索。"
    "注意：查结构化数据（订单、员工、销售额）用 run_query；查公司政策用 search_knowledge_base。"
    "返回 {results: [{text, score, metadata}], count, parsed}；库为空时返回空列表。"
))
async def search_memory(query: str, top_k: int = 5,
                        memory_type: str = None) -> dict:
    """query: 自然语言查询，如 '上次那个销售分析'、'之前讨论过的地区数据'
    top_k: 返回条数，默认 5
    memory_type: conversation(对话) / preference(偏好) / None(不过滤，由 Self-Query 抽取)"""
    if _vector_memory is None:
        return {"results": [], "count": 0, "error": "向量记忆未初始化"}
    if _llm_client is None:
        # 无 LLM 时退回普通 recall，保证 Tool 仍可用
        results = _vector_memory.recall(
            query, top_k=top_k, memory_type=memory_type,
        )
        return {
            "results": [
                {"text": r["text"], "score": r.get("score"), "metadata": r.get("metadata")}
                for r in results
            ],
            "count": len(results),
            "parsed": {"semantic_query": query, "filters": {}},
        }

    from rag.self_query import self_query_retrieve

    results, parts = await self_query_retrieve(
        query,
        _vector_memory,
        _llm_client,
        top_k=top_k,
        memory_type=memory_type,
    )
    return {
        "results": [
            {"text": r["text"], "score": r.get("score"), "metadata": r.get("metadata")}
            for r in results
        ],
        "count": len(results),
        "parsed": parts,
    }
