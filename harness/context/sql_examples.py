# rag/sql_examples.py — 检索式 few-shot：SQL 样例库（Vanna 模式）
#
# 核心思路（对标 vanna-ai/vanna）：
#   Text-to-SQL 的准确率瓶颈不在模型，在上下文。把"已验证正确"的
#   Q→SQL 对存进向量库，提问时召回最相似的几条注入 prompt——
#   模型照着同库同表的真实样例写，比凭 schema 空写准得多。
#
# 与自学习闭环的关系（升级路线第 3 项的地基）：
#   add_example() 是回流入口——用户确认正确的 SQL、HITL 批准的查询
#   以后都可以写回样例库，系统越用越准。
#
# 降级安全：
#   没配 EMBEDDING_API_KEY / Chroma 不可用 / 检索报错 → 返回空串，
#   SQL Agent 退回纯 schema 模式，绝不因为 few-shot 挂掉主流程。

import os
from pathlib import Path

import chromadb
from openai import OpenAI

_COLLECTION = "sql_examples"

# ── 种子样例：覆盖最常见的查询模式（JOIN / 分组聚合 / 枚举值过滤 / 同环比 / TopN）──
# 全部人工验证过，字段名与 db/seed.py 一致。
SEED_EXAMPLES = [
    {
        "question": "各部门的订单总金额是多少",
        "sql": (
            "SELECT d.name AS 部门, SUM(o.total) AS 订单总金额 "
            "FROM orders o JOIN departments d ON o.dept_id = d.id "
            "GROUP BY d.name ORDER BY 订单总金额 DESC"
        ),
    },
    {
        "question": "华东地区有哪些客户",
        "sql": "SELECT name, city, industry, tier FROM customers WHERE region = '华东'",
    },
    {
        "question": "销量最高的产品是哪个",
        "sql": (
            "SELECT p.name AS 产品, SUM(o.quantity) AS 总销量 "
            "FROM orders o JOIN products p ON o.product_id = p.id "
            "GROUP BY p.name ORDER BY 总销量 DESC LIMIT 1"
        ),
    },
    {
        "question": "对比最近两个月的销售额",
        "sql": (
            "SELECT strftime('%Y-%m', created_at) AS 月份, SUM(total) AS 销售额 "
            "FROM orders WHERE status = 'completed' "
            "GROUP BY 月份 ORDER BY 月份 DESC LIMIT 2"
        ),
    },
    {
        "question": "已取消的订单有多少单，占比多少",
        "sql": (
            "SELECT COUNT(*) AS 取消订单数, "
            "ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM orders), 1) AS 占比 "
            "FROM orders WHERE status = 'cancelled'"
        ),
    },
    {
        "question": "每个客户等级的平均订单金额",
        "sql": (
            "SELECT c.tier AS 客户等级, ROUND(AVG(o.total), 0) AS 平均订单金额 "
            "FROM orders o JOIN customers c ON o.customer_id = c.id "
            "GROUP BY c.tier ORDER BY c.tier"
        ),
    },
    {
        "question": "各区域客户的订单金额分布",
        "sql": (
            "SELECT c.region AS 区域, COUNT(o.id) AS 订单数, SUM(o.total) AS 总金额 "
            "FROM orders o JOIN customers c ON o.customer_id = c.id "
            "GROUP BY c.region ORDER BY 总金额 DESC"
        ),
    },
]

# 检索相似度阈值：低于它的样例宁可不给——错误的参照比没有参照更有害
_MIN_SCORE = 0.35


def format_examples(examples: list[dict]) -> str:
    """把样例列表格式化成可注入 prompt 的 few-shot 文本。纯函数，零依赖。"""
    if not examples:
        return ""
    lines = ["[相似问题的已验证 SQL 参考——同一个库，字段名可直接信任]"]
    for ex in examples:
        lines.append(f"Q: {ex['question']}")
        lines.append(f"SQL: {ex['sql']}")
    return "\n".join(lines)


class SQLExampleStore:
    """Q→SQL 样例库：ChromaDB 存储 + 语义检索 + 回流写入。"""

    def __init__(self):
        self._embed_client: OpenAI | None = None
        self._collection = None

    def _ensure_clients(self):
        if self._collection is not None:
            return
        self._embed_client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        chroma_dir = Path(__file__).resolve().parent.parent / "memory" / "chroma_db"
        client = chromadb.PersistentClient(path=str(chroma_dir))
        self._collection = client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._embed_client.embeddings.create(
            model=os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            input=texts,
        )
        return [d.embedding for d in resp.data]

    def seed(self):
        """首次使用时写入种子样例（已有数据则跳过）。"""
        self._ensure_clients()
        if self._collection.count() > 0:
            return
        questions = [ex["question"] for ex in SEED_EXAMPLES]
        self._collection.add(
            ids=[f"seed_{i}" for i in range(len(SEED_EXAMPLES))],
            documents=questions,
            embeddings=self._embed(questions),
            metadatas=[{"sql": ex["sql"], "source": "seed"} for ex in SEED_EXAMPLES],
        )

    def add_example(self, question: str, sql: str, source: str = "user") -> str:
        """回流入口：把验证过的 Q→SQL 写入样例库（自学习闭环的写路径）。"""
        self._ensure_clients()
        ex_id = f"{source}_{abs(hash(question)) % 10**8}"
        self._collection.upsert(
            ids=[ex_id],
            documents=[question],
            embeddings=self._embed([question]),
            metadatas=[{"sql": sql, "source": source}],
        )
        return ex_id

    def retrieve(self, question: str, top_k: int = 3) -> list[dict]:
        """语义检索最相似的样例，低于阈值的丢弃。"""
        self._ensure_clients()
        self.seed()
        if self._collection.count() == 0:
            return []
        results = self._collection.query(
            query_embeddings=self._embed([question]),
            n_results=min(top_k, self._collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        examples = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                dist = results["distances"][0][i] if results["distances"] else 1.0
                score = 1.0 - min(dist, 1.0)
                if score < _MIN_SCORE:
                    continue
                examples.append({
                    "question": results["documents"][0][i],
                    "sql": results["metadatas"][0][i].get("sql", ""),
                    "score": round(score, 3),
                })
        return examples


# 全局单例
_store: SQLExampleStore | None = None


def get_sql_fewshot(question: str, top_k: int = 3) -> str:
    """给 orchestrator 用的入口：检索相似样例并格式化。

    任何失败（无 key、无网络、Chroma 损坏）都返回空串——
    few-shot 是锦上添花，绝不能挡住主流程。
    """
    global _store
    try:
        if _store is None:
            _store = SQLExampleStore()
        return format_examples(_store.retrieve(question, top_k))
    except Exception as e:
        print(f"   ℹ️ SQL few-shot 检索不可用（{type(e).__name__}），退回纯 schema 模式")
        return ""


def record_sql_example(question: str, sql: str, source: str = "user") -> bool:
    """回流写入的安全封装。成功返回 True，失败静默返回 False。"""
    global _store
    try:
        if _store is None:
            _store = SQLExampleStore()
        _store.add_example(question, sql, source)
        return True
    except Exception:
        return False
