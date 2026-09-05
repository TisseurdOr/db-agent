# memory/long_term_memory.py — RAG 检索增强层
#
# 向量库（ChromaDB）已抽到 memory/vector_store.VectorMemory。
# 本文件只负责：embedding + HyDE + rerank + 对话读写编排。
# 依赖 vector_db 提供：add(...) / search(query_vec, ...) —— VectorMemory 已对齐。

import json
import math
import os
import re
import uuid
from collections import Counter

from openai import OpenAI  # 用 OpenAI embedding（最方便）

from harness.config import DEFAULT_MODEL
from harness.constraints.retry import acall_with_retry
from harness.observation.llm import extract_text, logger  # 安全取文字 + 共享日志器

RERANK_PROMPT = """你是检索排序器。根据查询与文档片段的相关性，给每个编号打分（0-1）。
只输出 JSON 数组，按相关性从高到低排序，例如：
[{{"index": 3, "score": 0.95}}, {{"index": 7, "score": 0.61}}]

查询: {query}

候选片段:
{pairs}"""


def _parse_rerank_output(text: str, candidates: list, top_k: int) -> list:
    """解析 LLM 重排输出：优先 JSON 分数，其次逗号编号，失败退回原顺序。"""
    if not text:
        return candidates[:top_k]

    # 优先 JSON 数组：[{"index": 3, "score": 0.9}, ...]
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            items = json.loads(m.group())
            scored = []
            for item in items:
                if isinstance(item, dict):
                    try:
                        idx = int(item.get("index", -1))
                        score = float(item.get("score", 0.0))
                    except (TypeError, ValueError):
                        continue
                elif isinstance(item, (int, float)):
                    idx, score = int(item), 1.0
                else:
                    continue
                if 0 <= idx < len(candidates):
                    scored.append((idx, score))
            if scored:
                seen = set()
                ordered = []
                for idx, _ in sorted(scored, key=lambda x: -x[1]):
                    if idx not in seen:
                        seen.add(idx)
                        ordered.append(idx)
                if ordered:
                    return [candidates[i] for i in ordered][:top_k]
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    # 兼容旧格式：逗号分隔编号，如 "3,7,12"
    try:
        indices = [
            int(x.strip())
            for x in text.replace("，", ",").split(",")
            if x.strip().lstrip("-").isdigit()
        ]
        valid = [i for i in indices if 0 <= i < len(candidates)]
        if valid:
            return [candidates[i] for i in valid][:top_k]
    except ValueError:
        pass

    return candidates[:top_k]


def _tokenize(text: str) -> list[str]:
    """ASCII 词 + CJK 单字。中文无空格，退化成单字也能做关键词匹配。"""
    terms = re.findall(r"[a-z0-9]+", text.lower())
    terms += re.findall(r"[一-鿿]", text)
    return terms


def _bm25_score_corpus(corpus: list[dict], query: str, top_k: int) -> list[dict]:
    """对语料跑 BM25，返回 top_k，score 归一化到 [0,1]（top=1.0）。"""
    n = len(corpus)
    if n == 0:
        return []
    tokenized = [(d, _tokenize(d["text"])) for d in corpus]
    doc_freqs: Counter = Counter()
    for _, terms in tokenized:
        for t in set(terms):
            doc_freqs[t] += 1
    avg_len = sum(len(t) for _, t in tokenized) / n
    qterms = _tokenize(query)
    k1, b = 1.5, 0.75

    scored = []
    for d, terms in tokenized:
        tf = Counter(terms)
        dl = len(terms) or 1
        s = 0.0
        for t in qterms:
            f = tf.get(t, 0)
            if not f:
                continue
            df = doc_freqs.get(t, 0)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avg_len))
        if s > 0:
            scored.append((s, d))

    scored.sort(key=lambda x: -x[0])
    top = scored[:top_k]
    max_s = top[0][0] if top else 1.0
    return [
        {**d, "score": round(s / max_s, 4) if s > 0 else 0.0, "bm25": round(s, 4)}
        for s, d in top
    ]


