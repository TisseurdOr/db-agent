"""多 Agent 编排（拆分后的兼容入口）。

代码已按职责拆到 helpers / nodes / graph / runner，
本文件只做 re-export，保持 `from harness.orchestration.multi.orchestrator import ...` 不破坏。
"""

from harness.orchestration.multi.agents import (
    analysis_agent,
    data_quality_agent,
    hbase_agent,
    hive_agent,
    sql_agent,
    strategy_agent,
)
from harness.orchestration.multi.cache import RouterCache
from harness.orchestration.multi.graph import build_multi_agent_graph, edge_router
from harness.orchestration.multi.helpers import (
    MAX_REPLAN_ATTEMPTS,
    _maybe_replan,
    _next_step,
)
from harness.orchestration.multi.nodes import (
    node_analysis,
    node_clarify,
    node_confidence_gate,
    node_data_quality,
    node_hbase,
    node_hive,
    node_reflection,
    node_router,
    node_sql,
    node_strategy,
)
from harness.orchestration.multi.runner import MultiAgentRunner

__all__ = [
    "MultiAgentRunner",
    "build_multi_agent_graph",
    "edge_router",
    "node_router",
    "node_clarify",
    "node_data_quality",
    "node_sql",
    "node_confidence_gate",
    "node_strategy",
    "node_hbase",
    "node_hive",
    "node_analysis",
    "node_reflection",
    "MAX_REPLAN_ATTEMPTS",
    "_maybe_replan",
    "_next_step",
    "RouterCache",
    "sql_agent",
    "strategy_agent",
    "analysis_agent",
    "data_quality_agent",
    "hbase_agent",
    "hive_agent",
]
