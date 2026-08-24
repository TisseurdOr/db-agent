"""指标口径注册中心 + 口径答疑 Tool。

解决「GMV 怎么算的」「销售额包含退款吗」「这个指标口径是什么」这类重复问题。
原型 5（口径答疑型 Agent）的工具底座——实现难度最低、ROI 最高的 Data Agent 方向。

背景：
"20-30% 的数据团队时间花在回答口径问题上。指标字典 + LLM 检索，
让业务方自助查口径，把数据工程师从重复答疑中解放出来。"
"""

from harness.tools import tool

# ═══════════════════════════════════════════════════════════════════════════════
# 指标口径字典
# ═══════════════════════════════════════════════════════════════════════════════

_METRICS = {
    "GMV": {
        "name": "GMV（总交易额）",
        "aliases": ["gmv", "交易总额", "成交额", "总销售额", "总交易额"],
        "definition": "统计周期内所有成功下单的订单金额总和，无论是否支付、是否退款。",
        "formula": "SUM(orders.total) WHERE status NOT IN ('cancelled')",
        "related_tables": ["orders"],
        "related_columns": ["orders.total", "orders.status", "orders.created_at"],
        "notes": "含未支付和已退款订单。如需剔除退款，用'净GMV'口径。",
        "owner": "数据平台组",
    },
    "净GMV": {
        "name": "净GMV（实收交易额）",
        "aliases": ["净gmv", "实收", "净销售额", "实收交易额"],
        "definition": "GMV 扣除退款后的实际收入。",
        "formula": "SUM(orders.total) WHERE status = 'completed'",
        "related_tables": ["orders"],
        "related_columns": ["orders.total", "orders.status"],
        "notes": "只计 completed 状态。仅退款不退货的情况需单独确认业务口径。",
        "owner": "数据平台组",
    },
    "DAU": {
        "name": "DAU（日活跃用户数）",
        "aliases": ["dau", "日活", "日活跃", "日活跃用户"],
        "definition": "统计日当天至少有一次有效行为的独立用户数。",
        "formula": "COUNT(DISTINCT user_id) WHERE event_date = target_date AND event_type IN ('login', 'browse', 'click', 'purchase')",
        "related_tables": ["user_events"],
        "related_columns": ["user_events.user_id", "user_events.event_type", "user_events.event_date"],
        "notes": "有效行为定义可能因产品而异。打开 APP 即算 vs 有交互才算，需对齐业务方。",
        "owner": "增长分析组",
    },
    "MAU": {
        "name": "MAU（月活跃用户数）",
        "aliases": ["mau", "月活", "月活跃", "月活跃用户"],
        "definition": "统计月内至少有一次有效行为的独立用户数。注意 DAU 之和 ≠ MAU。",
        "formula": "COUNT(DISTINCT user_id) WHERE event_month = target_month AND event_type IN ('login', 'browse', 'click', 'purchase')",
        "related_tables": ["user_events"],
        "related_columns": ["user_events.user_id", "user_events.event_type", "user_events.event_date"],
        "notes": "跨月去重，不是每日 DAU 累加。",
        "owner": "增长分析组",
    },
    "留存率": {
        "name": "次日/7日/30日留存率",
        "aliases": ["留存", "留存率", "次日留存", "7日留存", "用户留存"],
        "definition": "某日新增用户中，第 N 天后仍活跃的用户占比。",
        "formula": "第 N 天活跃用户数 / 首日新增用户数 × 100%",
        "related_tables": ["user_events"],
        "related_columns": ["user_events.user_id", "user_events.event_date", "user_events.event_type"],
        "notes": "需用户注册/激活日期表。计算时注意自然日 vs 24小时的差异。",
        "owner": "增长分析组",
    },
    "客单价": {
        "name": "客单价（ARPU / AOV）",
        "aliases": ["客单价", "平均订单金额", "arpu", "aov", "人均消费"],
        "definition": "每个付费用户的平均消费金额，或每笔订单的平均金额（看口径）。",
        "formula": "人均: SUM(total) / COUNT(DISTINCT customer_id) WHERE status='completed'\n订单均: SUM(total) / COUNT(*) WHERE status='completed'",
        "related_tables": ["orders"],
        "related_columns": ["orders.total", "orders.customer_id", "orders.status"],
        "notes": "AOV（Average Order Value）= 订单均；ARPU（Average Revenue Per User）= 人均。面试时要分清。",
        "owner": "商业分析组",
    },
    "转化率": {
        "name": "转化率（CVR）",
        "aliases": ["转化率", "cvr", "下单转化率", "支付转化率"],
        "definition": "从浏览到下单（或支付）的用户比例。",
        "formula": "下单用户数 / 浏览用户数 × 100%",
        "related_tables": ["user_events"],
        "related_columns": ["user_events.user_id", "user_events.event_type", "user_events.event_time"],
        "notes": "需定义转化窗口（如浏览后 24h 内下单）。漏斗每步分母不同，说'转化率'时要明确是哪一步。",
        "owner": "增长分析组",
    },
    "退货率": {
        "name": "退货率",
        "aliases": ["退货率", "退款率", "退单率"],
        "definition": "已发货订单中被退货的比例。",
        "formula": "退货订单数 / 已发货订单数 × 100%",
        "related_tables": ["orders"],
        "related_columns": ["orders.status"],
        "notes": "分子是退货申请数还是退货完成数？分母含不含未发货取消？口径对齐是关键。",
        "owner": "供应链分析组",
    },
    "库存周转天数": {
        "name": "库存周转天数",
        "aliases": ["库存周转", "周转天数", "库存天数"],
        "definition": "平均库存售完所需天数，衡量库存效率。",
        "formula": "平均库存成本 / 日均销售成本 × 天数",
        "related_tables": ["products", "orders"],
        "related_columns": ["products.stock_quantity", "products.cost", "orders.total", "orders.created_at"],
        "notes": "需库存表和销售表 JOIN。季节性商品需注意时间窗口选择。",
        "owner": "供应链分析组",
    },
    "毛利率": {
        "name": "毛利率",
        "aliases": ["毛利率", "毛利", "gross margin"],
        "definition": "销售收入扣除直接成本后的利润占比。",
        "formula": "(销售收入 - 销售成本) / 销售收入 × 100%",
        "related_tables": ["orders", "products"],
        "related_columns": ["orders.total", "products.cost"],
        "notes": "只含直接成本（COGS），不含运营费用。按产品线/渠道拆分时注意成本分摊逻辑。",
        "owner": "财务分析组",
    },
    "复购率": {
        "name": "复购率",
        "aliases": ["复购率", "回购率", "复购", "回头客"],
        "definition": "统计周期内购买 ≥2 次的用户占比。",
        "formula": "购买 ≥2 次的用户数 / 总购买用户数 × 100%",
        "related_tables": ["orders"],
        "related_columns": ["orders.customer_id", "orders.created_at"],
        "notes": "统计窗口通常为月/季度。新客和老客的复购率应分开看。",
        "owner": "商业分析组",
    },
    "LTV": {
        "name": "LTV（用户生命周期价值）",
        "aliases": ["ltv", "生命周期价值", "用户价值", "clv"],
        "definition": "一个用户从注册到流失期间贡献的总毛利。",
        "formula": "平均客单价 × 平均购买频次 × 平均留存周期 × 毛利率",
        "related_tables": ["orders", "user_events"],
        "related_columns": ["orders.customer_id", "orders.total", "user_events.event_date"],
        "notes": "LTV 是预估指标，不同业务阶段用不同模型（历史法 / 预测法 / 同类用户类比法）。",
        "owner": "商业分析组",
    },
    "CAC": {
        "name": "CAC（用户获取成本）",
        "aliases": ["cac", "获客成本", "用户获取成本"],
        "definition": "获取一个新用户/客户的平均市场费用。",
        "formula": "总市场费用 / 新获取用户数",
        "related_tables": [],  # 通常来自财务/市场系统
        "related_columns": [],
        "notes": "LTV/CAC ≥ 3 是 SaaS 行业健康基准。注意渠道归因——不同渠道 CAC 差异大。",
        "owner": "商业分析组",
    },
    "销售额": {
        "name": "销售额",
        "aliases": ["销售额", "销售", "营收", "收入"],
        "definition": "统计周期内所有成功订单的金额总和（成功=已支付且未退款）。",
        "formula": "SUM(orders.total) WHERE status = 'completed'",
        "related_tables": ["orders"],
        "related_columns": ["orders.total", "orders.status", "orders.created_at"],
        "notes": "与 GMV 的区别：销售额只含已完成订单，GMV 含未支付。面试常考这个区别。",
        "owner": "财务分析组",
    },
    "订单量": {
        "name": "订单量",
        "aliases": ["订单量", "订单数", "下单量"],
        "definition": "统计周期内创建的订单总数（不含已取消）。",
        "formula": "COUNT(*) WHERE status != 'cancelled'",
        "related_tables": ["orders"],
        "related_columns": ["orders.id", "orders.status", "orders.created_at"],
        "notes": "下单量 ≠ 成交量。下单量含 pending，成交量只含 completed。",
        "owner": "商业分析组",
    },
}