def _rrf_fuse(rankings: list[list[dict]], top_k: int = 10, k: int = 60) -> list[dict]:
    """RRF 融合多路排序：score(d) = Σ 1/(k+rank)。score 归一化到 [0,1]。"""
    scores: dict = {}
    docs: dict = {}
    for ranking in rankings:
        for rank, d in enumerate(ranking):
            did = d["id"]
            docs[did] = d
            scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank + 1)
    ordered = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    max_s = ordered[0][1] if ordered else 1.0
    out = []
    for did, s in ordered:
        d = dict(docs[did])
        d["score"] = round(s / max_s, 4)
        d["rrf"] = round(s, 4)
        out.append(d)
    return out


class RAGPipeline:
    def __init__(self, vector_db, llm_client, embed_model=None):
        self.vector_db = vector_db
        self.llm = llm_client
        # self.embed_client = OpenAI()  # 或本地 BGE 模型
        self.embed_client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        self.embed_model = embed_model or os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")

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
    async def retrieve(self, query: str, top_k: int = 5,
                       filters: dict = None,
                       use_hyde: bool = True,
                       use_rerank: bool = True,
                       use_hybrid: bool = False) -> list[dict]:
        """检索 + 可选 HyDE + 可选 rerank + 可选混合(BM25+RRF)。开关独立，便于消融。"""
        logger.info(
            "retrieve 开始: query=%r, top_k=%d, HyDE=%s, rerank=%s, hybrid=%s",
            query, top_k, use_hyde, use_rerank, use_hybrid,
        )

        # Step 1: 可选 HyDE——生成假设性答案再 embedding，缩小 query 与文档的语义鸿沟
        # （只影响向量一侧；BM25 走原始 query 关键词，不吃 HyDE）
        if use_hyde:
            hypo = await self._generate_hypothesis(query)
            # HyDE 可能返回空：带思考的模型（如 DeepSeek）可能把 max_tokens 全用在
            # thinking 上、没输出正文。空串不能拿去 embedding（会得到异常维度的向量，
            # 导致与库中向量维度不匹配而崩），退回用原始 query。
            text_to_embed = hypo if hypo.strip() else query
        else:
            text_to_embed = query
        query_vec = (await self.embed([text_to_embed]))[0]

        # Step 2: 向量检索（rerank/hybrid 前多取一些候选）
        fetch_k = top_k * 3 if (use_rerank or use_hybrid) else top_k
        results = self.vector_db.search(
            query_vec, top_k=fetch_k, filters=filters
        )

        # Step 2b: 混合检索——BM25 关键词 + 向量，RRF 融合
        if use_hybrid:
            bm25_results = self._bm25_search(query, filters=filters, top_k=fetch_k)
            results = _rrf_fuse([results, bm25_results], top_k=fetch_k)

        # Step 3: 可选 rerank——用 LLM 从候选里重排
        if use_rerank and len(results) > top_k:
            results = await self._rerank(query, results, top_k)

        logger.info("retrieve 完成: 返回 %d 条", len(results[:top_k]))
        return results[:top_k]

    def _bm25_search(self, query: str, filters: dict = None, top_k: int = 10) -> list[dict]:
        """BM25 关键词检索。# ponytail: O(N) 全量扫描语料，库上百万条再换倒排索引。"""
        corpus = self.vector_db.backend.get(where=filters)
        return _bm25_score_corpus(corpus, query, top_k)

    async def _generate_hypothesis(self, query: str) -> str:
        """HyDE: 生成假设性答案"""
        logger.info("HyDE 触发: query=%r", query)
        resp = await acall_with_retry(
            self.llm.messages.create,
            # model="claude-haiku-3-5",  # 便宜模型够了
            model=DEFAULT_MODEL,
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
        """用 LLM 给候选打分并重排；解析失败退回向量粗排顺序。"""
        logger.info("rerank 触发: %d 个候选 -> 取 top_k=%d", len(candidates), top_k)
        # 生产环境用 Cohere Rerank API
        pairs = "\n".join([
            f"[{i}] {c['text'][:300]}" for i, c in enumerate(candidates)
        ])
        resp = await acall_with_retry(
            self.llm.messages.create,
            model=DEFAULT_MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": RERANK_PROMPT.format(query=query, pairs=pairs),
            }]
        )
        text = extract_text(resp, context="rerank")
        return _parse_rerank_output(text, candidates, top_k)

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
            model=DEFAULT_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": augmented_prompt}],
        )
        return {
            "answer": extract_text(resp, context="query"),
            "sources": [{"text": d["text"][:200], "score": d.get("score")} for d in docs],
            "hyde_used": len(user_query) < 20,
        }
