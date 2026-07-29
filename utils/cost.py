"""成本计算器: 读 logs/traces/ 的 JSONL，按模型价格算 token 成本。

用法:
    python -m utils.cost --today        # 今天
    python -m utils.cost --last 7       # 最近 7 天
    python -m utils.cost --date 2026-07-21  # 指定日期
    python -m utils.cost --all          # 所有历史

价格说明:
    DeepSeek 用人民币 (¥/M tokens)，Claude 用美元 ($/M tokens)。
    输出按模型分别显示，汇率按 1 USD = 7.2 CNY 折算供参考。
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

TRACE_DIR = Path(__file__).resolve().parent.parent / "logs" / "traces"

# 价格表: ¥/M tokens (DeepSeek) 或 $/M tokens (Claude)
MODEL_PRICES: dict[str, dict[str, float]] = {
    "deepseek-chat":      {"input": 1.0,  "output": 4.0,   "currency": "¥"},
    "deepseek-v4-flash":  {"input": 1.0,  "output": 4.0,   "currency": "¥"},
    "deepseek-v4-pro":    {"input": 4.0,  "output": 16.0,  "currency": "¥"},
    "deepseek-reasoner":  {"input": 4.0,  "output": 16.0,  "currency": "¥"},  # legacy
    "claude-haiku":       {"input": 0.80, "output": 4.0,   "currency": "$"},
    "claude-sonnet":      {"input": 3.0,  "output": 15.0,  "currency": "$"},
    "claude-opus":        {"input": 15.0, "output": 75.0,  "currency": "$"},
}

# 如果 trace 没存 model 字段，用这个默认模型算
DEFAULT_MODEL = "deepseek-chat"


def _read_file(filepath: Path) -> list[dict]:
    if not filepath.exists():
        return []
    traces = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return traces


def _traces_for_date(date_str: str) -> list[dict]:
    return _read_file(TRACE_DIR / f"{date_str}.jsonl")


def _traces_for_dates(date_strs: list[str]) -> list[dict]:
    all_traces = []
    for d in date_strs:
        all_traces.extend(_traces_for_date(d))
    return all_traces


def _traces_for_last_days(n: int) -> list[dict]:
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]
    return _traces_for_dates(dates)


def _all_traces() -> list[dict]:
    all_traces = []
    for f in sorted(TRACE_DIR.glob("*.jsonl")):
        all_traces.extend(_read_file(f))
    return all_traces


def calculate(traces: list[dict], model: str = DEFAULT_MODEL) -> dict:
    """按模型汇总 token 消耗和成本。"""
    # model → {"input": N, "output": N, "count": N}
    model_tokens: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0, "count": 0})

    for t in traces:
        # trace 里目前没存 model 字段（0026 的 TraceContext 没记录），
        # 后续加上 model 字段后这里改成 t.get("model", DEFAULT_MODEL)
        m = t.get("model", model)
        totals = t.get("totals", {})
        model_tokens[m]["input"] += totals.get("input_tokens", 0)
        model_tokens[m]["output"] += totals.get("output_tokens", 0)
        model_tokens[m]["count"] += 1

    total_cost_cny = 0.0
    total_cost_usd = 0.0
    per_model = {}

    for m, tok in model_tokens.items():
        price = MODEL_PRICES.get(m)
        if not price:
            continue
        input_cost = (tok["input"] / 1_000_000) * price["input"]
        output_cost = (tok["output"] / 1_000_000) * price["output"]
        subtotal = input_cost + output_cost

        per_model[m] = {
            "input_tokens": tok["input"],
            "output_tokens": tok["output"],
            "total_tokens": tok["input"] + tok["output"],
            "queries": tok["count"],
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "subtotal": round(subtotal, 6),
            "currency": price["currency"],
        }

        if price["currency"] == "¥":
            total_cost_cny += subtotal
        else:
            total_cost_usd += subtotal

    return {
        "per_model": per_model,
        "total_cost_cny": round(total_cost_cny, 6),
        "total_cost_usd": round(total_cost_usd, 6),
        "total_cost_cny_equivalent": round(total_cost_cny + total_cost_usd * 7.2, 6),
        "total_queries": sum(tok["count"] for tok in model_tokens.values()),
    }


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def print_report(result: dict, date_range: str) -> None:
    """打印成本报告。"""
    print(f"\n{' 成本报告: ' + date_range :=^50s}")
    print(f"  查询次数: {result['total_queries']}")

    if not result["per_model"]:
        print("  无数据。\n")
        return

    for m, info in result["per_model"].items():
        cur = info["currency"]
        print(f"\n  [{m}]")
        print(f"    Token:  入 {_fmt_tokens(info['input_tokens'])}  "
              f"出 {_fmt_tokens(info['output_tokens'])}  "
              f"共 {_fmt_tokens(info['total_tokens'])}")
        print(f"    查询:   {info['queries']} 次")
        print(f"    入成本: {cur}{info['input_cost']:.4f}")
        print(f"    出成本: {cur}{info['output_cost']:.4f}")
        print(f"    小计:   {cur}{info['subtotal']:.4f}")

    if result["total_cost_cny"] > 0:
        print(f"\n  人民币合计: ¥{result['total_cost_cny']:.4f}")
    if result["total_cost_usd"] > 0:
        print(f"  美元合计:   ${result['total_cost_usd']:.4f}")
    if result["total_cost_cny"] > 0 and result["total_cost_usd"] > 0:
        print(f"  折合人民币: ¥{result['total_cost_cny_equivalent']:.4f} (按 1 USD = 7.2 CNY)")

    total = result["total_cost_cny"] + result["total_cost_usd"] * 7.2
    if total < 0.01:
        print(f"\n  💡 花费很低。DeepSeek 确实便宜。")
    elif total < 10:
        print(f"\n  💡 花费可控。关注是否有重复调用可以优化。")
    else:
        print(f"\n  ⚠️ 花费较高。建议检查 trace 找重复或无效调用。")
    print()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent token 成本计算器")
    parser.add_argument("--today", action="store_true", help="今天的成本")
    parser.add_argument("--last", type=int, help="最近 N 天")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="所有历史")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL,
                        help=f"默认模型（trace 未存 model 时使用，默认 {DEFAULT_MODEL}）")
    args = parser.parse_args()

    if args.date:
        traces = _traces_for_date(args.date)
        date_range = args.date
    elif args.today:
        date_str = datetime.now().strftime("%Y-%m-%d")
        traces = _traces_for_date(date_str)
        date_range = date_str
    elif args.last:
        traces = _traces_for_last_days(args.last)
        start = (datetime.now() - timedelta(days=args.last - 1)).strftime("%Y-%m-%d")
        end = datetime.now().strftime("%Y-%m-%d")
        date_range = f"{start} ~ {end}"
    elif args.all:
        traces = _all_traces()
        date_range = "全部历史"
    else:
        parser.print_help()
        sys.exit(0)

    if not traces:
        print(f"\n  日期范围 {date_range} 内无 trace 记录。")
        sys.exit(0)

    result = calculate(traces, model=args.model)
    print_report(result, date_range)
