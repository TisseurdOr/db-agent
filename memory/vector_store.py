# memory/vector_store.py
import os
import chromadb
from datetime import datetime
from openai import OpenAI
from utils.llm import logger


class VectorMemory:
    """基于 ChromaDB 的长期语义记忆 / 向量库（原 ChromaVectorDB 已并入此类）。

    两套用法：
      1. 作业 API：remember(content) / recall(query) —— 内部自己做 embedding
      2. RAGPipeline 后端：add(...) / search(query_vec, ...) —— 接收已算好的向量
    """

    def __init__(self, persist_dir="memory/chroma_db", embed_model=None,
                 collection_name="conversations"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        # 与项目其余部分一致：通义 DashScope（OpenAI 兼容），读 .env
        self.embed_client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        self.embed_model = embed_model or os.getenv(
            "EMBEDDING_MODEL", "qwen3.7-text-embedding"
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},  # 用余弦相似度
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → embedding vectors"""
        if isinstance(texts, str):
            texts = [texts]
        resp = self.embed_client.embeddings.create(
            model=self.embed_model,
            input=texts,
        )
        return [d.embedding for d in resp.data]

    # ── RAGPipeline 用的底层接口（对齐原 ChromaVectorDB）────────────────

    def add(self, ids, documents, embeddings, metadatas=None):
        """写入：向量 + 原文 + 元数据。供 RAGPipeline.add_conversation 调用。"""
        self.collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def search(self, query_vec, top_k=5, filters=None) -> list[dict]:
        """按已有向量检索。供 RAGPipeline.retrieve 调用。

        Returns:
            [{text, score, metadata}, ...]  score = 1 - cosine distance
        """
        count = self.collection.count()
        if count == 0:
            return []
        res = self.collection.query(
            query_embeddings=[query_vec],
            n_results=min(top_k, count),
            where=filters,
            include=["documents", "metadatas", "distances"],
        )
        docs = res["documents"][0]
        dists = res["distances"][0]
        metas = res["metadatas"][0]
        return [
            {"text": d, "score": round(1 - dist, 3), "metadata": m}
            for d, dist, m in zip(docs, dists, metas)
        ]

    # ── 作业 / 直接调用的高层 API ──────────────────────────────────────

    def remember(self, content: str, memory_type: str = "conversation",
                 user_id: str = "default", metadata: dict = None):
        """存一条记忆。

        Args:
            content: 对话片段或知识文本
            memory_type: 'conversation' | 'preference' | 'entity'
            user_id: 多租户标识
            metadata: 额外的过滤属性
        """
        vec = self.embed(content)

        # 生成唯一 ID
        ts = datetime.now().isoformat()
        memory_id = f"{user_id}_{memory_type}_{ts}"

        meta = {
            "memory_type": memory_type,
            "user_id": user_id,
            "timestamp": ts,
            **(metadata or {}),
        }

        self.add(
            ids=[memory_id],
            embeddings=vec,
            documents=[content],
            metadatas=[meta],
        )
        return memory_id

    def recall(self, query: str, top_k: int = 5,
               memory_type: str = None, user_id: str = "default") -> list[dict]:
        """语义检索相关记忆。

        Args:
            query: 自然语言查询（"上次那个销售分析"）
            top_k: 返回最相关的 K 条
            memory_type: 过滤类型，None 表示不过滤
            user_id: 多租户过滤；None 表示不过滤。默认 "default"。
                若过滤 0 命中，会自动退回不过滤（兼容旧库无 user_id 的文档）。
        """
        count = self.collection.count()
        if count == 0:
            logger.info("recall: 库为空，跳过 query=%r", query)
            return []

        logger.info(
            "recall 开始: query=%r, top_k=%d, user_id=%s, memory_type=%s",
            query, top_k, user_id, memory_type,
        )
        query_vec = self.embed(query)

        # 构建元数据过滤。旧数据（add_conversation）只有 {"type": "..."}，无 user_id；
        # 强制 where={"user_id": ...} 会永远空。user_id=None 表示不过滤。
        def _where(uid, mtype):
            if uid and mtype:
                return {"$and": [{"user_id": uid}, {"memory_type": mtype}]}
            if mtype:
                return {"memory_type": mtype}
            if uid:
                return {"user_id": uid}
            return None

        where = _where(user_id, memory_type)
        n = min(top_k, count)

        def _query(w):
            return self.collection.query(
                query_embeddings=query_vec,
                n_results=n,
                where=w,
                include=["documents", "metadatas", "distances"],
            )

        try:
            #有user_id的过滤搜索
            results = _query(where)
        except Exception as e:
            # 过滤条件匹配 0 条时，部分 Chroma 版本会直接抛错
            logger.info("recall: 带过滤查询失败 (%s)，退回不过滤", e)
            results = _query(_where(None, memory_type))
            where = None

        # 有 user_id 过滤但 0 命中 → 多半是旧库无此字段，退回不过滤再搜一次
        if where and user_id and not (results.get("ids") and results["ids"][0]):
            logger.info("recall: user_id 过滤无命中，退回不过滤（兼容旧数据）")
            results = _query(_where(None, memory_type))

        # 格式化结果
        memories = []
        if results["ids"][0]:
            for i, mem_id in enumerate(results["ids"][0]):
                memories.append({
                    "id": mem_id,
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                    # cosine distance → similarity
                    "score": 1 - results["distances"][0][i],
                })
        logger.info("recall 完成: 返回 %d 条", len(memories))
        return memories

    def forget(self, memory_id: str):
        """删除一条记忆。"""
        self.collection.delete(ids=[memory_id])

    def count(self, user_id: str = None) -> int:
        """统计记忆数量。"""
        if user_id:
            result = self.collection.get(where={"user_id": user_id})
        else:
            result = self.collection.get()
        return len(result["ids"]) if result["ids"] else 0

    def list_recent(self, user_id: str = "default", limit: int = 10) -> list[dict]:
        """列出最近的记忆（按时间戳降序，不走向量检索）。

        注意：Chroma get(limit=N) 不是「最新 N 条」，只是任意截断 N 条。
        所以先取出该 user 的全部记忆，再按 timestamp 排序后切片。
        """
        where = {"user_id": user_id} if user_id else None
        result = self.collection.get(
            where=where,
            include=["documents", "metadatas"],
        )
        memories = []
        if result["ids"]:
            for i, mem_id in enumerate(result["ids"]):
                memories.append({
                    "id": mem_id,
                    "text": result["documents"][i],
                    "metadata": result["metadatas"][i] or {},
                })
        memories.sort(
            key=lambda m: m["metadata"].get("timestamp", ""),
            reverse=True,
        )
        return memories[:limit]


# === 使用示例 ===

async def demo():
    mem = VectorMemory()

    # 存
    mem.remember(
        content="用户在 2026-07-10 查询了 Q2 各地区的订单金额分布。北京最高(120万)，上海次之(98万)。",
        memory_type="conversation",
        user_id="user_01",
    )
    mem.remember(
        content="用户偏好：每次查询默认按金额降序排列，不需要确认。默认数据库为 sales_db。",
        memory_type="preference",
        user_id="user_01",
    )

    # 查
    results = mem.recall("上次那个销售分析", user_id="user_01", top_k=3)
    for r in results:
        print(f"score={r['score']:.3f} | {r['text'][:100]}")

    # 按类型过滤
    prefs = mem.recall("用户喜欢什么排序方式", memory_type="preference")
    for p in prefs:
        print(f"偏好: {p['text']}")

    print(f"总记忆数: {mem.count()}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())