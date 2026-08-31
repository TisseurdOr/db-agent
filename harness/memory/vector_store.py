# memory/vector_store.py
import json
import os
from datetime import datetime

from openai import OpenAI

from harness.observation.llm import logger

# ═══════════════════════════════════════════════════════════════════════════════
# 向量存储后端抽象：ChromaDB（本地嵌入式）/ Milvus（Lite 嵌入式或分布式集群）
# ═══════════════════════════════════════════════════════════════════════════════
# 统一接口（where 兼容 Chroma 风格：{k:v} 或 {"$and":[{k:v},...]}）：
#   add(ids, documents, embeddings, metadatas)
#   count(where=None) -> int
#   query(query_vec, top_k, where=None) -> [{id,text,metadata,score,distance}]
#   get(where=None) -> [{id,text,metadata}]
#   delete(ids)
#   drop()


def normalize_where(where: dict | None) -> dict | None:
    """把 Chroma 风格 where（{k:v} 或 {"$and":[{k:v},...]}）拍平成扁平 AND 字典。"""
    if not where:
        return None
    if "$and" in where:
        flat: dict = {}
        for clause in where["$and"]:
            flat.update(clause)
        return flat or None
    return dict(where)


def _meta_to_flat(meta: dict) -> dict:
    """元数据字典 → 可过滤的扁平字段（user_id / memory_type / timestamp + extra_json）。"""
    return {
        "user_id": str(meta.get("user_id", "")),
        "memory_type": str(meta.get("memory_type", "")),
        "timestamp": str(meta.get("timestamp", "")),
        "extra_json": json.dumps(
            {k: v for k, v in meta.items()
             if k not in ("user_id", "memory_type", "timestamp")},
            ensure_ascii=False, default=str,
        ),
    }


def _flat_to_meta(flat: dict) -> dict:
    """扁平记录 → 元数据字典（含 extra_json 里的附加键）。"""
    meta = {
        "user_id": flat.get("user_id", ""),
        "memory_type": flat.get("memory_type", ""),
        "timestamp": flat.get("timestamp", ""),
    }
    extra = flat.get("extra_json") or "{}"
    try:
        meta.update(json.loads(extra))
    except Exception:
        pass
    return meta


class ChromaBackend:
    """ChromaDB 本地嵌入式后端（默认）。"""

    def __init__(self, persist_dir: str, collection_name: str):
        import chromadb
        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def _chroma_where(self, where: dict | None) -> dict | None:
        flat = normalize_where(where)
        if not flat:
            return None
        if len(flat) > 1:
            return {"$and": [{k: v} for k, v in flat.items()]}
        return flat

    def add(self, ids, documents, embeddings, metadatas=None):
        self.collection.add(ids=ids, embeddings=embeddings,
                            documents=documents, metadatas=metadatas)

    def count(self, where: dict | None = None) -> int:
        if where is None:
            return self.collection.count()
        res = self.collection.get(where=self._chroma_where(where))
        return len(res["ids"])

    def query(self, query_vec, top_k: int, where: dict | None = None) -> list[dict]:
        n = min(top_k, self.collection.count())
        if n == 0:
            return []
        res = self.collection.query(
            query_embeddings=[query_vec],
            n_results=n,
            where=self._chroma_where(where),
            include=["documents", "metadatas", "distances"],
        )
        out = []
        if res["ids"] and res["ids"][0]:
            for i, mem_id in enumerate(res["ids"][0]):
                dist = res["distances"][0][i]
                out.append({
                    "id": mem_id,
                    "text": res["documents"][0][i],
                    "metadata": res["metadatas"][0][i],
                    "distance": dist,
                    "score": round(1 - dist, 4),
                })
        return out

    def get(self, where: dict | None = None, limit: int | None = None,
            ids: list | None = None) -> list[dict]:
        kwargs: dict = {"include": ["documents", "metadatas"]}
        if ids is not None:
            kwargs["ids"] = list(ids)
        else:
            kwargs["where"] = self._chroma_where(where)
            if limit is not None:
                kwargs["limit"] = int(limit)
        res = self.collection.get(**kwargs)
        out = []
        if res["ids"]:
            for i, mem_id in enumerate(res["ids"]):
                out.append({
                    "id": mem_id,
                    "text": res["documents"][i],
                    "metadata": res["metadatas"][i] or {},
                })
        return out

    def get_by_ids(self, ids: list) -> list[dict]:
        if not ids:
            return []
        return self.get(ids=ids)

    def delete(self, ids):
        self.collection.delete(ids=ids)

    def drop(self):
        self.client.delete_collection(self.collection.name)