# 构建别名→主名索引
_ALIAS_INDEX = {}
for key, entry in _METRICS.items():
    for alias in entry.get("aliases", []):
        _ALIAS_INDEX[alias] = key


# ═══════════════════════════════════════════════════════════════════════════════
# Tool
# ═══════════════════════════════════════════════════════════════════════════════

@tool(description=(
    "查询指标口径定义。当用户问「XX 怎么算的」「XX 指标包含什么」「XX 的口径是什么」"
    "「GMV 包含退款吗」这类问题时调用。返回指标的完整定义、计算公式、关联表和字段、注意事项。"
    "返回 {found: true, metric: {...}} 或 {found: false, suggestions: [...]}。"
))
def lookup_metric(query: str) -> dict:
    """query: 指标名或别名，如 'GMV'、'销售额'、'DAU'、'转化率'、'客单价'"""
    q = query.strip().lower()

    # 精确别名匹配
    if q in _ALIAS_INDEX:
        return {"found": True, "query": query,
                "metric": _METRICS[_ALIAS_INDEX[q]]}

    # 精确 key 匹配
    if q.upper() in _METRICS:
        return {"found": True, "query": query,
                "metric": _METRICS[q.upper()]}

    # 反向匹配：检查 query 中是否包含任何别名（处理 "GMV怎么算的" 这类自然语言）
    for alias, key in sorted(_ALIAS_INDEX.items(), key=lambda x: -len(x[0])):
        if alias in q:
            return {"found": True, "query": query,
                    "metric": _METRICS[key]}

    # 模糊匹配：按字符重叠打分，需 ≥3 个字符命中才算有效
    suggestions = []
    for key, entry in _METRICS.items():
        score = 0
        for ch in q:
            if ch in key.lower() or any(ch in a for a in entry.get("aliases", [])):
                score += 1
        if score >= 3:
            suggestions.append((key, score))
    suggestions.sort(key=lambda x: -x[1])

    if suggestions:
        best, _ = suggestions[0]
        return {
            "found": True, "query": query,
            "metric": _METRICS[best],
            "also_matched": [k for k, _ in suggestions[1:5]] if len(suggestions) > 1 else [],
        }

    return {
        "found": False, "query": query,
        "available_metrics": [{"key": k, "name": v["name"]} for k, v in _METRICS.items()],
        "hint": f"未找到「{query}」对应的指标口径。以上是所有可用指标及其定义，请选择最接近的。",
    }


def list_all_metrics() -> list:
    """列出所有已注册的指标名。"""
    return [{"key": k, "name": v["name"]} for k, v in _METRICS.items()]
