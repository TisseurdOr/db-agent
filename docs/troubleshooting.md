# 错误查询手册（Troubleshooting）

踩过的坑 + 解决办法。遇到报错时用 `Ctrl+F` 搜**报错关键字**或**现象**。

每条格式：**现象 → 原因 → 解决 → 涉及文件**。

---

## 目录

1. [`'ThinkingBlock' object has no attribute 'text'`](#1-thinkingblock-object-has-no-attribute-text)
2. [DeepSeek 报模型不存在 / `claude-haiku-3-5` 调不通](#2-deepseek-报模型不存在)
3. [DashScope 报 `Arrearage` / Access denied](#3-dashscope-报-arrearage--access-denied)
4. [`.env` 里 `EMBEDDING_MODEL` 改了不生效](#4-env-里-embedding_model-改了不生效)
5. [`KeyError: 'answer'`（拼接历史对话时）](#5-keyerror-answer)
6. [`KeyError: 'ANTHROPIC_API_KEY'`](#6-keyerror-anthropic_api_key)
7. [`pip install` 装了包却还是 ModuleNotFound / uv sync 后消失](#7-pip-install-装了包却还是-modulenotfound)
8. [向量维度不匹配 `expecting dimension of 1024, got 2560`](#8-向量维度不匹配-expecting-dimension-of-1024-got-2560)
9. [HyDE 对问候/闲聊短句也触发，白调 LLM 拖慢响应](#9-hyde-对问候闲聊短句也触发白调-llm-拖慢响应)
10. [`recall` 永远空：旧记忆没有 `user_id`](#10-recall-永远空旧记忆没有-user_id)：新旧数据迁移

---

## 1. `'ThinkingBlock' object has no attribute 'text'`

**现象**
```
AttributeError: 'ThinkingBlock' object has no attribute 'text'
```
调用 `resp.content[0].text` 时崩。常见于 HyDE、rerank、摘要压缩、任何解析 LLM 回复的地方。

**原因**
带「思考」的模型（DeepSeek reasoner、Claude thinking 等）返回的 `content` 列表里，
第一个块可能是 **`ThinkingBlock`（思考过程）**，它没有 `.text` 属性。
死取 `content[0].text` 就会崩——不能假设第一个块一定是文字块。

**解决**
遍历 `content`，取第一个 `type == "text"` 的块，别死取 `[0]`。
项目里已封装成 `utils/llm.py` 的 `extract_text()`：
```python
from utils.llm import extract_text
text = extract_text(resp, context="hyde")   # 取不到会记日志并返回 ""
```

**涉及文件**
`utils/llm.py`、`memory/long_term_memory.py`、`memory/short_term_memory.py`

---

## 2. DeepSeek 报模型不存在

**现象**
调 HyDE / rerank 时报模型错误，或请求 `claude-haiku-3-5` 之类的模型失败。

**原因**
代码里把模型名**写死**成了 Claude 的名字（如 `model="claude-haiku-3-5"`），
但 `self.llm` 这个 client 实际指向的是 DeepSeek 端点，DeepSeek 没有这些模型名。

**解决**
别写死模型名，读环境变量，和主 agent 保持一致：
```python
model=os.getenv("ANTHROPIC_MODEL", "deepseek-chat")
```

**涉及文件**
`memory/long_term_memory.py`（`_generate_hypothesis`、`_rerank`）

---

## 3. DashScope 报 `Arrearage` / Access denied

**现象**
```
openai.BadRequestError: 400 - code: 'Arrearage'
'Access denied, please make sure your account is in good standing'
```

**原因**
用阿里通义（DashScope）做 embedding 时，**当前账号对所请求的模型没有权限**
（或欠费/未开通）。注意：错误码写着 `Arrearage`（欠费），但实际也可能是
**该模型未对你的账号授权**——报错信息有误导性。

**解决**
1. 去[阿里云百炼控制台](https://bailian.console.aliyun.com)确认账号已开通、无欠费；
2. 把 `.env` 的 `EMBEDDING_MODEL` 换成**你账号确实有权限的模型**
   （例：本项目可用 `qwen3.7-text-embedding`，`text-embedding-v3` 无权限）。

**涉及文件**
`.env`（`EMBEDDING_MODEL`）

---

## 4. `.env` 里 `EMBEDDING_MODEL` 改了不生效

**现象**
改了 `.env` 的 `EMBEDDING_MODEL`，实际请求还是用旧模型名。

**原因**
构造函数参数给了**非空默认值**，导致 `or` 短路：
```python
def __init__(self, ..., embed_model="text-embedding-3-small"):
    self.embed_model = embed_model or os.getenv("EMBEDDING_MODEL", "...")
```
`embed_model` 永远是真值 → `or` 后半段（读环境变量）永远不执行。

**解决**
默认值改成 `None`，让环境变量能兜底：
```python
def __init__(self, ..., embed_model=None):
    self.embed_model = embed_model or os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
```

**涉及文件**
`memory/long_term_memory.py`（`RAGPipeline.__init__`）

---

## 5. `KeyError: 'answer'`

**现象**
拼接历史对话准备注入 System Prompt 时：
```
KeyError: 'answer'
```
出现在 `m['metadata']['answer']` 这类取值。

**原因**
检索结果 `m` 的 `metadata` 里根本没有 `answer` 键
（存的时候 metadata 只放了 `{"type": "conversation"}`）。
而且完整问答其实已经在 `m['text']` 里了（存入时就是 `f"问: {q}\n答: {a}"`），
不需要再去 metadata 找。

**解决**
直接用 `m['text']`：
```python
memories_text = "\n\n".join(m['text'] for m in memories)
```

**涉及文件**
`main.py`（检索结果拼接处）

---

## 6. `KeyError: 'ANTHROPIC_API_KEY'`

**现象**
```
KeyError: 'ANTHROPIC_API_KEY'
```
启动 `main.py` 时崩。

**原因**
`os.environ["ANTHROPIC_API_KEY"]` 要求该变量必须存在，但 `.env` 里
用的是别的名字（如 `DEEPSEEK_API_KEY`），键名对不上。

**解决**
统一键名——`.env` 里用 `ANTHROPIC_API_KEY`，值填 DeepSeek 的 key：
```
ANTHROPIC_API_KEY=sk-xxx
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
```

**涉及文件**
`.env`、`main.py`

---

## 7. `pip install` 装了包却还是 ModuleNotFound

**现象**
`pip install xxx` 之后代码还是找不到包；或 `uv sync` 后包又没了。

**原因**
本项目用 **uv** 管理依赖。`pip install` 装的包不写进 `pyproject.toml`，
`uv sync` 会按 `pyproject.toml` 重建环境，把「计划外」的包删掉。

**解决**
用 `uv add` 代替 `pip install`，它会同时更新 `pyproject.toml` 和 `uv.lock`：
```bash
uv add chromadb openai numpy
```

**涉及文件**
`pyproject.toml`、`uv.lock`

---

## 8. 向量维度不匹配 `expecting dimension of 1024, got 2560`

**现象**
```
chromadb.errors.InvalidArgumentError: Collection expecting embedding with dimension of 1024, got 2560
```
检索时崩，存入却正常。

**原因**
连环坑：HyDE 的 `_generate_hypothesis` 返回了**空字符串**（带思考的模型把
`max_tokens` 全用在 thinking 上，没输出正文，`extract_text` 记了 warning 并返回 ""），
空串拿去 embedding，服务端对空输入返回了**异常维度**的向量（2560），
和库里正常向量（1024）维度不一致 → ChromaDB 报错。

排查线索：`logs/db-agent.log` 里会有
`extract_text 未找到 text 块 (context=hyde, blocks=['thinking'])`。

**解决**
HyDE 返回空时退回用**原始 query** 去检索，绝不拿空串 embedding：
```python
hypo = await self._generate_hypothesis(query)
text_to_embed = hypo if hypo.strip() else query
query_vec = (await self.embed([text_to_embed]))[0]
```
（治本可另调大 `_generate_hypothesis` 的 `max_tokens`，给正文留出空间。）

**涉及文件**
`memory/long_term_memory.py`（`retrieve`）

---

## 9. HyDE 对问候/闲聊短句也触发，白调 LLM 拖慢响应

**现象**
问「你好」「你是谁」「介绍下」这类问候语时，明显卡顿几秒。
`logs/db-agent.log` 里能看到 `HyDE 触发: query='你好，介绍下是？'`——
HyDE 对这种闲聊也调了一次 LLM（还常返回空、退回原 query），纯属浪费。

**原因**
HyDE 的触发条件只看长度：
```python
if len(query) < 20:   # 太糙
```
问候语也是短句 → 被误判成「需要 HyDE」。长度区分不了
「短而有实义（销售最高?）」和「短的闲聊（你好）」。

**解决**
加一道「闲聊」判断，闲聊直接跳过检索 + HyDE（推荐放 `main.py` 检索前）：
```python
GREETING_MARKERS = ("你好", "您好", "hi", "hello", "你是谁", "介绍下",
                    "介绍一下", "自我介绍", "在吗", "谢谢", "再见")

def _is_chitchat(q: str) -> bool:
    q = q.strip().lower()
    return any(m in q for m in GREETING_MARKERS)

# 检索前：
memories = [] if _is_chitchat(user_input) else await long_memory.retrieve(user_input, top_k=3)
```
局限：关键词表脆弱，换个说法（「嗨」「你叫啥」）会漏；
要更稳可上意图分类小模型，但会增延迟，学习阶段用关键词表即可。

**涉及文件**
`main.py`（检索前的闲聊门）、`memory/long_term_memory.py`（`retrieve` 的 HyDE 条件）

---

## 10. `recall` 永远空：旧记忆没有 `user_id`

**现象**
每轮 `vector_memory.recall(user_query)` 注入 System Prompt，但日志里常是
`recall 完成: 返回 0 条`；问「上次那个……」模型找不到向量历史，转而去调
`read_memory`（SQLite）也是空的。Chroma 里其实有十几条对话。

**原因**
两套写入路径的 metadata 不一致：

| 写入方式 | metadata | `recall` 能否命中 |
|----------|----------|-------------------|
| 旧：`RAGPipeline.add_conversation` | `{"type": "conversation"}`，**无 `user_id`** | 否（被 where 滤掉） |
| 新：`VectorMemory.remember` | 含 `user_id` / `memory_type` | 是 |

`recall` 默认 `where={"user_id": "default"}`。旧文档没有这个字段，
Chroma 过滤匹配不到 → 永远返回空。读用 `recall`、写曾用 `add_conversation`
时必踩。

**解决**
1. **读侧兼容**：`recall` 带 `user_id` 过滤若 0 命中（或 Chroma 抛错），
   自动退回不过滤再搜一次；日志会打
   `user_id 过滤无命中，退回不过滤（兼容旧数据）`。
2. **写侧统一**：新对话用 `remember()`（带 `user_id`），不要再用
   `add_conversation` 写同一 collection，避免继续制造无 `user_id` 的脏数据。
3. （可选）清库重建：删掉 `memory/chroma_db/` 后只走 `remember`，元数据一致。

**涉及文件**
`memory/vector_store.py`（`recall`）、`main.py`（`remember` / `recall`）、
`memory/long_term_memory.py`（旧 `add_conversation`）

---

> 新踩到坑就往这里加一条，格式照旧：现象 → 原因 → 解决 → 涉及文件。