class MilvusBackend:
    """Milvus 后端：URI 指向本地文件 = Milvus Lite（嵌入式）；指向 http://host:19530 = 集群。

    用 MilvusClient（pymilvus 3.x 推荐 API），每个实例一个 client，无全局连接管理。
    """

    def __init__(self, collection_name: str, uri: str | None = None):
        from pymilvus import DataType, MilvusClient  # noqa: F401  DataType 用于 schema
        self.collection_name = collection_name
        # 注意：本地文件路径只能通过 uri 参数传入，不能放 MILVUS_URI 环境变量——
        # pymilvus 导入时会解析该环境变量，文件路径会被 ORM 拒绝。
        # 环境变量只用于 http(s):// 集群地址。
        env_uri = os.getenv("MILVUS_URI", "").strip()
        self.uri = uri or env_uri or "milvus_lite.db"
        self._client = MilvusClient(uri=self.uri)
        self._created = False
        self._dim: int | None = None

    # ── schema / collection ──────────────────────────────────────────

    def _ensure_collection(self, dim: int):
        if self._created and self._dim == dim:
            return
        from pymilvus import DataType
        if self._client.has_collection(self.collection_name):
            # 进程重启/实例重建后复用同一持久化文件：集合已存在，
            # 直接标记复用，不再 create_collection（否则抛 already exists）。
            # 若 dim 与既有集合不一致，后续 insert 会由 Milvus 给出明确报错。
            self._created = True
            self._dim = dim
            return
        schema = self._client.create_schema(auto_id=False)
        schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=255)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)
        schema.add_field("memory_type", DataType.VARCHAR, max_length=64)
        schema.add_field("timestamp", DataType.VARCHAR, max_length=64)
        schema.add_field("extra_json", DataType.VARCHAR, max_length=4096)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
        self._client.create_collection(
            self.collection_name, schema=schema,
            metric_type="COSINE", auto_id=False,
        )
        self._created = True
        self._dim = dim

    def _expr(self, where: dict | None) -> str | None:
        flat = normalize_where(where)
        if not flat:
            return None
        parts = []
        for k, v in flat.items():
            if isinstance(v, str):
                parts.append(f'{k} == "{v}"')
            elif isinstance(v, bool):
                parts.append(f"{k} == {str(v).lower()}")
            else:
                parts.append(f"{k} == {v}")
        return " && ".join(parts)

    _OUTPUT_FIELDS = ["id", "text", "user_id", "memory_type", "timestamp", "extra_json"]

    # ── CRUD ─────────────────────────────────────────────────────────

    def add(self, ids, documents, embeddings, metadatas=None):
        if not embeddings:
            return
        self._ensure_collection(len(embeddings[0]))
        metadatas = metadatas or [{}] * len(ids)
        rows = []
        for i, mem_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            flat = _meta_to_flat(meta)
            rows.append({
                "id": str(mem_id),
                "text": documents[i],
                **flat,
                "embedding": embeddings[i],
            })
        self._client.insert(self.collection_name, rows)
        self._client.flush(self.collection_name)

    def count(self, where: dict | None = None) -> int:
        if not self._created:
            return 0
        if where is None:
            return self._client.get_collection_stats(self.collection_name).get("row_count", 0)
        rows = self._client.query(self.collection_name, filter=self._expr(where), output_fields=["id"])
        return len(rows)

    def query(self, query_vec, top_k: int, where: dict | None = None) -> list[dict]:
        if not self._created:
            return []
        res = self._client.search(
            self.collection_name, data=[query_vec], limit=top_k,
            output_fields=self._OUTPUT_FIELDS,
            filter=self._expr(where),
        )
        out = []
        if not res:
            return []
        for hit in res[0]:
            ent = hit.get("entity", {})
            sim = float(hit.get("distance", 0.0))  # COSINE 下 distance = 相似度（1=最相似）
            out.append({
                "id": hit.get("id"),
                "text": ent.get("text", ""),
                "metadata": _flat_to_meta(ent),
                "distance": round(1 - sim, 4),
                "score": round(sim, 4),
            })
        return out

    def get(self, where: dict | None = None, limit: int | None = None,
            ids: list | None = None) -> list[dict]:
        if not self._created:
            return []
        if ids is not None:
            if not ids:
                return []
            # Milvus query by id list
            id_list = ", ".join(f'"{i}"' for i in ids)
            expr = f"id in [{id_list}]"
            rows = self._client.query(
                self.collection_name, filter=expr, output_fields=self._OUTPUT_FIELDS,
            )
        else:
            kwargs = {
                "collection_name": self.collection_name,
                "filter": self._expr(where) or "",
                "output_fields": self._OUTPUT_FIELDS,
            }
            if limit is not None:
                kwargs["limit"] = int(limit)
            rows = self._client.query(**kwargs)
        return [
            {"id": r.get("id"), "text": r.get("text", ""), "metadata": _flat_to_meta(r)}
            for r in rows
        ]

    def get_by_ids(self, ids: list) -> list[dict]:
        return self.get(ids=ids)

    def delete(self, ids):
        if not self._created:
            return
        for mem_id in ids:
            self._client.delete(self.collection_name, filter=f'id == "{mem_id}"')
        self._client.flush(self.collection_name)

    def drop(self):
        if self._created:
            self._client.drop_collection(self.collection_name)
            self._created = False
            self._dim = None


