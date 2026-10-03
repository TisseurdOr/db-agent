"""LangGraph 图用的 State 定义。

- MultiAgentState: orchestrator.py（主路径，--mode multi）
- DBAgentState: 旧课单 Agent 图用；图代码已迁到 archive/legacy_single_agent_graph/
"""

import os
from typing import Annotated, Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import add_messages

# Checkpointer 只保留最近 K 条 messages，防止几千轮后 state/DB 膨胀。
# 原文已在 ConversationManager / turn JSONL / episode 归档；节点侧本来只读 [-6:]。
CHECKPOINT_MESSAGE_LIMIT = int(os.getenv("CHECKPOINT_MESSAGE_LIMIT", "20"))


def add_messages_trim(left: list, right: list) -> list:
    """add_messages 后裁剪到最近 K 条。"""
    merged = list(add_messages(left, right))
    limit = CHECKPOINT_MESSAGE_LIMIT
    if limit > 0 and len(merged) > limit:
        return merged[-limit:]
    return merged


def _merge_stats(left: dict, right: dict) -> dict:
    """累加各节点的 token 和耗时统计。"""
    return {
        "input_tokens": left.get("input_tokens", 0) + right.get("input_tokens", 0),
        "output_tokens": left.get("output_tokens", 0) + right.get("output_tokens", 0),
        "turns": left.get("turns", 0) + right.get("turns", 0),
        "elapsed": left.get("elapsed", 0) + right.get("elapsed", 0),
        "nodes": left.get("nodes", []) + right.get("nodes", []),
    }


# ── 0020: 单 Agent ──

class DBAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    tool_results: list
    sql_to_review: str
    next_step: str


# ── 0023: 多 Agent 编排 ──
# _client / _model 等 _ 前缀键现在走 ConfigurablePayload（agent_config()），
# 不再放 state。

class ConfigurablePayload(TypedDict, total=False):
    """MultiAgentRunner 注入 configurable 的自定义键。

    LangGraph 的 RunnableConfig 不声明这些自定义键，pyright 会报
    "Could not access item in TypedDict"。这里显式声明，访问走 agent_config()。
    """
    thread_id: str
    _client: Any
    _model: str
    _trace: Any
    _router_cache: Any
    _task_manager: Any
    _event_queue: Any


def agent_config(config: RunnableConfig) -> ConfigurablePayload:
    """把 LangGraph RunnableConfig 的 configurable 转成有类型的自定义负载。"""
    return cast(ConfigurablePayload, config.get("configurable") or {})


def require_client(config: RunnableConfig) -> Any:
    """取必填的 _client；缺失即配置错误（避免 TypedDict 可选键访问告警）。"""
    client = agent_config(config).get("_client")
    if client is None:
        raise RuntimeError("RunnableConfig.configurable 缺少 _client")
    return client


class MultiAgentState(TypedDict):
    query: str                  # 用户问题（ainvoke 时写入）
    messages: Annotated[list, add_messages_trim]  # 对话历史（有界 K，防 checkpoint 膨胀）
    _recalled_memories: str     # pre-turn 向量召回的记忆
    _conversation_summary: str  # ConversationManager 压缩的早期对话摘要
    plan: list                  # Router 输出的执行计划
    results: dict               # 各 Agent 中间结果
    final_answer: str           # 最终输出（analysis 写入）
    next: str                   # 下一个节点名（_next_step 写入，edge_router 读取）
    _stats: Annotated[dict, _merge_stats]  # 各节点累计: input_tokens/output_tokens/turns/elapsed/nodes
    _inject_dq: bool            # 是否注入 DataQuality（首轮为 True）
    _reflection_attempts: int   # Reflection 节点重试次数（上限 2）
    _replan_attempts: int       # 失败重规划次数（上限 1——防 router↔agent 死循环）
    _replan_feedback: str       # Agent 失败原因，回喂 Router 重排计划后清空
    _skip_confidence: bool      # Web 交互跳过置信度门
    _skip_reflection: bool      # Web 交互跳过 Reflection
