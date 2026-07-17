import numpy as np
from openai import OpenAI  # 用 OpenAI embedding（最方便）
import uuid
import chromadb
import os
from utils.llm import extract_text, logger  # 安全取文字 + 共享日志器

class ChromaVectorDB:
    """把 ChromaDB 包成 RAGPipeline 期望的 .add() / .search() 接口。

    为什么要这层适配器：
    - RAGPipeline 里用的是 self.vector_db.search(query_vec, top_k, filters)
    - 但 ChromaDB 原生方法叫 .query()，参数名也不同（query_embeddings/n_results/where）
    - 用适配器把「统一接口」翻译成「ChromaDB 的调用」，RAGPipeline 就不用改
    """

    def __init__(self, persist_dir="memory/chroma_db", collection_name="conversations"):
        # PersistentClient = 落盘，重启不丢；cosine = 文本相似度常用距离
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, ids, documents, embeddings, metadatas=None):
        """写入：向量 + 原文 + 元数据。"""
        self.collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info("向量库写入: %d 条, 库中共 %d 条", len(ids), self.collection.count())

    def search(self, query_vec, top_k=5, filters=None):
        """检索：翻译成 ChromaDB 的 query()，返回 RAGPipeline 要的 {text, score} 列表。"""
        count = self.collection.count()
        if count == 0:            # 空库直接返回，别让 query 报错
            logger.info("向量检索: 库为空，跳过")
            return []
        res = self.collection.query(
            query_embeddings=[query_vec],       # 用现成向量（模式B）
            n_results=min(top_k, count),        # 别超过库里总数
            where=filters,                      # None 表示不过滤
        )
        # query 支持多条查询，返回嵌套 list，取第 0 条
        docs = res["documents"][0]
        dists = res["distances"][0]
        metas = res["metadatas"][0]
        # 余弦距离越小越像 → 换成 0~1 的 score，rerank/query 用得上
        hits = [
            {"text": d, "score": round(1 - dist, 3), "metadata": m}
            for d, dist, m in zip(docs, dists, metas)
        ]
        logger.info("向量检索: 请求 top_k=%d, 命中 %d 条", top_k, len(hits))
        return hits

