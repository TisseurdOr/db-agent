"""离线确定性 embedding——让向量记忆 / Self-Query 测试不依赖 Embedding API。

思路：字符级哈希 → 固定维度向量 + L2 归一化。语义相近的中文文本共享字符，
余弦相似度会更高，足以支撑测试里"模糊查询召回正确主题"的断言。
完全离线、确定、零成本。
"""
import hashlib

_DIM = 1024  # 足够大，避免 md5 哈希碰撞淹没真实语义信号


def _char_hash(ch: str) -> int:
    return int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16)


def fake_embedding(texts: list[str] | str) -> list[list[float]]:
    """把文本映射为固定维度向量（字符级词袋 + L2 归一化）。"""
    if isinstance(texts, str):
        texts = [texts]
    out = []
    for text in texts:
        vec = [0.0] * _DIM
        for ch in text:
            vec[_char_hash(ch) % _DIM] += 1.0
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        out.append(vec)
    return out
