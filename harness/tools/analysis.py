"""结果分析 Tool：把 run_query 的原始行转成业务洞察 + 可视化建议。"""

from datetime import date, datetime

ANALYZE_RESULTS_TOOL = {
    "name": "analyze_results",
    "description": (
        "分析 run_query 返回的查询结果，生成排名、汇总和可视化建议。"
        "在拿到查询数据后、需要向用户解释排名或占比时调用。"
        "返回 JSON: {title, insight, ranking: [{rank, label, value, formatted, share_pct}], "
        "total, total_formatted, chart_suggestion, field_properties, "
        "suggested_charts: [{type, title, labels, values, reason}]}；"
        "suggested_charts 每项可直接作为 render_chart 单图模式参数。"
        "出错时返回 error 及 available（可用列名）。"
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

    field_properties = _infer_field_properties(rows)

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

    # 根据字段语义推荐多个图型（可直接喂 render_chart）
    suggested_charts = _suggest_charts(field_properties, rows, metric_column, label_column, title)
    chart_suggestion = suggested_charts[0] if suggested_charts else None

    return {
        "title": title,
        "insight": insight,
        "ranking": ranking,
        "total": total,
        "total_formatted": f"¥{total:,.0f}",
        "chart_suggestion": chart_suggestion,
        "field_properties": field_properties,
        "suggested_charts": suggested_charts,
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


MAX_POINTS = 10


def _to_number(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _infer_field_properties(rows: list[dict]) -> dict[str, dict]:
    """推断每列语义类型（number/date/category/string），纯 Python 零 LLM。

    是所有分析能力（排名/洞察/选图）的公共底座：数值列算 min/max/sum，
    日期列算时间范围，类别列算基数，替代让 Agent 自己猜哪列是数值哪列是标签。
    """
    if not rows:
        return {}
    props: dict[str, dict] = {}
    for col in rows[0].keys():
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if not values:
            props[col] = {"dtype": "empty", "nunique": 0}
            continue
        nunique = len(set(map(str, values)))
        nums = [_to_number(v) for v in values]
        if all(n is not None for n in nums):
            props[col] = {
                "dtype": "number",
                "min": min(nums),
                "max": max(nums),
                "sum": sum(nums),
                "nunique": nunique,
            }
            continue
        parsed_dates = [_parse_date(v) for v in values]
        if all(d is not None for d in parsed_dates):
            props[col] = {
                "dtype": "date",
                "min": min(parsed_dates).isoformat(),
                "max": max(parsed_dates).isoformat(),
                "nunique": nunique,
            }
            continue
        props[col] = {
            "dtype": "category" if nunique / len(values) < 0.5 else "string",
            "nunique": nunique,
        }
    return props


def _suggest_charts(props: dict, rows: list[dict], metric_col: str,
                    label_col: str, title: str) -> list[dict]:
    """按字段语义推荐多个图型，每项可直接喂 render_chart（type/title/labels/values）。

    可视化最佳实践映射：日期→折线看趋势；离散类别→柱状对比；类别少→补一张占比饼图。
    单值无图，靠 insight 文字说明即可。
    """
    pairs = []
    for r in rows:
        v = _to_number(r.get(metric_col))
        if v is None:
            continue
        pairs.append((r.get(label_col), v))
    if len(pairs) <= 1:
        return []

    label_dtype = props.get(label_col, {}).get("dtype", "string")
    nunique = props.get(label_col, {}).get("nunique", len(pairs))
    charts: list[dict] = []

    if label_dtype == "date":
        pairs.sort(key=lambda p: _parse_date(p[0]) or datetime.min)
        charts.append({
            "type": "line",
            "title": f"{title} · 趋势",
            "labels": [str(p[0]) for p in pairs],
            "values": [p[1] for p in pairs],
            "reason": "时间序列，折线图看趋势",
        })
    else:
        pairs.sort(key=lambda p: p[1], reverse=True)
        top = pairs[:MAX_POINTS]
        truncated = "（已截取 Top 10）" if len(pairs) > MAX_POINTS else ""
        charts.append({
            "type": "bar",
            "title": f"{title} · 对比",
            "labels": [str(p[0]) for p in top],
            "values": [p[1] for p in top],
            "reason": f"离散类别，柱状图对比{truncated}",
        })
        if 2 <= nunique <= 8:
            charts.append({
                "type": "pie",
                "title": f"{title} · 占比",
                "labels": [str(p[0]) for p in top],
                "values": [p[1] for p in top],
                "reason": "类别较少，饼图看占比",
            })
    return charts
