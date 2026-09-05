"""Agent 名常量：plan 的 agent 字段 / results 的 key / 图节点名三者共用同一字符串。

router / nodes / helpers / graph / agents 都从这里取，改名只改这一处。
"""

AGENT_SQL = "sql"
AGENT_STRATEGY = "strategy"
AGENT_HBASE = "hbase"
AGENT_HIVE = "hive"
AGENT_ANALYSIS = "analysis"
AGENT_DATA_QUALITY = "data_quality"
