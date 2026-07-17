import numpy as np
from openai import OpenAI  # 用 OpenAI embedding（最方便）

class RAGPipeline:
    def __init__(self, vector_db, llm_client, embed_model="text-embedding-3-small"):
        self.vector_db = vector_db
        self.llm = llm_client
        self.embed_client = OpenAI()  # 或本地 BGE 模型
        self.embed_model = embed_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量"""
        resp = self.embed_client.embeddings.create(
            model=self.embed_model, input=texts
        )
        return [d.embedding for d in resp.data]

    async def retrieve(self, query: str, top_k: int = 5,
                       filters: dict = None) -> list[dict]:
        """检索 + 可选 HyDE + 可选 rerank"""
        # Step 1: 判断是否需要 HyDE
        if len(query) < 20:  # 短查询——用 HyDE
            hypo = await self._generate_hypothesis(query)
            query_vec = (await self.embed([hypo]))[0]
        else:
            query_vec = (await self.embed([query]))[0]

        # Step 2: 向量检索
        results = self.vector_db.search(
            query_vec, top_k=top_k * 3, filters=filters  # 多取一些给 rerank
        )

        # Step 3: Rerank（如果结果多）
        if len(results) > top_k:
            results = await self._rerank(query, results, top_k)

        return results[:top_k]

    async def _generate_hypothesis(self, query: str) -> str:
        """HyDE: 生成假设性答案"""
        resp = self.llm.messages.create(
            model="claude-haiku-3-5",  # 便宜模型够了
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"请用一段话详细描述以下查询可能涉及的场景和数据。请包含具体数字、日期、类别等细节以帮助检索相关文档。查询: {query}"
            }]
        )
        return resp.content[0].text

    async def _rerank(self, query: str, candidates: list, top_k: int) -> list:
        """简化 rerank: 用 LLM 一次打分"""
        # 生产环境用 Cohere Rerank API
        pairs = "\n".join([
            f"[{i}] {c['text'][:300]}" for i, c in enumerate(candidates)
        ])
        resp = self.llm.messages.create(
            model="claude-haiku-3-5",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": f"从以下文档片段中选出与查询最相关的{top_k}个。只输出编号，如 3,7,12。查询: {query}\n\n{pairs}"
            }]
        )
        # 解析编号，返回对应 candidates
        try:
            indices = [int(x.strip()) for x in resp.content[0].text.split(",")]
            return [candidates[i] for i in indices if i < len(candidates)]
        except (ValueError, IndexError):
            return candidates[:top_k]  # fallback

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
            "answer": resp.content[0].text,
            "sources": [{"text": d["text"][:200], "score": d.get("score")} for d in docs],
            "hyde_used": len(user_query) < 20,
        }