class RAGPipeline:
    def __init__(self, vector_db, llm_client, embed_model=None, hyde_client=None, hyde_model=None):
        self.vector_db = vector_db
        self.llm = llm_client  # 主 LLM（DeepSeek Anthropic）——rerank / query / HyDE 默认用它
        self.embed_client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        self.embed_model = embed_model or os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

        # HyDE 默认走主 Agent 同一条 DeepSeek 链路（Anthropic 兼容）。
        # 百炼对话模型在你账号上常报 Arrearage，embedding 能用但 chat 不行。
        # 若以后要单独换 HyDE 提供商，传 hyde_client 或设 HYDE_USE_OPENAI=1。
        self.hyde_use_openai = os.getenv("HYDE_USE_OPENAI", "").lower() in ("1", "true", "yes")
        if hyde_client is not None:
            self.hyde_client = hyde_client
        elif self.hyde_use_openai:
            self.hyde_client = OpenAI(
                api_key=os.getenv("HYDE_API_KEY") or os.environ["EMBEDDING_API_KEY"],
                base_url=os.getenv("HYDE_BASE_URL") or os.environ["EMBEDDING_BASE_URL"],
            )
        else:
            self.hyde_client = None  # 用 self.llm
        self.hyde_model = hyde_model or os.getenv("HYDE_MODEL") or os.getenv("ANTHROPIC_MODEL", "deepseek-chat")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量"""
        resp = self.embed_client.embeddings.create(
            model=self.embed_model, input=texts
        )
        vecs = [d.embedding for d in resp.data]
        logger.info("embedding: model=%s, %d 条文本 -> %d 维", self.embed_model, len(texts), len(vecs[0]) if vecs else 0)
        return vecs

    async def retrieve(self, query: str, top_k: int = 5,
                       filters: dict = None) -> list[dict]:
        """检索 + 可选 HyDE + 可选 rerank"""
        logger.info("retrieve 开始: query=%r, top_k=%d, HyDE=%s", query, top_k, len(query) < 20)
        # Step 1: 判断是否需要 HyDE
        if len(query) < 20:  # 短查询——用 HyDE
            hypo = await self._generate_hypothesis(query)
            # HyDE 可能返回空：带思考的模型（如 DeepSeek）可能把 max_tokens 全用在
            # thinking 上、没输出正文。空串不能拿去 embedding（会得到异常维度的向量，
            # 导致与库中向量维度不匹配而崩），退回用原始 query。
            text_to_embed = hypo if hypo.strip() else query
            query_vec = (await self.embed([text_to_embed]))[0]
        else:
            query_vec = (await self.embed([query]))[0]

        # Step 2: 向量检索
        results = self.vector_db.search(
            query_vec, top_k=top_k * 3, filters=filters  # 多取一些给 rerank
        )

        # Step 3: Rerank（如果结果多）
        if len(results) > top_k:
            results = await self._rerank(query, results, top_k)

        logger.info("retrieve 完成: 返回 %d 条", len(results[:top_k]))
        return results[:top_k]

    async def _generate_hypothesis(self, query: str) -> str:
        """HyDE: 生成假设性答案。默认走 DeepSeek（Anthropic SDK），能真正出正文。"""
        logger.info("HyDE 触发: query=%r, model=%s, via=%s",
                    query, self.hyde_model,
                    "openai" if self.hyde_client is not None else "anthropic")
        prompt = (
            "请用一段话详细描述以下查询可能涉及的场景和数据。"
            "请包含具体数字、日期、类别等细节以帮助检索相关文档。"
            f"查询: {query}"
        )
        try:
            if self.hyde_client is not None:
                # OpenAI 兼容（百炼等）——你账号上 chat 常报 Arrearage，默认不用
                resp = self.hyde_client.chat.completions.create(
                    model=self.hyde_model,
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = (resp.choices[0].message.content or "").strip()
            else:
                # DeepSeek Anthropic 兼容——主 Agent 同链路，已验证可用
                resp = self.llm.messages.create(
                    model=self.hyde_model,
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = extract_text(resp, context="hyde").strip()
            if not text:
                logger.warning("HyDE 返回空正文 (model=%s)", self.hyde_model)
            else:
                logger.info("HyDE 成功: 假设答案前60字=%r", text[:60])
            return text
        except Exception as e:
            logger.warning("HyDE 调用失败，退回原 query: %s", e)
            return ""

    async def _rerank(self, query: str, candidates: list, top_k: int) -> list:
        """简化 rerank: 用 LLM 一次打分"""
        logger.info("rerank 触发: %d 个候选 -> 取 top_k=%d", len(candidates), top_k)
        # 生产环境用 Cohere Rerank API
        pairs = "\n".join([
            f"[{i}] {c['text'][:300]}" for i, c in enumerate(candidates)
        ])
        resp = self.llm.messages.create(
            model="deepseek-chat",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": f"从以下文档片段中选出与查询最相关的{top_k}个。只输出编号，如 3,7,12。查询: {query}\n\n{pairs}"
            }]
        )
        # 解析编号，返回对应 candidates
        try:
            indices = [int(x.strip()) for x in extract_text(resp, context="rerank").split(",")]
            return [candidates[i] for i in indices if i < len(candidates)]
        except (ValueError, IndexError):
            return candidates[:top_k]  # fallback
    async def add_conversation(self, question: str, answer: str, metadata: dict = None):
        """把一轮问答存进长期记忆：拼文字 → embedding → 存向量库。"""
        logger.info("写入长期记忆: 问=%r", question[:40])
        text = f"问: {question}\n答: {answer}"          # 问答拼一条，上下文完整
        vector = (await self.embed([text]))[0]          # 复用你自己的 embed()（模式B）
        self.vector_db.add(
            ids=[str(uuid.uuid4())],                    # 唯一 id
            documents=[text],                           # 原文，方便取回来看
            embeddings=[vector],                        # 算好的向量
            metadatas=[metadata or {"type": "conversation"}],
        )
    async def query_conversation(self, question: str, top_k: int = 5) -> dict:
        """查询长期记忆：拼文字 → embedding → 向量检索"""
        vector = (await self.embed([question]))[0]
        results = self.vector_db.search(vector, top_k=top_k)
        return results

    async def query(self, user_query: str, top_k: int = 5) -> dict:
        """完整 RAG 查询入口"""
        docs = await self.retrieve(user_query, top_k)
        context = "\n\n".join([d["text"] for d in docs])

        # 注入到 System Prompt
        augmented_prompt = f"""基于以下参考信息回答用户问题。如果参考信息不足以回答问题，请说明。

参考信息:
{context}

用户问题: {user_query}"""

        resp = self.llm.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": augmented_prompt}],
        )
        return {
            "answer": extract_text(resp, context="query"),
            "sources": [{"text": d["text"][:200], "score": d.get("score")} for d in docs],
            "hyde_used": len(user_query) < 20,
        }