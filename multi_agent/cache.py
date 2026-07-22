"""Router 缓存: LRU 精确匹配，避免相同 query 重复调 LLM。

策略: 去标点、去空格、小写后精确匹配。不做模糊匹配——安全第一。
"""

import re


class RouterCache:
    """LRU 缓存 query → plan 映射。内存存储，进程重启后清空。"""

    def __init__(self, max_size: int = 100):
        self._cache: dict[str, list[dict]] = {}  # {normalized_query: plan}
        self._order: list[str] = []               # LRU 淘汰顺序
        self._max_size = max_size
        self.hits = 0
        self.misses = 0

    def normalize(self, query: str) -> str:
        """去标点、去多余空格、小写。"""
        q = re.sub(r'[^\w\s]', '', query.lower())
        return ' '.join(q.split())

    def get(self, query: str) -> list[dict] | None:
        """查缓存。命中返回 plan，否则返回 None。"""
        key = self.normalize(query)
        if key in self._cache:
            self.hits += 1
            self._touch(key)
            return self._cache[key]
        self.misses += 1
        return None

    def set(self, query: str, plan: list[dict]) -> None:
        """写入缓存。超过 max_size 时淘汰最久未使用的条目。"""
        key = self.normalize(query)
        if key in self._cache:
            self._touch(key)
            self._cache[key] = plan
            return
        if len(self._order) >= self._max_size:
            old = self._order.pop(0)
            del self._cache[old]
        self._cache[key] = plan
        self._order.append(key)

    def _touch(self, key: str) -> None:
        """把 key 移到 LRU 队列末尾（最近使用）。"""
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)

    @property
    def hit_rate(self) -> str:
        total = self.hits + self.misses
        if total == 0:
            return "0/0"
        return f"{self.hits}/{total} ({100 * self.hits // total}%)"
