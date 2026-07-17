# memory/vector_store.py
import chromadb
import json
from datetime import datetime
from openai import OpenAI


class VectorMemory:
    """基于 ChromaDB 的长期语义记忆。

    存: 每次重要的对话片段 → embedding → ChromaDB collection
    查: 自然语言 query → embedding → 向量相似度搜索 → 最相关的记忆
    """

    def __init__(self, persist_dir="./chroma_data", embed_model="text-embedding-3-small"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embed_client = OpenAI()  # 或换成本地 BGE 模型
        self.embed_model = embed_model

        # 获取或创建 collection
        self.collection = self.client.get_or_create_collection(
            name="agent_memory",
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

        self.collection.add(
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
            user_id: 多租户过滤
        """
        query_vec = self.embed(query)

        # 构建元数据过滤条件
        where = {"user_id": user_id}
        if memory_type:
            where["memory_type"] = memory_type

        results = self.collection.query(
            query_embeddings=query_vec,
            n_results=top_k,
            where=where,
            # ChromaDB 返回 document + metadata + distance
            include=["documents", "metadatas", "distances"],
        )

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
        """列出最近的记忆（按时间戳，不走向量检索）。"""
        result = self.collection.get(
            where={"user_id": user_id},
            limit=limit,
            include=["documents", "metadatas"],
        )
        memories = []
        if result["ids"]:
            for i, mem_id in enumerate(result["ids"]):
                memories.append({
                    "id": mem_id,
                    "text": result["documents"][i],
                    "metadata": result["metadatas"][i],
                })
        # 按时间戳降序
        memories.sort(
            key=lambda m: m["metadata"].get("timestamp", ""),
            reverse=True
        )
        return memories


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