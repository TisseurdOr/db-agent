"""自学习闭环的"控制"齿轮：SQL few-shot 样例的验证 + 回滚。

三趟评测，学习副作用全程靠 AUTO_LEARN_SQL=0 关掉 node_sql 的急切回流，
由本脚本在学习阶段显式写入"事实断言通过"的样例：
  1. 基线（关学习，seed-only）→ baseline 通过率
  2. 学习（只学 expected 事实断言通过的 case）→ learn_from_success(force=True) 写样例
  3. 验证（只读）→ get_sql_fewshot 检索到刚学的样例 → after 通过率
退化超过阈值 → purge_learned() 回滚到 seed 基线。

用法:
    # 预览三趟对比、不真删
    uv run python -m tests.eval_selflearn --reset --dry-run

    # 真实执行：退化就回滚
    uv run python -m tests.eval_selflearn --reset

    # 只跑 output_quality 类（默认）
    uv run python -m tests.eval_selflearn --category output_quality
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("AGENT_USER", "dba")

from anthropic import Anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness.context.sql_examples import list_learned, purge_learned
from harness.memory.feedback import learn_from_success
from harness.observation.regression import append_history, check_regression, save_baseline
from harness.orchestration.multi.orchestrator import MultiAgentRunner
from tests.eval_cases import get_cases_by_category
from tests.eval_runner import _run_full_case, print_result, print_summary

RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _pass_rate(results) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)


async def run_eval(runner, cases, learn=False):
    results = []
    for i, case in enumerate(cases):
        print(f"\n  [{i+1}/{len(cases)}] {case.id}: {case.description}")
        result, answer = await _run_full_case(case, runner)
        results.append(result)
        print_result(result)
        # 学习阶段：只写事实断言（expected）通过的样例——避免把"能执行但结果错"的
        # SQL 当成功样例写进库（旧逻辑在 node_sql 里靠 sql_executes 门，拦不住错答案）。
        if learn and result.passed and case.expected:
            sql = (runner.get_execution_info() or {}).get("sql", "")
            if sql:
                learn_from_success(case.query, answer, sql=sql, source="auto", force=True)
    return results


async def main():
    parser = argparse.ArgumentParser(description="自学习闭环控制齿轮")
    parser.add_argument("--reset", action="store_true", help="先清掉非 seed 样例到 seed 基线")
    parser.add_argument("--category", type=str, default="output_quality", help="评测类别")
    parser.add_argument("--threshold", type=float, default=0.05, help="退化阈值（通过率下降超过即回滚）")
    parser.add_argument("--dry-run", action="store_true", help="只打印回滚决策不真删")
    parser.add_argument("--model", type=str, default="deepseek-chat")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{RED}需要 ANTHROPIC_API_KEY。{RESET}")
        sys.exit(1)

    cases = get_cases_by_category(args.category)
    if not cases:
        print(f"没有匹配的用例: {args.category}")
        sys.exit(1)

    if args.reset:
        n = purge_learned()
        print(f"{BOLD}重置{RESET}: 删除 {n} 条非 seed 样例")

    client = Anthropic(api_key=api_key, base_url=os.getenv("ANTHROPIC_BASE_URL"))

    # Phase 1: 基线（关学习，seed-only）
    os.environ["AUTO_LEARN_SQL"] = "0"
    runner = await MultiAgentRunner.create(
        client, model=args.model, enable_data_quality=False, thread_id="selflearn-baseline",
    )
    print(f"{BOLD}基线（学习关，{len(cases)} 条）{RESET}")
    baseline_results = await run_eval(runner, cases)
    await runner.aclose()
    baseline = _pass_rate(baseline_results)
    print_summary(baseline_results)

    # Phase 2: 学习（只学事实断言通过的 case；AUTO_LEARN_SQL=0 关掉 node_sql 的急切回流）
    runner = await MultiAgentRunner.create(
        client, model=args.model, enable_data_quality=False, thread_id="selflearn-learn",
    )
    print(f"\n{BOLD}学习（只学 expected 通过，{len(cases)} 条）{RESET}")
    await run_eval(runner, cases, learn=True)
    await runner.aclose()
    learned = list_learned()
    print(f"\n学习后非 seed 样例: {len(learned)} 条")

    # Phase 3: 验证（只读，不写样例）
    runner = await MultiAgentRunner.create(
        client, model=args.model, enable_data_quality=False, thread_id="selflearn-validate",
    )
    print(f"\n{BOLD}验证（只读，{len(cases)} 条）{RESET}")
    after_results = await run_eval(runner, cases)
    await runner.aclose()
    after = _pass_rate(after_results)
    print_summary(after_results)

    # Phase 4: 控制
    drop = baseline - after
    direction = "提升" if after > baseline else ("持平" if after == baseline else "退化")
    print(f"\n{BOLD}控制{RESET}: 通过率 {baseline:.0%} → {after:.0%}（{direction} {abs(baseline - after):.0%}）")
    if drop > args.threshold:
        if args.dry_run:
            print(f"{YELLOW}--dry-run: 退化 {drop:.0%} 超阈值 {args.threshold:.0%}，"
                  f"将回滚 {len(learned)} 条非 seed 样例（未执行）{RESET}")
        else:
            n = purge_learned()
            print(f"回滚: 删除 {n} 条非 seed 样例，回到 seed 基线")
    else:
        print("未退化，保留学习样例")
    for w in check_regression("sql_selflearn", {"pass_rate": after, "case_count": len(cases)}):
        print(f"{RED}{w}{RESET}")
    # 写 baseline/history，让 web Eval 页趋势图能看到控制齿轮的跑分（mode=sql_selflearn）
    passed = sum(1 for r in after_results if r.passed)
    save_baseline("sql_selflearn", {"pass_rate": after, "case_count": len(cases)})
    append_history("sql_selflearn", {
        "pass_rate": after,
        "case_count": len(cases),
        "passed": passed,
        "total": len(cases),
        "model": args.model,
        "judge": False,
        "failed_ids": [r.case.id for r in after_results if not r.passed],
    })


if __name__ == "__main__":
    asyncio.run(main())