def make_backend(kind: str, persist_dir: str, collection_name: str):
    """后端工厂：kind = chroma | milvus（读 VECTOR_DB 环境变量可覆盖）。"""
    kind = (kind or os.getenv("VECTOR_DB", "chroma")).strip().lower()
    if kind == "milvus":
        return MilvusBackend(collection_name=collection_name)
    if kind == "chroma":
        return ChromaBackend(persist_dir=persist_dir, collection_name=collection_name)
    raise ValueError(f"未知向量后端: {kind}（支持 chroma / milvus）")


# ═══════════════════════════════════════════════════════════════════════════════

class VectorMemory:
    """基于向量库的长期语义记忆 / 向量库（后端可切换：ChromaDB / Milvus）。

    两套用法：
      1. 作业 API：remember(content) / recall(query) —— 内部自己做 embedding
      2. RAGPipeline 后端：add(...) / search(...) —— 接收已算好的向量
    """

    def __init__(self, persist_dir="harness/memory/chroma_db", embed_model=None,
                 collection_name="conversations", embed_fn=None, embed_client=None,
                 backend=None):
        self.backend = (
            backend
            if isinstance(backend, (ChromaBackend, MilvusBackend))
            else make_backend(backend, persist_dir, collection_name)
        )
        # 惰性初始化：不在构造时连 Embedding API（可注入 embed_fn / embed_client）
        self._embed_fn = embed_fn
        self._embed_client = embed_client
        self.embed_model = embed_model or os.getenv(
            "EMBEDDING_MODEL", "qwen3.7-text-embedding"
        )

    # 兼容：老代码/测试可能直接访问 chroma client
    @property
    def client(self):
        return getattr(self.backend, "client", None)

    @property
    def collection(self):
        return getattr(self.backend, "collection", None)

    def _get_embed_client(self) -> OpenAI:
        """按需创建 Embedding client；缺配置时给出可读错误而不是 KeyError。"""
        if self._embed_client is not None:
            return self._embed_client
        api_key = os.getenv("EMBEDDING_API_KEY")
        base_url = os.getenv("EMBEDDING_BASE_URL")
        if not api_key or not base_url:
            raise RuntimeError(
                "VectorMemory.embed 需要 EMBEDDING_API_KEY / EMBEDDING_BASE_URL，"
                "或构造时显式传入 embed_fn / embed_client"
            )
        self._embed_client = OpenAI(api_key=api_key, base_url=base_url)
        return self._embed_client

    def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → embedding vectors"""
        if isinstance(texts, str):
            texts = [texts]
        if self._embed_fn is not None:
            return self._embed_fn(texts)
        resp = self._get_embed_client().embeddings.create(
            model=self.embed_model,
            input=texts,
        )
        return [d.embedding for d in resp.data]

    # ── RAGPipeline 底层接口 ─────────────────────────────────────────

    def add(self, ids, documents, embeddings, metadatas=None):
        self.backend.add(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    def search(self, query_vec, top_k=5, filters=None) -> list[dict]:
        """按已有向量检索（filters 兼容 Chroma where 格式）。"""
        return self.backend.query(query_vec, top_k=top_k, where=filters)

    # ── 作业 / 高层 API ──────────────────────────────────────────────

    def remember(self, content: str, memory_type: str = "conversation",
                 user_id: str = "default", metadata: dict = None):
        vec = self.embed(content)
        ts = datetime.now().isoformat()
        memory_id = f"{user_id}_{memory_type}_{ts}"
        meta = {
            "memory_type": memory_type,
            "user_id": user_id,
            "timestamp": ts,
            **(metadata or {}),
        }
        self.backend.add(
            ids=[memory_id],
            embeddings=vec,
            documents=[content],
            metadatas=[meta],
        )
        try:
            from harness.memory.recent_index import append_recent
            append_recent(
                self.collection_name,
                memory_id,
                user_id=user_id,
                timestamp=ts,
                memory_type=memory_type,
            )
        except Exception:
            pass
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
        count = self.backend.count()
        if count == 0:
            logger.info("recall: 库为空，跳过 query=%r", query)
            return []

        logger.info(
            "recall 开始: query=%r, top_k=%d, user_id=%s, memory_type=%s",
            query, top_k, user_id, memory_type,
        )
        query_vec = self.embed(query)[0]  # 单向量；后端接口统一收单向量

        def _where(uid, mtype):
            where = {}
            if uid:
                where["user_id"] = uid
            if mtype:
                where["memory_type"] = mtype
            return where or None

        where = _where(user_id, memory_type)
        n = min(top_k, count)

        try:
            results = self.backend.query(query_vec, top_k=n, where=where)
        except Exception as e:
            # 过滤条件匹配 0 条时部分后端会抛错——退回不过滤
            logger.info("recall: 带过滤查询失败 (%s)，退回不过滤", e)
            results = self.backend.query(query_vec, top_k=n, where=_where(None, memory_type))
            where = None

        # 有 user_id 过滤但 0 命中 → 多半是旧库无此字段，退回不过滤再搜一次
        if where and user_id and not results:
            logger.info("recall: user_id 过滤无命中，退回不过滤（兼容旧数据）")
            results = self.backend.query(query_vec, top_k=n, where=_where(None, memory_type))

        logger.info("recall 完成: 返回 %d 条", len(results))
        return results

    def forget(self, memory_id: str):
        """删除一条记忆。"""
        self.backend.delete([memory_id])

    def count(self, user_id: str = None) -> int:
        """统计记忆数量。"""
        if user_id:
            return self.backend.count(where={"user_id": user_id})
        return self.backend.count()

    def list_recent(self, user_id: str = "default", limit: int = 10,
                    memory_type: str | None = None) -> list[dict]:
        """列出最近的记忆（按时间戳降序，不走向量检索）。

        优先走 append-only recent index（O(tail)），避免 Chroma/Milvus 全量 get。
        无索引时退回有限扫描（count 小）或空列表。
        """
        # 1) index path
        try:
            from harness.memory.recent_index import list_recent_ids
            ids = list_recent_ids(
                self.collection_name,
                user_id=user_id or "default",
                limit=limit,
                memory_type=memory_type,
            )
            if ids and hasattr(self.backend, "get_by_ids"):
                rows = self.backend.get_by_ids(ids)
                # preserve newest-first order from index
                by_id = {r["id"]: r for r in rows}
                ordered = [by_id[i] for i in ids if i in by_id]
                if ordered:
                    return ordered
        except Exception:
            pass

        # 2) fallback: only full-scan when collection is small
        try:
            total = self.backend.count(where={"user_id": user_id} if user_id else None)
        except Exception:
            total = self.backend.count()
        max_full = int(os.getenv("MEMORY_LIST_RECENT_FULL_SCAN_MAX", "500"))
        if total > max_full:
            return []
        where = {"user_id": user_id} if user_id else None
        memories = self.backend.get(where=where)
        if memory_type:
            memories = [
                m for m in memories
                if (m.get("metadata") or {}).get("memory_type") == memory_type
            ]
        memories.sort(key=lambda m: m["metadata"].get("timestamp", ""), reverse=True)
        return memories[:limit]

    def drop(self) -> None:
        """清空整个 collection（测试 / 重建用）。"""
        self.backend.drop()
        try:
            from harness.memory.recent_index import drop_index
            drop_index(self.collection_name)
        except Exception:
            pass


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
    pass
