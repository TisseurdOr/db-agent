"""结果分析 Tool：把 run_query 的原始行转成业务洞察 + 可视化建议。"""

ANALYZE_RESULTS_TOOL = {
    "name": "analyze_results",
    "description": (
        "分析 run_query 返回的查询结果，生成排名、汇总和可视化建议。"
        "在拿到查询数据后调用，帮助用业务语言向用户解释结果。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "description": "run_query 返回的 rows 列表",
                "items": {"type": "object"},
            },
            "metric_column": {
                "type": "string",
                "description": "要分析的数值列名，例如 sales、total",
            },
            "label_column": {
                "type": "string",
                "description": "分组/标签列名，例如 name、status",
            },
            "title": {
                "type": "string",
                "description": "分析标题，例如「上周各部门销售额排名」",
            },
        },
        "required": ["rows", "metric_column", "label_column"],
    },
}


def analyze_results(
    rows: list[dict],
    metric_column: str,
    label_column: str,
    title: str = "查询结果分析",
) -> dict:
    if not rows:
        return {
            "title": title,
            "insight": "查询结果为空，没有可分析的数据。",
            "ranking": [],
            "chart_suggestion": None,
        }

    # 校验列是否存在
    sample = rows[0]
    if metric_column not in sample:
        return {"error": f"找不到数值列 '{metric_column}'", "available": list(sample.keys())}
    if label_column not in sample:
        return {"error": f"找不到标签列 '{label_column}'", "available": list(sample.keys())}

    # 转成可排序的 (label, value) 列表
    items = []
    for row in rows:
        try:
            value = float(row[metric_column] or 0)
        except (TypeError, ValueError):
            continue
        items.append({"label": row[label_column], "value": value})

    if not items:
        return {"error": f"列 '{metric_column}' 无法解析为数值"}

    # 按数值降序排名
    items.sort(key=lambda x: x["value"], reverse=True)
    total = sum(x["value"] for x in items)

    ranking = []
    for i, item in enumerate(items, start=1):
        pct = (item["value"] / total * 100) if total else 0
        ranking.append({
            "rank": i,
            "label": item["label"],
            "value": item["value"],
            "formatted": f"¥{item['value']:,.0f}",
            "share_pct": round(pct, 1),
        })

    top = ranking[0]
    insight = (
        f"{title}：共 {len(ranking)} 项，合计 ¥{total:,.0f}。"
        f"第一名是 {top['label']}（{top['formatted']}，占比 {top['share_pct']}%）。"
    )
    if len(ranking) >= 2:
        gap = ranking[0]["value"] - ranking[1]["value"]
        insight += f" 领先第二名 ¥{gap:,.0f}。"

    # 根据数据形状给图表建议
    chart_suggestion = _suggest_chart(len(ranking), metric_column, label_column)

    return {
        "title": title,
        "insight": insight,
        "ranking": ranking,
        "total": total,
        "total_formatted": f"¥{total:,.0f}",
        "chart_suggestion": chart_suggestion,
    }


def _suggest_chart(n: int, metric: str, label: str) -> dict:
    """按结果行数推荐图表类型（给 Agent 写回复时参考）。"""
    if n <= 1:
        return {"type": "kpi", "reason": "单值结果，适合用大数字 KPI 展示"}
    if n <= 8:
        return {
            "type": "bar",
            "x": label,
            "y": metric,
            "reason": "类别不多，横向/纵向柱状图最清晰",
        }
    return {
        "type": "bar",
        "x": label,
        "y": metric,
        "reason": "类别较多，建议只展示 Top 10 柱状图，其余归入「其他」",
        "limit": 10,
    }
