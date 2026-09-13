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

import hashlib
import os
import time
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
    {
        "question": "各产品的销售趋势",
        "sql": (
            "SELECT p.name AS 产品, strftime('%Y-%m', o.created_at) AS 月份, "
            "SUM(o.total) AS 销售额 "
            "FROM orders o JOIN products p ON o.product_id = p.id "
            "GROUP BY p.name, 月份 ORDER BY 月份, 销售额 DESC"
        ),
    },
]

# 检索相似度阈值：低于它的样例宁可不给——错误的参照比没有参照更有害
_MIN_SCORE = 0.35

# learned 样例容量上限：超过就按 LRU（last_hit_at 最旧）淘汰，防止只进不出无限膨胀。
# ponytail: 只对 learned（非 seed）生效；seed 是人工基线永不淘汰。
MAX_LEARNED = int(os.getenv("MAX_LEARNED_EXAMPLES", "200"))


def _example_id(question: str, source: str) -> str:
    """样例稳定 ID。

    不能用 Python 内置 hash()：字符串 hash 每进程随机化（PYTHONHASHSEED），
    跨重启同一问题会生成不同 ID，Chroma upsert 退化为插入，
    样例库会无限堆积重复项。用 hashlib 摘要——跨进程/重启恒定，
    同一问题（同 source）后写覆盖先写，库随使用收敛而非膨胀。
    """
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:16]
    return f"{source}_{digest}"


def format_examples(examples: list[dict]) -> str:
    """把样例列表格式化成可注入 prompt 的 few-shot 文本。纯函数，零依赖。"""
    if not examples:
        return ""
    lines = ["[相似问题的已验证 SQL 参考——同一个库，字段名可直接信任]"]
    for ex in examples:
        lines.append(f"Q: {ex['question']}")
        lines.append(f"SQL: {ex['sql']}")
    return "\n".join(lines)


def _open_collection():
    """直接开 Chroma collection（不碰 embedding 客户端——回滚不能依赖 embedding 服务）。"""
    chroma_dir = Path(__file__).resolve().parent.parent / "memory" / "chroma_db"
    client = chromadb.PersistentClient(path=str(chroma_dir))
    return client.get_or_create_collection(
        name=_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


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
        self._collection = _open_collection()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._embed_client.embeddings.create(
            model=os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            input=texts,
        )
        return [d.embedding for d in resp.data]

    def seed(self):
        """写入种子样例（幂等：只补缺失的 seed，不重复 embed 已有的）。"""
        self._ensure_clients()
        seed_ids = [f"seed_{i}" for i in range(len(SEED_EXAMPLES))]
        existing = set(self._collection.get(ids=seed_ids).get("ids") or [])
        missing = [i for i in range(len(SEED_EXAMPLES)) if seed_ids[i] not in existing]
        if not missing:
            return
        now = int(time.time())
        self._collection.add(
            ids=[seed_ids[i] for i in missing],
            documents=[SEED_EXAMPLES[i]["question"] for i in missing],
            embeddings=self._embed([SEED_EXAMPLES[i]["question"] for i in missing]),
            metadatas=[
                {"sql": SEED_EXAMPLES[i]["sql"], "source": "seed", "created_at": now, "last_hit_at": now, "hit_count": 0}
                for i in missing
            ],
        )

    def _evict_lru(self) -> int:
        """learned 超过容量上限时，按 last_hit_at 最旧淘汰。返回删除条数。"""
        try:
            got = self._collection.get(include=["metadatas"])
            ids = got.get("ids") or []
            metas = got.get("metadatas") or []
            learned = [
                (i, m or {}) for i, m in zip(ids, metas)
                if (m or {}).get("source") != "seed"
            ]
            if len(learned) <= MAX_LEARNED:
                return 0
            learned.sort(key=lambda x: x[1].get("last_hit_at") or 0)
            overflow = learned[: len(learned) - MAX_LEARNED]
            self._collection.delete(ids=[i for i, _ in overflow])
            return len(overflow)
        except Exception:
            return 0

    def add_example(self, question: str, sql: str, source: str = "user") -> str:
        """回流入口：把验证过的 Q→SQL 写入样例库（自学习闭环的写路径）。"""
        self._ensure_clients()
        self._evict_lru()
        ex_id = _example_id(question, source)
        now = int(time.time())
        self._collection.upsert(
            ids=[ex_id],
            documents=[question],
            embeddings=self._embed([question]),
            metadatas=[{"sql": sql, "source": source, "created_at": now, "last_hit_at": now, "hit_count": 0}],
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
        touched = []  # (id, 原 metadata)：命中后刷新使用痕迹
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                dist = results["distances"][0][i] if results["distances"] else 1.0
                score = 1.0 - min(dist, 1.0)
                if score < _MIN_SCORE:
                    continue
                meta = results["metadatas"][0][i] or {}
                touched.append((results["ids"][0][i], meta))
                examples.append({
                    "question": results["documents"][0][i],
                    "sql": meta.get("sql", ""),
                    "score": round(score, 3),
                })
        # 命中即刷新 last_hit_at / hit_count，供 LRU 淘汰判定（失败静默，不影响检索）
        if touched:
            try:
                now = int(time.time())
                self._collection.update(
                    ids=[i for i, _ in touched],
                    metadatas=[
                        {**m, "last_hit_at": now, "hit_count": int(m.get("hit_count") or 0) + 1}
                        for _, m in touched
                    ],
                )
            except Exception:
                pass
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


def list_learned() -> list[dict]:
    """列出所有非 seed 样例（自学习回流进来的），返回 [{id, question, sql, source}]。"""
    try:
        collection = _open_collection()
        got = collection.get(include=["documents", "metadatas"])
        ids = got.get("ids") or []
        docs = got.get("documents") or []
        metas = got.get("metadatas") or []
        learned = []
        for ex_id, question, meta in zip(ids, docs, metas):
            meta = meta or {}
            if meta.get("source") == "seed":
                continue
            learned.append({
                "id": ex_id,
                "question": question,
                "sql": meta.get("sql", ""),
                "source": meta.get("source", ""),
            })
        return learned
    except Exception:
        return []


def purge_learned() -> int:
    """删除所有非 seed 样例（回滚到种子基线）。返回删除条数；失败返回 0。"""
    try:
        collection = _open_collection()
        got = collection.get(include=["metadatas"])
        ids = [
            i for i, m in zip(got.get("ids") or [], got.get("metadatas") or [])
            if (m or {}).get("source") != "seed"
        ]
        if ids:
            collection.delete(ids=ids)
        return len(ids)
    except Exception:
        return 0
