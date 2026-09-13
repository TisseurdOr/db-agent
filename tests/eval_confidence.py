"""置信度校准评测 — 验证 confidence_gate 的 LLM 自评分准不准。

背景：
  SQL 生成后过置信度门（node_confidence_gate），LLM 按 6 项标准自评，
  低于 0.7 暂停让用户确认。但这个分准不准，从来没验证过——
  LLM 可能给错 SQL 打高分、给对 SQL 打低分。

做法：
  跑 output_quality 用例（带 expected 标准答案），对每条：
    1. 跑完整系统，拿 passed（expected 事实断言）+ 生成的 SQL
    2. 独立调 CONFIDENCE_PROMPT 给这条 SQL 打分（schema 用 demo.db 全表结构）
    3. 按 confidence 是否 ≥ 阈值分组，算各组通过率
  高分组通过率明显高于低分组 → 置信度门有区分度；
  两者接近 → LLM 自评不可信，阈值或 6 项标准要调。

用法:
    uv run python -m tests.eval_confidence --category output_quality
    uv run python -m tests.eval_confidence --threshold 0.7 --model deepseek-chat

结果写 logs/confidence_calibration.json，供 web Eval 页读取。
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("AGENT_USER", "dba")

from anthropic import Anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness.constraints.confidence import CONFIDENCE_PROMPT, parse_confidence_result
from harness.observation.llm import extract_text
from harness.orchestration.multi.orchestrator import MultiAgentRunner
from tests.eval_cases import get_cases_by_category
from tests.eval_runner import _run_full_case, print_result

DB_PATH = PROJECT_ROOT / "db" / "demo.db"
OUT_FILE = PROJECT_ROOT / "logs" / "confidence_calibration.json"

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _schema_snapshot() -> str:
    """demo.db 全部建表语句拼接，作为 CONFIDENCE_PROMPT 的表结构上下文。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
        ).fetchall()
        return "\n".join(r[0] for r in rows)
    finally:
        conn.close()


def _score_sql(client: Anthropic, model: str, query: str, schema: str, sql: str) -> float | None:
    """独立调 CONFIDENCE_PROMPT 给一条 SQL 打分，返回 confidence（0-1）或 None。"""
    prompt = CONFIDENCE_PROMPT.format(query=query, schema_context=schema, sql=sql)
    resp = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    data = parse_confidence_result(extract_text(resp, context="confidence"))
    conf = data.get("confidence")
    return float(conf) if isinstance(conf, (int, float)) else None


def _pass_rate(group: list[dict]) -> float | None:
    if not group:
        return None
    return sum(1 for r in group if r["passed"]) / len(group)


async def main():
    parser = argparse.ArgumentParser(description="置信度校准评测")
    parser.add_argument("--category", type=str, default="output_quality", help="评测类别")
    parser.add_argument("--threshold", type=float, default=0.7, help="置信度分组阈值")
    parser.add_argument("--model", type=str, default="deepseek-chat")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{RED}需要 ANTHROPIC_API_KEY。{RESET}")
        sys.exit(1)

    cases = [c for c in get_cases_by_category(args.category) if c.expected]
    if not cases:
        print(f"没有带 expected 标准答案的用例: {args.category}")
        sys.exit(1)

    client = Anthropic(api_key=api_key, base_url=os.getenv("ANTHROPIC_BASE_URL"))
    schema = _schema_snapshot()

    runner = await MultiAgentRunner.create(
        client, model=args.model, enable_data_quality=False, thread_id="confidence-calib",
    )

    rows: list[dict] = []
    try:
        for i, case in enumerate(cases):
            print(f"\n  [{i+1}/{len(cases)}] {case.id}: {case.description}")
            result, _ = await _run_full_case(case, runner)
            print_result(result)
            sql = (runner.get_execution_info() or {}).get("sql", "")
            conf = None
            if sql:
                try:
                    conf = _score_sql(client, args.model, case.query, schema, sql)
                except Exception as e:
                    print(f"    {YELLOW}打分失败: {type(e).__name__}{RESET}")
            rows.append({"id": case.id, "passed": result.passed, "confidence": conf, "sql": sql})
    finally:
        await runner.aclose()

    scored = [r for r in rows if r["confidence"] is not None]
    high = [r for r in scored if r["confidence"] >= args.threshold]
    low = [r for r in scored if r["confidence"] < args.threshold]
    high_rate = _pass_rate(high)
    low_rate = _pass_rate(low)
    gap = (high_rate - low_rate) if (high_rate is not None and low_rate is not None) else None

    print(f"\n{BOLD}置信度校准结果{RESET}")
    print(f"  用例 {len(rows)} 条，成功打分 {len(scored)} 条")
    print(f"  高分组(≥{args.threshold}): {len(high)} 条，通过率 {high_rate:.0%}" if high_rate is not None else "  高分组: 0 条")
    print(f"  低分组(<{args.threshold}): {len(low)} 条，通过率 {low_rate:.0%}" if low_rate is not None else "  低分组: 0 条")
    if gap is not None:
        verdict = (
            f"{GREEN}区分度良好：置信度能区分对错{RESET}" if gap >= 0.2 else
            (f"{YELLOW}区分度一般：置信度有一定参考性{RESET}" if gap > 0 else
             f"{RED}无区分度：LLM 自评不可信，阈值或 6 项标准需调{RESET}")
        )
        print(f"  区分度 gap = {gap:+.0%}  →  {verdict}")

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "threshold": args.threshold,
        "total": len(rows),
        "scored": len(scored),
        "high_count": len(high),
        "low_count": len(low),
        "high_pass_rate": high_rate,
        "low_pass_rate": low_rate,
        "gap": gap,
        "rows": rows,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写 {OUT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
