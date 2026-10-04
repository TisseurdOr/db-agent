# 用 Jev（System One 决策模型）替换 LLM 判断节点

**背景**：项目里有若干「从固定选项里选 + 要一个概率」的判断节点，用自回归 LLM 来做是浪费——慢（2~5s）、贵、还要容错解析 JSON 字符串（`parse_confidence_result` 就是为了兜这个）。

**Jev**：TypeSafe AI 2026-09 发布的 System One 模型。不生成文本，输入「状态 + 类型化问题（选项/等级/是非）」，**并行返回带校准概率的结构化答案**。官方称快/便宜约两个数量级。

**决策**：把 Jev 作为**可选决策 provider** 接入，遵守现有 provider 约定（可换、可降级）：

1. **接入点（只接判断节点，不接生成）**
   - Router 的 Agent 选择（`_route_via_jev`）——options = sql/hbase/hive/strategy/clarify/none
   - 置信度门（`_assess_sql_confidence`）——scale 问题，直接取校准概率，可退休 `parse_confidence_result`
2. **不接**：SQL 生成、分析结论、知识库回答、HITL 文案——Jev 不生成文本
3. **零侵入降级**：未配置 `JEV_API_KEY` → `is_enabled()=False`，完全走原 LLM 路径；调用失败/超时 → 返回 None 回退。**永不因 Jev 不可用而打断主流程**
4. **可测**：HTTP 出口抽成 `_post_json`，测试 monkeypatch 即可，不联网

**接入通道**：优先 OpenRouter（`OPENROUTER_API_KEY` + `typesafe/jev-1.13`）；也可用 TypeSafe 原生 `/decide`。两条通道对调用方透明。

**踩坑记录**：① OpenRouter 上 `typesafe/jev-router` 是**模型路由器**（把请求转发给下游 LLM），给不了校准概率；真正要的是 **`typesafe/jev-1.13`**（decisions 模型，`provider: TypeSafe`）。② decisions 模型**不能**用 `/chat/completions`，必须走 `/api/alpha/decisions`。③ 请求体：`{model, state, questions}`，其中 `questions` 是 **record**（不是数组），每题 `{type, instructions}`，`type ∈ {noul, choice, score}`；`noul` 返回 0~1 概率。④ `noul` 概率**绝对值偏低**，用固定 0.5 阈值会大面积漏选，改用**相对阈值**（≥0.65×最高分 且 ≥0.15）。

**取舍**：Jev 尚未完全开源、需申请 Key，是外部依赖；换来的是判断节点更快更便宜，且**概率是校准的**（LLM 自评分数并不校准）。因此它是「增强」，不是「替换模型」。
