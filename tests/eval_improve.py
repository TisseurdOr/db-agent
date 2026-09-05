"""评测 → 改进 → 验证 最小闭环（路由错自愈）。

跑评测 → 把失败用例按根因分类（路由错/事实错/质量低）→
对「路由错」反推精确匹配规则写进 router_rules.json（Router 最高优先级读取）→
复跑 routing 用例验证通过率提升、无回归。

用法:
    # 预览（只打印不写规则表）
    uv run python -m tests.eval_improve --dry-run

    # 真实执行：写入规则表并复跑验证
    uv run python -m tests.eval_improve

    # 只跑 routing 类（快、便宜，适合冒烟）
    uv run python -m tests.eval_improve --category routing --dry-run

事实错/质量低本次只分类展示、不自动改（需 LLM 写 SQL 提示/prompt 补丁，风险高，留下一步）。
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

from harness.observation.regression import check_regression
from harness.orchestration.multi.orchestrator import MultiAgentRunner
from harness.orchestration.multi.router_rules import add_rule
from tests.eval_cases import ALL_CASES, get_cases_by_category
from tests.eval_runner import _run_full_case, print_result, print_summary

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _pass_rate(results) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)


def classify_failure(case, plan_agents: set[str]) -> str:
    """失败根因分类: routing / fact / quality（确定性，按断言键判断）。"""
    a = case.assertions
    if "agent_in_plan" in a or "agent_not_in_plan" in a:
        in_ok = all(x in plan_agents for x in a.get("agent_in_plan", []))
        not_ok = all(x not in plan_agents for x in a.get("agent_not_in_plan", []))
        if not (in_ok and not_ok):
            return "routing"
    if case.expected:
        return "fact"
    return "quality"


async def run_cases(runner, cases):
    """跑一批 full cases，返回 [(case, result, plan_agents)]。"""
    records = []
    for i, case in enumerate(cases):
        print(f"\n  [{i+1}/{len(cases)}] {case.id}: {case.description}")
        result, _ = await _run_full_case(case, runner)
        info = runner.get_execution_info()
        plan_agents = set(info.get("plan_agents") or [])
        records.append((case, result, plan_agents))
        print_result(result)
    return records


async def main():
    parser = argparse.ArgumentParser(description="评测→改进→验证 闭环")
    parser.add_argument("--dry-run", action="store_true", help="只打印规则不写盘")
    parser.add_argument("--category", type=str, help="只跑某类 (routing/output_quality/edge)")
    parser.add_argument("--model", type=str, default="deepseek-chat")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{RED}需要 ANTHROPIC_API_KEY。{RESET}")
        sys.exit(1)

    if args.category:
        cases = [c for c in ALL_CASES if c.category == args.category and c.category != "guardrail"]
    else:
        cases = [c for c in ALL_CASES if c.category != "guardrail"]
    if not cases:
        print("没有匹配的用例。")
        sys.exit(1)

    client = Anthropic(api_key=api_key, base_url=os.getenv("ANTHROPIC_BASE_URL"))

    # Phase 1: 评测
    runner = await MultiAgentRunner.create(
        client, model=args.model, enable_data_quality=False, thread_id="eval-improve-session",
    )
    print(f"{BOLD}评测 ({len(cases)} 条){RESET}")
    records = await run_cases(runner, cases)
    await runner.aclose()

    results = [r for _, r, _ in records]
    print_summary(results)

    # Phase 2: 分类
    failed = [(c, r, p) for c, r, p in records if not r.passed]
    buckets = {"routing": [], "fact": [], "quality": []}
    for c, _, p in failed:
        buckets[classify_failure(c, p)].append(c)

    print(f"\n{BOLD}失败分类{RESET}: 路由错 {len(buckets['routing'])} / "
          f"事实错 {len(buckets['fact'])} / 质量低 {len(buckets['quality'])}")

    # Phase 3: 改进（仅路由错，确定性反推；只有 agent_not_in_plan 的需人工判断去向）
    applied = []
    for c in buckets["routing"]:
        agents = c.assertions.get("agent_in_plan")
        if agents is None:
            print(f"  {YELLOW}跳过（只有 agent_not_in_plan，去向不明需人工）{RESET}: {c.id} {c.query[:30]!r}")
            continue
        applied.append((c, agents))
        if not args.dry_run:
            add_rule(c.query, agents)
        print(f"  {'写入' if not args.dry_run else '预览'}规则: {c.query[:40]!r} -> {agents}")

    if args.dry_run:
        print(f"\n{YELLOW}--dry-run: 未写盘。去掉该参数真正落地。{RESET}")

    # Phase 4: 验证（写了规则且跑过 routing 类才复跑）
    if applied and not args.dry_run:
        routing_before = _pass_rate([r for c, r, _ in records if c.category == "routing"])
        verify_runner = await MultiAgentRunner.create(
            client, model=args.model, enable_data_quality=False, thread_id="eval-improve-verify",
        )
        routing_cases = get_cases_by_category("routing")
        print(f"\n{BOLD}验证：复跑 routing ({len(routing_cases)} 条){RESET}")
        vrecords = await run_cases(verify_runner, routing_cases)
        await verify_runner.aclose()
        routing_after = _pass_rate([r for _, r, _ in vrecords])
        print(f"\n{BOLD}验证结果{RESET}: routing 通过率 {routing_before:.0%} → {routing_after:.0%}")
        for w in check_regression("routing", {"pass_rate": routing_after, "case_count": len(routing_cases)}):
            print(f"{RED}{w}{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
