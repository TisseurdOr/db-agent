# Fin-Agent Harness 层改进报告

> 日期：2026-06-24  
> 版本：v0.1.0 → v0.1.1  
> 新增代码：~140 行（3 个新文件），修改 3 个现有文件

---

## 一、总体变化

为 fin-agent 添加了 **LLM 执行 Harness 层**——一个薄的基础设施层，位于业务代码与 LLM API 之间，负责**可靠性、可观测性、可维护性**。

```
改进前：  业务代码 ──裸调──→ DeepSeek/通义千问 API
                         └── 网络故障 → 500 崩溃

改进后：  业务代码 ──call_llm()──→ 重试/日志/降级 ──→ LLM API
                              └── 网络故障 → 3次重试 → 仍失败 → 503 + 降级消息
```

---

## 二、新增文件

| 文件 | 职责 | 行数 |
|------|------|------|
| `app/config.py` | 集中化 LLM 工厂——3 个模型的注册表 + 延迟实例化 + 缓存复用 | 67 |
| `app/llm_harness.py` | 核心执行层——`call_llm()` 包装所有 LLM 调用 | 88 |
| `app/observability.py` | 日志配置 + RequestID 中间件 | 44 |

---

## 三、具体改进项

### 1. 可靠性：所有 LLM 调用增加重试机制

**改进前**：7 个 `llm.invoke()` 调用点全部裸调，网络抖动 / API 限流 / 服务暂时不可用 → 未处理异常 → FastAPI 500 Internal Server Error。

**改进后**：全部替换为 `call_llm()`，每次调用最多 4 次尝试（3 次重试），指数退避（1s → 2s → 4s）+ 随机抖动，避免惊群效应。

**影响范围**：
- `graph.py`：analyst、risk、report 三个 LangGraph 节点
- `rag.py`：reranker、query rewrite、HyDE 生成
- `main.py`：/api/chat、/api/askRAG 端点

### 2. 可观测性：从零到有

**改进前**：640 行代码，零个 `logging` 调用。LLM 调用失败时完全不知道哪个节点、什么原因、耗时多少。

**改进后**：
- **每条 LLM 调用**：成功时 INFO 日志（调用方、模型、耗时 ms、内容长度、第几次尝试）
- **重试**：WARNING 日志（错误原因、退避秒数）
- **耗尽重试次数**：ERROR 日志（所有尝试失败后的最终错误）
- **分析任务**：analyze_pdf 开始/结束时 INFO 日志（文件名、文本长度、风险等级、报告长度）
- **HyDE 失败**：之前静默 `except Exception: pass`，现在 WARNING 日志后降级

### 3. 错误处理统一化

**改进前**：三种错误处理风格混用：
- 裸 `llm.invoke()` 无保护 → 未处理异常（7 处）
- `{"error": "..."}` 字符串键判断（graph.py analyze_pdf）
- `except Exception: pass` 静默吞错（rag.py _hyde_generate）

**改进后**：
- `LLMResult` 数据类统一 LLM 调用的成功/失败返回，`call_llm()` 永不抛异常
- `/api/chat` 和 `/api/askRAG` 失败时返回 **503 Service Unavailable** + 错误描述（而非 500 堆栈）
- `_hyde_generate` 失败时 WARNING 日志 → 返回 `""`（行为不变，但可观测）
- 各节点有明确的降级路径（analyst 失败 → 错误 dict → risk 检测到 → 提前返回高危标记）

### 4. 配置集中化：5 个分散的 ChatOpenAI 实例 → 1 个工厂

**改进前**：
- `graph.py`：1 个模块级 `ChatOpenAI`（import 时即构建）
- `rag.py`：1 个 `ChatOpenAI` + 1 个 `OpenAI`（import 时即构建）
- `main.py`：3 个 `ChatOpenAI` 在一个 `MODELS` 字典里（import 时即构建）
- 共 5 个独立实例，API key / base_url / temperature 字符串字面量重复出现

**改进后**：
- `app/config.py` 的 `get_llm(model_name)` 单一入口
- 延迟实例化 + 字典缓存——只有被调用时才构建，同一模型复用同一实例
- 新增模型只需在 `_MODEL_REGISTRY` 加一条，所有模块自动可用
- `max_retries=0`（重试交给 harness 层，不在 HTTP 客户端层重复）

### 5. 健康检查：从空操作变为真正的依赖检测

**改进前**：
```python
@app.get("/api/health")
async def health():
    return {"status": "ok", "models": list(MODELS.keys())}
# 永远返回 ok，即使 ChromaDB 损坏、API key 缺失
```

**改进后**：
- 检测 ChromaDB 连通性（调用 `get_stats()` 验证向量库可读）
- 检测每个模型的 API key 是否配置（非 placeholder）
- 有异常时返回 `{"status": "degraded", "issues": [...]}`
- 可用于 Kubernetes liveness/readiness probe

### 6. 请求追踪

- `RequestIDMiddleware` 从 `X-Request-ID` header 读取或生成 UUID
- 存入 `ContextVar`，任意调用点可通过 `get_request_id()` 获取
- 注入响应 header，实现端到端请求关联
- 第三方库日志（httpx/openai/chromadb）抑制到 WARNING，减少噪声

### 7. 延迟实例化：import 不再触发 HTTP 客户端构建

**改进前**：`import app.graph` 或 `import app.rag` 就会执行 `ChatOpenAI(...)` 构造，即使只是用到 `AnalysisState` 类型也会初始化 HTTP 客户端。API key 缺失时静默替换为 `"sk-placeholder"`。

**改进后**：`get_llm()` 只在第一次调用时构建实例。import 安全。

---

## 四、没有做的（当前阶段属于过度工程化）

| 不做的 | 原因 |
|--------|------|
| tenacity 重试库 | 10 行手动循环完全等价，零新增依赖 |
| OpenTelemetry 分布式追踪 | 640 行项目，结构化日志已足够 |
| `asyncio.to_thread` 异步化 LLM 调用 | 需要重构 pipeline，留到 v0.2.0 |
| Pydantic Settings 配置类 | 只有 4 个 env var |
| 抽象 BaseLLMHarness 基类 | 无第二个实现需要多态 |
| 熔断器/限流器 | 单用户原型，无并发压力 |

---

## 五、修改文件清单

| 文件 | 改动类型 | 改动量 |
|------|---------|--------|
| `app/config.py` | **新增** | 67 行 |
| `app/llm_harness.py` | **新增** | 88 行 |
| `app/observability.py` | **新增** | 44 行 |
| `app/graph.py` | 修改 | +20 / -10 行 |
| `app/rag.py` | 修改 | +25 / -15 行 |
| `app/main.py` | 修改 | +30 / -20 行 |
| **总计** | | **新代码 ~140 行，修改 ~60 行** |

---

## 六、验证结果

```
✅ 所有新模块 import 正常（含缓存复用验证）
✅ /api/health    → {"status": "ok"}（真实检测 ChromaDB + API key）
✅ /api/chat      → LLM 调用通过 harness，返回正常回复
✅ /api/graph     → 三节点 LangGraph 流水线完整
✅ 无效模型名     → 400 + 可用模型列表
✅ LLM 调用失败   → 3 次重试 → 503 + 降级错误消息（不再 500 堆栈）
✅ 结构化日志输出 → 每次 LLM 调用的耗时、状态、调用方可追踪
```
