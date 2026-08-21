"""Hive / Impala (Hue) 语法检索 Tool。

用 @tool 装饰器——对比手动写 JSON Schema dict：
  之前：每个 Tool ~30 行（schema dict 25 行 + 函数 5 行）
  现在：每个 Tool ~15 行，改参数只改函数签名，schema 自动跟。

search_hive_syntax：知识检索——HiveQL 是完整 SQL 方言，LLM 负责合成，Tool 查语法模板。
"""

from harness.tools import tool

# ═══════════════════════════════════════════════════════════════════════════════
# Hive / Impala 语法模板
# ═══════════════════════════════════════════════════════════════════════════════

_HIVE_SYNTAX = {
    "select": {
        "title": "SELECT 查询",
        "hive": (
            "SELECT [/*+ STREAMTABLE(a) */] col1, col2, ...\n"
            "FROM table_name [PARTITION (dt='2025-01-01')]\n"
            "[WHERE conditions]\n"
            "[GROUP BY col]\n"
            "[HAVING conditions]\n"
            "[ORDER BY col [ASC|DESC]]\n"
            "[LIMIT n]"
        ),
        "impala": (
            "SELECT [STRAIGHT_JOIN] col1, col2, ...\n"
            "FROM table_name\n"
            "[WHERE conditions]\n"
            "[GROUP BY col]\n"
            "[HAVING conditions]\n"
            "[ORDER BY col [ASC|DESC]]\n"
            "[LIMIT n [OFFSET m]]"
        ),
        "notes": "Hive: 用 PARTITION 子句做分区裁剪。Impala: 不支持 PARTITION 语法，用 WHERE dt='...' 代替。",
        "dialect_specific": {
            "impala": "Impala 支持 OFFSET + LIMIT 分页；Hive 仅 LIMIT。Impala 支持 STRAIGHT_JOIN 提示。",
        },
    },
    "create_table": {
        "title": "CREATE TABLE 建表",
        "hive": (
            "CREATE [EXTERNAL] TABLE table_name (\n"
            "  col1 TYPE COMMENT '注释',\n"
            "  col2 TYPE\n"
            ")\n"
            "[PARTITIONED BY (dt STRING, hr STRING)]\n"
            "[CLUSTERED BY (col) [SORTED BY (col)] INTO n BUCKETS]\n"
            "[ROW FORMAT DELIMITED\n"
            "  FIELDS TERMINATED BY '\\t'\n"
            "  LINES TERMINATED BY '\\n']\n"
            "[STORED AS PARQUET|ORC|TEXTFILE|AVRO]\n"
            "[LOCATION '/path/to/data']\n"
            "[TBLPROPERTIES ('key'='value')]"
        ),
        "impala": (
            "CREATE [EXTERNAL] TABLE table_name (\n"
            "  col1 TYPE COMMENT '注释',\n"
            "  col2 TYPE\n"
            ")\n"
            "[PARTITIONED BY (dt STRING, hr STRING)]\n"
            "[ROW FORMAT DELIMITED\n"
            "  FIELDS TERMINATED BY '\\t']\n"
            "[STORED AS PARQUET|TEXTFILE|KUDU]\n"
            "[LOCATION '/path/to/data']"
        ),
        "notes": "EXTERNAL 表删除时不删数据文件。STORED AS PARQUET 推荐用于分析场景（列存 + 压缩）。",
        "dialect_specific": {
            "impala": "Impala 不支持 CLUSTERED BY。Impala 支持 Kudu 存储引擎。Hive 的 ORC 格式 Impala 不完全支持。",
        },
    },
    "insert": {
        "title": "INSERT 插入数据",
        "hive": (
            "-- 追加\n"
            "INSERT INTO TABLE table_name [PARTITION (dt='2025-01-01')]\n"
            "SELECT ...\n\n"
            "-- 覆盖（替换分区数据）\n"
            "INSERT OVERWRITE TABLE table_name [PARTITION (dt='2025-01-01')]\n"
            "SELECT ..."
        ),
        "impala": (
            "-- 追加\n"
            "INSERT INTO TABLE table_name [PARTITION (dt='2025-01-01')]\n"
            "SELECT ...\n\n"
            "-- 覆盖\n"
            "INSERT OVERWRITE TABLE table_name [PARTITION (dt='2025-01-01')]\n"
            "SELECT ...\n\n"
            "-- Upsert（仅 Kudu 表）\n"
            "UPSERT INTO table_name VALUES (...)"
        ),
        "notes": "动态分区: SET hive.exec.dynamic.partition.mode=nonstrict; 然后省略 PARTITION 子句中的值。",
        "dialect_specific": {
            "impala": "Impala 的 INSERT OVERWRITE 只覆盖指定分区（不像 Hive 可能影响整表）。Impala 支持 UPSERT（Kudu 表）。",
        },
    },
    "window_function": {
        "title": "窗口函数",
        "hive": (
            "SELECT col,\n"
            "  ROW_NUMBER() OVER (PARTITION BY dept ORDER BY amount DESC) AS rn,\n"
            "  RANK() OVER (PARTITION BY dept ORDER BY amount DESC) AS rank,\n"
            "  SUM(amount) OVER (PARTITION BY dept) AS dept_total,\n"
            "  LAG(col, 1, 0) OVER (ORDER BY dt) AS prev_val\n"
            "FROM table_name"
        ),
        "impala": (
            "SELECT col,\n"
            "  ROW_NUMBER() OVER (PARTITION BY dept ORDER BY amount DESC) AS rn,\n"
            "  RANK() OVER (PARTITION BY dept ORDER BY amount DESC) AS rank,\n"
            "  SUM(amount) OVER (PARTITION BY dept) AS dept_total,\n"
            "  LAG(col, 1, 0) OVER (ORDER BY dt) AS prev_val\n"
            "FROM table_name"
        ),
        "notes": "ROW_NUMBER/RANK/DENSE_RANK/LAG/LEAD/SUM/AVG/COUNT/MIN/MAX 均支持。",
        "dialect_specific": {
            "impala": "Impala 要求窗口函数中 ORDER BY 不能省略（与 Hive 不同）。Impala 不支持 ROWS BETWEEN 子句。",
        },
    },
    "lateral_view": {
        "title": "LATERAL VIEW 展开复杂类型",
        "hive": (
            "SELECT base_col, exploded_col\n"
            "FROM table_name\n"
            "LATERAL VIEW explode(array_col) t AS exploded_col\n"
            "LATERAL VIEW OUTER explode(array_col) t2 AS e2  -- OUTER: 保留 null 行"
        ),
        "impala": (
            "SELECT base_col, item\n"
            "FROM table_name, table_name.array_col AS item\n"
            "-- 或子查询 UNNEST"
        ),
        "notes": "Hive LATERAL VIEW explode 展开 ARRAY/MAP。Impala 用不同的语法。",
        "dialect_specific": {
            "impala": "Impala 不支持 LATERAL VIEW。展开 ARRAY 用 FROM t, t.arr AS item 或 UNNEST。",
        },
    },
    "compute_stats": {
        "title": "COMPUTE STATS 统计信息",
        "hive": "ANALYZE TABLE table_name [PARTITION (dt)] COMPUTE STATISTICS;",
        "impala": "COMPUTE STATS table_name;\nCOMPUTE INCREMENTAL STATS table_name;\nSHOW TABLE STATS table_name;",
        "notes": "统计信息对查询优化至关重要——CBO（代价优化器）依赖它选 JOIN 顺序。",
        "dialect_specific": {
            "impala": "Impala 有 COMPUTE STATS / COMPUTE INCREMENTAL STATS。Hive 用 ANALYZE TABLE。",
        },
    },
    "show_partitions": {
        "title": "查看分区",
        "hive": "SHOW PARTITIONS table_name;",
        "impala": "SHOW PARTITIONS table_name;",
        "notes": "Hive 和 Impala 语法相同。分区很多时用: SHOW PARTITIONS table_name PARTITION(dt>='2025-01-01')。",
    },
    "explain": {
        "title": "EXPLAIN 执行计划",
        "hive": "EXPLAIN [EXTENDED|DEPENDENCY|AUTHORIZATION] SELECT ...",
        "impala": "EXPLAIN SELECT ...\nSUMMARY;  -- 在执行后显示详细统计",
        "notes": "分析 JOIN 顺序、分区裁剪、数据量预估。瓶颈通常标注为 'EXCHANGE'（数据 shuffle）。",
    },
    "join": {
        "title": "JOIN 语法",
        "hive": (
            "SELECT /*+ MAPJOIN(b) */ a.col, b.col\n"
            "FROM large_table a\n"
            "JOIN small_table b ON a.key = b.key\n"
            "WHERE ...\n\n"
            "-- Hive 支持: INNER / LEFT OUTER / RIGHT OUTER / FULL OUTER / LEFT SEMI / CROSS"
        ),
        "impala": (
            "SELECT [STRAIGHT_JOIN] a.col, b.col\n"
            "FROM large_table a\n"
            "JOIN small_table b ON a.key = b.key\n"
            "WHERE ...\n\n"
            "-- Impala 支持: INNER / LEFT OUTER / RIGHT OUTER / FULL OUTER / LEFT SEMI / CROSS / LEFT ANTI"
        ),
        "notes": "大表 JOIN 小表：用 MAPJOIN 提示（Hive）或 STRAIGHT_JOIN（Impala）把大表放前面。",
        "dialect_specific": {
            "impala": "Impala 支持 LEFT ANTI JOIN（不存在于 Hive）。Impala 的 BROADCAST hint 隐式做 mapjoin。",
        },
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# Tool 定义
# ═══════════════════════════════════════════════════════════════════════════════

@tool(description=(
    "检索 Hive / Impala（Hue 查询编辑器）的 SQL 语法模板。\n"
    "HiveQL 是 SQL 方言，含 Hadoop 特有语法（PARTITIONED BY、LATERAL VIEW explode、"
    "STORED AS PARQUET/ORC、MAPJOIN 提示等）。\n"
    "当用户需要编写 Hive 或 Impala 查询时，先用此 Tool 获取正确的语法模板。\n"
    "返回 JSON: {results: [{title, syntax, notes, dialect_specific}], dialect, hint}。\n"
    "query_type 不匹配时返回可用类型列表。"
))
def search_hive_syntax(
    query_type: str,
    table_references: str = "",
    dialect: str = "hive",
) -> dict:
    """query_type: 查询类型关键词（select/insert/create_table/window_function/lateral_view/compute_stats/show_partitions/explain/join）
    table_references: 涉及的表名，逗号分隔（用于查找表结构，可选）
    dialect: 目标引擎 hive 或 impala（默认 hive）"""
    d = (dialect or "hive").strip().lower()
    if d not in ("hive", "impala"):
        return {"error": True, "message": f"不支持的 dialect: '{dialect}'", "suggestion": "仅支持 hive 或 impala"}

    qt = (query_type or "").strip().lower()
    if not qt:
        return {
            "error": True,
            "message": "缺少 query_type 参数",
            "suggestion": f"可用类型: {', '.join(sorted(_HIVE_SYNTAX.keys()))}",
        }

    entry = _HIVE_SYNTAX.get(qt)
    if not entry:
        for key in _HIVE_SYNTAX:
            if key in qt or qt in key:
                entry = _HIVE_SYNTAX[key]
                qt = key
                break

    if not entry:
        return {
            "results": [],
            "count": 0,
            "dialect": d,
            "hint": f"未匹配 '{query_type}'。可用类型: {', '.join(sorted(_HIVE_SYNTAX.keys()))}",
        }

    syntax_text = entry.get(d) or entry.get("hive", "")
    if isinstance(syntax_text, dict):
        syntax_text = syntax_text.get(d, str(syntax_text))

    result = {
        "title": entry["title"],
        "syntax": syntax_text,
        "notes": entry.get("notes", ""),
    }

    ds = entry.get("dialect_specific", {})
    if d in ds:
        result["dialect_specific"] = ds[d]
    other = "impala" if d == "hive" else "hive"
    if other in ds:
        result["other_dialect_note"] = f"{other}: {ds[other]}"

    return {
        "results": [result],
        "count": 1,
        "dialect": d,
        "hint": None,
    }
