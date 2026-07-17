# memory/long_term_memory.py — RAG 检索增强层
#
# 向量库（ChromaDB）已抽到 memory/vector_store.VectorMemory。
# 本文件只负责：embedding + HyDE + rerank + 对话读写编排。
# 依赖 vector_db 提供：add(...) / search(query_vec, ...) —— VectorMemory 已对齐。

from openai import OpenAI  # 用 OpenAI embedding（最方便）
import uuid
import os
from utils.llm import extract_text, logger  # 安全取文字 + 共享日志器


class RAGPipeline:
    def __init__(self, vector_db, llm_client, embed_model=None):
        self.vector_db = vector_db
        self.llm = llm_client
        # self.embed_client = OpenAI()  # 或本地 BGE 模型
        self.embed_client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        self.embed_model = embed_model or os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
    '''
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量"""
        resp = self.embed_client.embeddings.create(
            model=self.embed_model, input=texts
        )
        vecs = [d.embedding for d in resp.data]
        logger.info(
            "embedding: model=%s, %d 条文本 -> %d 维",
            self.embed_model, len(texts), len(vecs[0]) if vecs else 0,
        )
        return vecs
    '''
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
        """HyDE: 生成假设性答案"""
        logger.info("HyDE 触发: query=%r", query)
        resp = self.llm.messages.create(
            # model="claude-haiku-3-5",  # 便宜模型够了
            model=os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "请用一段话详细描述以下查询可能涉及的场景和数据。"
                    "请包含具体数字、日期、类别等细节以帮助检索相关文档。"
                    f"查询: {query}"
                ),
            }]
        )
        return extract_text(resp, context="hyde")

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
                "content": (
                    f"从以下文档片段中选出与查询最相关的{top_k}个。"
                    f"只输出编号，如 3,7,12。查询: {query}\n\n{pairs}"
                ),
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

    async def query_conversation(self, question: str, top_k: int = 5) -> list[dict]:
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
