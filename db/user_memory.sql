-- user_memory: 结构化用户记忆表（Phase 3 长期记忆 — 结构化路径）
--
-- 与向量路径（ChromaDB）互补:
--   结构化路径（本表）: 用户偏好、实体关系 → 精确匹配，不走向量检索
--   向量路径（ChromaDB）: 对话片段 → 语义检索，走 embedding
--
-- 选型依据: 结构化数据不需要向量检索（精确匹配更快更准），
-- 向量检索只用在语义模糊的自然语言回忆场景。

CREATE TABLE IF NOT EXISTS user_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    memory_type TEXT NOT NULL,          -- preference / insight / note / entity
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    access_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_user_memory_user_id ON user_memory(user_id);
CREATE INDEX IF NOT EXISTS idx_user_memory_type ON user_memory(memory_type);
