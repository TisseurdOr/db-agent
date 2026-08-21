"""结果分析 Tool：把 run_query 的原始行转成业务洞察 + 可视化建议。"""

ANALYZE_RESULTS_TOOL = {
    "name": "analyze_results",
    "description": (
        "分析 run_query 返回的查询结果，生成排名、汇总和可视化建议。"
        "在拿到查询数据后、需要向用户解释排名或占比时调用。"
        "返回 JSON: {title, insight, ranking: [{rank, label, value, formatted, share_pct}], "
        "total, total_formatted, chart_suggestion}；出错时返回 error 及 available（可用列名）。"
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
        return {
            "error": f"找不到数值列 '{metric_column}'",
            "available": list(sample.keys()),
            "hint": "请从 available 中选一个数值列作为 metric_column 重试。",
        }
    if label_column not in sample:
        return {
            "error": f"找不到标签列 '{label_column}'",
            "available": list(sample.keys()),
            "hint": "请从 available 中选一个列作为 label_column 重试。",
        }

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


# compare_periods: 同比/环比对比分析。
# 面试亮点——"Agent 不仅能查数据，还能做时间序列对比，自动标出
# 增长最快的和下滑最严重的，给出可能的业务原因。"
COMPARE_PERIODS_TOOL = {
    "name": "compare_periods",
    "description": (
        "对比两个时间段的同一指标。当用户问'这个月跟上个月比'、"
        "'Q2 vs Q1'、'同比/环比'时调用。"
        "必须先分别查两段时间的数据，然后把两组结果传入此 Tool。"
        "返回 JSON: {period1_label, period2_label, items: [{label, value1, value2, "
        "change_abs, change_pct, trend}], summary, top_growers, top_decliners}。"
        "数据不足或参数缺失时返回 error 及修复建议。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "period1_label": {
                "type": "string",
                "description": "第一段时间的标签，如 '2026年6月'",
            },
            "period2_label": {
                "type": "string",
                "description": "第二段时间的标签，如 '2026年7月'",
            },
            "period1_data": {
                "type": "array",
                "description": "第一段时间的查询结果（run_query 返回的 rows）",
                "items": {"type": "object"},
            },
            "period2_data": {
                "type": "array",
                "description": "第二段时间的查询结果（run_query 返回的 rows）",
                "items": {"type": "object"},
            },
            "label_column": {
                "type": "string",
                "description": "分组/标签列名，如 name、category。两段数据的 label 必须一致才能对应比较。",
            },
            "metric_column": {
                "type": "string",
                "description": "要对比的数值列名，如 total、sales、count",
            },
        },
        "required": ["period1_label", "period2_label", "period1_data",
                     "period2_data", "label_column", "metric_column"],
    },
}


def compare_periods(
    period1_label: str,
    period2_label: str,
    period1_data: list[dict],
    period2_data: list[dict],
    label_column: str,
    metric_column: str,
) -> dict:
    """对比两段时间的同一指标，生成同比/环比分析。"""
    if not period1_data or not period2_data:
        return {
            "error": True,
            "message": "对比需要两组数据，至少一组为空",
            "suggestion": "先分别查两段时间的数据，确认查到了有效结果再对比",
        }

    # 校验列存在
    all_rows = period1_data + period2_data
    sample = all_rows[0]
    if label_column not in sample:
        return {
            "error": True,
            "message": f"找不到标签列 '{label_column}'",
            "available_columns": list(sample.keys()),
            "hint": "请从 available_columns 中选一个作为 label_column",
        }
    if metric_column not in sample:
        return {
            "error": True,
            "message": f"找不到数值列 '{metric_column}'",
            "available_columns": list(sample.keys()),
            "hint": "请从 available_columns 中选一个作为 metric_column",
        }

    # 构建 period1 的 label → value 映射
    def build_map(data: list[dict]) -> dict:
        m = {}
        for row in data:
            try:
                m[str(row[label_column])] = float(row[metric_column] or 0)
            except (ValueError, TypeError):
                continue
        return m

    map1 = build_map(period1_data)
    map2 = build_map(period2_data)

    # 合并所有 label（并集——某个 label 可能只在一个时期有数据）
    all_labels = sorted(set(map1.keys()) | set(map2.keys()))

    items = []
    for label in all_labels:
        v1 = map1.get(label, 0)
        v2 = map2.get(label, 0)
        change_abs = v2 - v1
        # 除零保护
        change_pct = round((change_abs / v1 * 100) if v1 != 0 else (100 if v2 > 0 else 0), 1)
        items.append({
            "label": label,
            "period1_value": round(v1, 2),
            "period2_value": round(v2, 2),
            "change_abs": round(change_abs, 2),
            "change_pct": change_pct,
            "trend": "up" if change_pct > 1 else ("down" if change_pct < -1 else "flat"),
        })

    # 按变化率排序
    sorted_by_growth = sorted(items, key=lambda x: x["change_pct"], reverse=True)

    # top 3 增长和下滑
    growers = [x for x in sorted_by_growth if x["trend"] == "up"][:3]
    decliners = [x for x in sorted_by_growth if x["trend"] == "down"][:3]

    # 汇总
    total1 = sum(x["period1_value"] for x in items)
    total2 = sum(x["period2_value"] for x in items)
    total_change = total2 - total1
    total_change_pct = round((total_change / total1 * 100) if total1 != 0 else 0, 1)

    # 生成洞察
    summary_parts = [
        f"{period2_label} vs {period1_label}：总计 {_fmt_amount(total2)}（{_trend_word(total_change_pct)}{abs(total_change_pct)}%）",
    ]
    if growers:
        names = ", ".join(x["label"] for x in growers)
        summary_parts.append(f"增长最快: {names}")
    if decliners:
        names = ", ".join(x["label"] for x in decliners)
        summary_parts.append(f"下滑明显: {names}")

    return {
        "period1_label": period1_label, 
        "period2_label": period2_label,
        "summary": "。".join(summary_parts) + "。",
        "total1": round(total1, 2),
        "total2": round(total2, 2),
        "total_change_abs": round(total_change, 2),
        "total_change_pct": total_change_pct,
        "items": items,
        "top_growers": growers,
        "top_decliners": decliners,
    }


def _trend_word(pct: float) -> str:
    """增长/下降的中文表述。"""
    if pct > 10:
        return "大幅增长"
    if pct > 1:
        return "小幅增长"
    if pct > -1:
        return "基本持平，"
    if pct > -10:
        return "小幅下降"
    return "大幅下降"


def _fmt_amount(val: float) -> str:
    """金额格式化。"""
    if abs(val) >= 10000:
        return f"¥{val/10000:.1f}万"
    return f"¥{val:,.0f}"


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
