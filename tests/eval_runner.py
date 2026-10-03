"""Agent 评估执行器 — 跑 eval cases，输出通过率报告。

用法:
    # 快速模式（不调 LLM，秒级跑完 6 条 guardrail 测试）
    python -m tests.eval_runner --fast

    # 完整模式（调 LLM，跑全部 41 条用例）
    python -m tests.eval_runner --full

    # LLM-as-Judge: 用 Kimi 对失败用例打分（准确性/完整性/简洁性），独立于被测模型
    python -m tests.eval_runner --full --judge

    # Pro 建议: 让 Kimi 分析失败原因并给出修改方案
    python -m tests.eval_runner --full --judge --suggest

    # 同步 eval cases 到 Opik Dataset 并上传 Experiment（需 OPIK_ENABLED=1）
    python -m tests.eval_runner --fast --opik
    python -m tests.eval_runner --full --judge --opik
    # 或: OPIK_EVAL=1 python -m tests.eval_runner --fast

    # 指定类别
    python -m tests.eval_runner --category routing
    python -m tests.eval_runner --category guardrail

    # 单个用例
    python -m tests.eval_runner --id route-001

设计原则:
    1. 快速模式优先——零 token 成本，每次 commit 都能跑
    2. 断言宽松——agent 输出不确定，用 contains/not_contains 而非精确匹配
    3. 失败不中断——跑完所有用例再汇总，一次看到全部问题
    4. LLM-as-Judge——用强模型当裁判，不靠精确字符串匹配判断质量
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
# eval 需要查库；仅 dba/manager 有库权限，未显式配置时用 dba
os.environ.setdefault("AGENT_USER", "dba")

from anthropic import Anthropic

from db.seed import DB_PATH

# Kimi API 配置（Anthropic 兼容接口，用于 Judge 独立评估）
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/anthropic")
KIMI_MODEL = os.getenv("KIMI_MODEL", "kimi-k2.5")


def _make_kimi_client() -> Anthropic | None:
    """创建 Kimi 客户端（Anthropic 兼容），用于 Judge/Suggest 独立评估。"""
    if not KIMI_API_KEY:
        return None
    return Anthropic(api_key=KIMI_API_KEY, base_url=KIMI_BASE_URL)


# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harness.constraints.guardrails import guard_input
from harness.observation.llm import extract_text
from tests.eval_cases import (
    ALL_CASES,
    EvalCase,
    get_cases_by_category,
    get_fast_cases,
)

JUDGE_PROMPT = """你是 Agent 输出质量评估员。对以下回答从三个维度打分（1-5 分）:

1. accuracy（准确性）: 数据是否正确？有没有编造数字？
2. completeness（完整性）: 是否完整回答了用户问题？有没有遗漏关键信息？
3. conciseness（简洁性）: 是否直接回答？有没有冗余废话？

【重要】如果题目附带了【标准答案】(ground truth)，accuracy 必须以它为准：
- 回答包含标准答案的关键数字/名称 → accuracy 打高分（4-5）
- 回答的关键数字与标准答案不符，或标准答案明确存在但回答说没有 → accuracy 打低分（1-2）
- 没有附带标准答案时，才凭回答内部一致性判断是否编造

输出格式（严格 JSON，不要任何其他文字）:
{"accuracy": N, "completeness": N, "conciseness": N, "total": N, "verdict": "pass"|"fail", "comment": "一句话评价"}

verdict 规则: total >= 12 为 pass，否则 fail。total = accuracy + completeness + conciseness。
"""

SUGGEST_PROMPT = """你是 Agent 架构师。以下 eval case 未通过测试或评分较低。

请分析失败原因并给出具体修改方案。只关注能落地的事情：
- Prompt 怎么改（给具体文字）
- token 上限是否合理
- Tool schema 是否需要调整
- Router 规则是否需要补充

输出格式:
## 失败原因
[一句话]

## 是否假阳性
yes / no — 如果实际回答没问题只是断言太严，是假阳性

## 修改方案
[具体的修改步骤，含代码或 prompt 文字]

## 优先级
high / medium / low
"""

# 颜色输出
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"


class EvalResult:
    """单条用例的执行结果。"""
    __slots__ = ("case", "passed", "details", "elapsed", "tokens")

    def __init__(self, case: EvalCase):
        self.case = case
        self.passed = True
        self.details: list[str] = []
        self.elapsed = 0.0
        self.tokens = 0

    def fail(self, reason: str) -> None:
        self.passed = False
        self.details.append(reason)

    def ok(self, msg: str) -> None:
        self.details.append(f"  {GREEN}✓{RESET} {msg}")


async def _run_fast_case(case: EvalCase) -> EvalResult:
    """快速用例：只测 guardrails，不调 LLM。"""
    result = EvalResult(case)
    t0 = time.time()

    assertions = case.assertions

    if "guard_should_block" in assertions:
        passed, reason = guard_input(case.query)
        should_block = assertions["guard_should_block"]

        if should_block and passed:
            result.fail(f"应被拦截但放行了: '{case.query[:50]}'")
        elif should_block and not passed:
            result.ok(f"正确拦截: {reason}")
        elif not should_block and not passed:
            result.fail(f"不应被拦截但拦了: {reason}")
        elif not should_block and passed:
            result.ok("正确放行")

    result.elapsed = time.time() - t0
    return result


def _is_interrupt(answer) -> bool:
    return isinstance(answer, dict) and bool(answer.get("__interrupt__"))


def _interrupt_data(answer) -> dict:
    data = answer.get("data") if isinstance(answer, dict) else {}
    return data if isinstance(data, dict) else {}


# interrupt payload 的 tool 名 → plan 里的 agent 名（暂停时 trace 还没落盘）
_INTERRUPT_TOOL_TO_AGENT = {
    "run_hbase": "hbase",
    "run_query": "sql",
    "run_hive": "hive",
}


def _agents_from_interrupt(data: dict) -> set[str]:
    tool = str(data.get("tool") or "")
    agent = _INTERRUPT_TOOL_TO_AGENT.get(tool)
    return {agent} if agent else set()


def _resolve_expected(case: EvalCase) -> str:
    """返回 case 的标准答案片段。

    优先用 expected_sql 在 db/demo.db 实查（动态 ground truth，防 seed 日期推进导致
    expected 过时）；expected_sql 为空或实查失败时回退写死的 expected。
    """
    if case.expected_sql:
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                row = conn.execute(case.expected_sql).fetchone()
            finally:
                conn.close()
            if row and row[0] is not None:
                return str(row[0])
        except Exception as e:
            print(f"⚠️ 动态实查失败（{case.id}），回退写死 expected: {e}")
    return case.expected


async def _run_full_case(case: EvalCase, runner) -> tuple[EvalResult, str]:
    """完整用例：调 multi-agent 系统，检查输出和 plan。返回 (result, answer_text)。"""
    result = EvalResult(case)
    t0 = time.time()
    answer = ""

    try:
        # 每个 case 独立 thread_id，隔离 checkpointer 的 messages 累积——
        # 否则 node_router 读 state["messages"][-6:] 会串进前面 case 的 query，
        # maybe_rerecall 再把污染后的 plan 拼进 probe，SQL 上下文越滚越脏。
        tid = f"{getattr(runner, 'thread_id', 'eval')}-{case.id}"
        answer = await runner.run(case.query, thread_id=tid)
    except Exception as e:
        result.fail(f"Agent 执行异常: {e}")
        result.elapsed = time.time() - t0
        return result, ""

    result.elapsed = time.time() - t0
    assertions = case.assertions
    expect_hitl = bool(assertions.get("expect_hitl"))
    hitl_paused = _is_interrupt(answer)
    interrupt_payload = _interrupt_data(answer) if hitl_paused else {}

    if expect_hitl and hitl_paused:
        itype = interrupt_payload.get("type") or "?"
        tool = interrupt_payload.get("tool") or "?"
        op = interrupt_payload.get("operation") or "?"
        result.ok(f"HITL 已暂停: type={itype} tool={tool} op={op}")
        # 评测只证明「停下来了」；拒绝以清掉 checkpointer，避免下一条用例卡在 interrupt
        try:
            await runner.resume(approved=False)
        except Exception as e:
            result.fail(f"HITL 拒绝恢复异常: {e}")
            return result, interrupt_payload.get("message") or str(answer)
        answer = interrupt_payload.get("message") or json.dumps(interrupt_payload, ensure_ascii=False)
    elif expect_hitl and not hitl_paused:
        preview = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        result.fail(
            "应触发 HITL interrupt，但 runner.run() 直接返回了最终回答。"
            f"\n    实际回答（前 200 字）: {preview[:200]}"
        )
    elif hitl_paused and not expect_hitl:
        # 其它用例偶发审批：自动批准，让后续断言仍能看最终回答
        try:
            answer = await runner.resume(approved=True)
        except Exception as e:
            result.fail(f"HITL resume 异常: {e}")
            return result, str(answer)

    # 执行信息从 runner 内存直接读（不解析 trace 文件——文件是全局共享状态，
    # 并行/连续运行会串读，且 HITL 暂停时 trace 尚未落盘）。
    # plan 是 Router 的输出计划，正好是 agent_in_plan 断言想验证的东西。
    exec_info = runner.get_execution_info()
    agent_names = set(exec_info.get("plan_agents") or [])
    if expect_hitl and hitl_paused:
        # 兜底：interrupt 发生在工具执行层，plan 里一定包含对应 agent
        agent_names = agent_names | _agents_from_interrupt(interrupt_payload)
    tokens = int(exec_info.get("tokens") or 0)
    result.tokens = tokens

    # 安全转换：resume 后仍可能是 dict（边缘情况）
    if isinstance(answer, dict):
        answer = json.dumps(answer, ensure_ascii=False)

    # ── 断言检查 ──

    # agent_in_plan: plan 中必须包含这些 agent
    if "agent_in_plan" in assertions:
        for agent in assertions["agent_in_plan"]:
            if agent in agent_names:
                result.ok(f"plan 包含 {agent}")
            else:
                result.fail(f"plan 缺少 {agent}（实际: {', '.join(agent_names) or '无'}）")

    # agent_not_in_plan: plan 中不能有这些 agent
    if "agent_not_in_plan" in assertions:
        for agent in assertions["agent_not_in_plan"]:
            if agent not in agent_names:
                result.ok(f"plan 不含 {agent}")
            else:
                result.fail(f"plan 不应包含 {agent}（实际: {', '.join(agent_names)}）")

    # output_contains: 回答中应包含的关键词（任一命中即通过）
    if "output_contains" in assertions:
        keywords = assertions["output_contains"]
        hits = [kw for kw in keywords if kw.lower() in answer.lower()]
        if hits:
            result.ok(f"回答包含: {', '.join(hits)}")
        else:
            result.fail(
                f"回答不包含任何预期关键词 {keywords}。\n"
                f"    实际回答（前 200 字）: {answer[:200]}"
            )

    # output_not_contains: 回答中不能出现的内容
    if "output_not_contains" in assertions:
        for keyword in assertions["output_not_contains"]:
            if keyword.lower() in answer.lower():
                result.fail(f"回答不应包含 '{keyword}'")
            else:
                result.ok(f"回答不含 '{keyword}'")

    # 事实断言（expected 非空才检查）：标准答案片段必须出现在回答里。
    # 这是从「回答里有没有提关键词」升级到「回答的事实对不对」的关键一环。
    # expected_sql 存在时先实查 DB 得到动态 ground truth，防写死值过时。
    expected = _resolve_expected(case)
    if expected:
        if expected.lower() in answer.lower():
            result.ok(f"事实正确: 回答包含标准答案「{expected}」")
        else:
            result.fail(
                f"事实错误: 回答不包含标准答案「{expected}」。\n"
                f"    实际回答（前 200 字）: {answer[:200]}"
            )

    # max_tokens: token 预算上限（从 runner 内存 trace 读，不依赖文件）
    if "max_tokens" in assertions:
        limit = assertions["max_tokens"]
        if tokens <= limit:
            result.ok(f"token 用量 {tokens} ≤ {limit}")
        else:
            result.fail(f"token 用量 {tokens} > 上限 {limit}")

    # max_elapsed: 耗时上限
    if "max_elapsed" in assertions:
        limit = assertions["max_elapsed"]
        if result.elapsed <= limit:
            result.ok(f"耗时 {result.elapsed:.1f}s ≤ {limit}s")
        else:
            result.fail(f"耗时 {result.elapsed:.1f}s > 上限 {limit}s")

    return result, answer


async def judge_answer(client: Anthropic, case: EvalCase, answer: str, model: str = KIMI_MODEL) -> dict:
    """LLM-as-Judge: 让模型对回答打分。

    如果用例带 expected（标准答案片段），把它注入给 Judge——
    让 accuracy 变成「对着标准答案核对」，而不是凭感觉判断数字像不像编的。
    """
    expected_note = ""
    expected = _resolve_expected(case)
    if expected:
        expected_note = f"\n标准答案（ground truth）: {expected}"
    user_msg = (
        f"用户问题: {case.query}\n"
        f"Agent 回答: {answer[:2000]}\n"
        f"用例预期: {case.description}\n"
        f"断言规则: {json.dumps(case.assertions, ensure_ascii=False)}"
        f"{expected_note}"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=2000,  # Kimi 等推理模型 thinking 吃 token，需给足空间
        system=JUDGE_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = extract_text(resp, context="eval_judge") or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"accuracy": 0, "completeness": 0, "conciseness": 0, "total": 0, "verdict": "error", "comment": f"JSON 解析失败: {text[:100]}"}


async def judge_results(client: Anthropic, failing: list, model: str = "deepseek-chat") -> list[dict]:
    """对失败用例逐条打分。"""
    scores = []
    for i, (result, answer) in enumerate(failing):
        case = result.case
        print(f"  [{i+1}/{len(failing)}] 评分: {case.id} ...", end=" ")
        score = await judge_answer(client, case, answer, model=model)
        scores.append(score)
        verdict = score.get("verdict", "?")
        total = score.get("total", "?")
        comment = score.get("comment", "")
        icon = "✓" if verdict == "pass" else "✗"
        print(f"{icon} total={total}/15 {verdict} — {comment}")
    return scores


async def suggest_fixes(client: Anthropic, failing_with_answers: list, model: str = "deepseek-reasoner") -> str:
    """让 Pro 模型分析失败用例并给出修改方案。"""
    cases_text = []
    for result, answer in failing_with_answers:
        case = result.case
        cases_text.append(
            f"### {case.id}: {case.description}\n"
            f"query: {case.query}\n"
            f"assertions: {json.dumps(case.assertions, ensure_ascii=False)}\n"
            f"失败原因: {'; '.join(result.details)}\n"
            f"实际回答（前 2000 字）: {answer[:2000]}\n"
        )
    user_msg = "\n---\n".join(cases_text)

    print(f"\n  正在请求 {model} 分析 {len(failing_with_answers)} 条失败用例...")
    resp = client.messages.create(
        model=model,
        max_tokens=8000,  # Kimi 等推理模型 thinking 吃 token，需给足空间
        system=SUGGEST_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return extract_text(resp, context="eval_suggest") or "无输出"


def print_header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


def print_result(result: EvalResult) -> None:
    """打印单条用例的执行结果。"""
    case = result.case
    status = f"{GREEN}✓ PASS{RESET}" if result.passed else f"{RED}✗ FAIL{RESET}"
    elapsed = f"{result.elapsed:.2f}s" if result.elapsed > 0 else ""
    token_info = f" · {result.tokens}t" if result.tokens > 0 else ""
    print(f"  [{case.id}] {status} {case.description} ({elapsed}{token_info})")
    if not result.passed:
        for detail in result.details:
            if detail.startswith("  " + GREEN) or detail.startswith("  " + RED):
                print(detail)
            else:
                print(f"    {RED}→ {detail}{RESET}")


def print_summary(results: list[EvalResult]) -> None:
    """打印汇总报告。"""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    total_elapsed = sum(r.elapsed for r in results)
    total_tokens = sum(r.tokens for r in results)

    rate = passed / total * 100 if total > 0 else 0
    color = GREEN if rate == 100 else (YELLOW if rate >= 80 else RED)

    print_header("评估汇总")
    print(f"  用例总数: {total}")
    print(f"  通过:     {GREEN}{passed}{RESET}")
    if failed > 0:
        print(f"  失败:     {RED}{failed}{RESET}")
    print(f"  通过率:   {color}{rate:.0f}%{RESET}")
    print(f"  总耗时:   {total_elapsed:.1f}s")
    if total_tokens > 0:
        print(f"  总 token: {total_tokens}")

    if failed > 0:
        print(f"\n  {RED}失败用例:{RESET}")
        for r in results:
            if not r.passed:
                print(f"    [{r.case.id}] {r.case.description}")
                for detail in r.details:
                    if not detail.startswith("  "):
                        print(f"      → {detail}")

    print()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent 评估执行器")
    parser.add_argument("--fast", action="store_true", help="快速模式（仅 guardrail，不调 LLM）")
    parser.add_argument("--full", action="store_true", help="完整模式（需要 API Key）")
    parser.add_argument("--category", type=str, help="按类别过滤 (guardrail/routing/output_quality/edge)")
    parser.add_argument("--id", type=str, help="跑单个用例")
    parser.add_argument("--model", type=str, default="deepseek-chat", help="模型名")
    parser.add_argument("--judge", action="store_true", help="LLM-as-Judge: 对失败用例用 deepseek-chat 打分")
    parser.add_argument("--suggest", action="store_true", help="让 deepseek-v4-flash 分析失败原因并给出修改方案")
    parser.add_argument("--opik", action="store_true", help="同步 Dataset 并上传 Experiment 到 Opik（或设 OPIK_EVAL=1）")
    args = parser.parse_args()

    use_opik = args.opik or os.getenv("OPIK_EVAL", "").strip().lower() in {"1", "true", "yes", "on"}
    if use_opik:
        # Eval upload needs Opik client; enable if caller only passed --opik
        os.environ.setdefault("OPIK_ENABLED", "1")

    if args.id:
        cases = [c for c in ALL_CASES if c.id == args.id]
        if not cases:
            print(f"未找到用例: {args.id}")
            sys.exit(1)
    elif args.category:
        cases = get_cases_by_category(args.category)
    elif args.fast:
        cases = get_fast_cases()
    elif args.full:
        cases = ALL_CASES
    else:
        # 默认：跑快速用例
        cases = get_fast_cases()
        print(f"{YELLOW}默认跑快速模式（{len(cases)} 条 guardrail 用例）。--full 跑完整测试。{RESET}")

    if not cases:
        print("没有匹配的用例。")
        sys.exit(1)

    if use_opik:
        try:
            from harness.observation.opik_eval import sync_eval_dataset
            from tests.eval_cases import ALL_CASES as _ALL_START
            n = len(sync_eval_dataset(_ALL_START))
            print(f"{CYAN}Opik dataset synced ({n} cases){RESET}")
        except Exception as exc:
            print(f"{YELLOW}Opik dataset sync skipped: {exc}{RESET}")

    results: list[EvalResult] = []
    full_answers: dict[str, str] = {}  # case.id → answer text（judge 用）

    # 分离快速用例和完整用例
    fast_cases = [c for c in cases if c.category == "guardrail"]
    full_cases = [c for c in cases if c.category != "guardrail"]

    # Fast cases: 不调 LLM
    if fast_cases:
        print_header(f"快速评估 — Guardrail ({len(fast_cases)} 条)")
        for case in fast_cases:
            result = await _run_fast_case(case)
            results.append(result)
            print_result(result)

    # Full cases: 调 LLM
    if full_cases:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print(f"\n{RED}需要 ANTHROPIC_API_KEY 才能跑完整评估。{RESET}")
            print(f"快速用例已跑完，跳过 {len(full_cases)} 条完整用例。")
        else:
            client = Anthropic(
                api_key=api_key,
                base_url=os.getenv("ANTHROPIC_BASE_URL"),
            )
            from harness.orchestration.multi.orchestrator import MultiAgentRunner

            print_header(f"完整评估 ({len(full_cases)} 条)")
            runner = await MultiAgentRunner.create(
                client,
                model=args.model,
                enable_data_quality=False,
                thread_id="eval-session",
            )

            try:
                for i, case in enumerate(full_cases):
                    print(f"\n  [{i+1}/{len(full_cases)}] {case.id}: {case.description}")
                    result, answer = await _run_full_case(case, runner)
                    results.append(result)
                    full_answers[case.id] = answer
                    print_result(result)
            finally:
                await runner.aclose()

    print_summary(results)

    # ── 回归基线：与上次同模式通过率对比，退步自动告警 ──
    # 像体检报告存档：改代码后跑评测，立刻知道有没有把系统改坏。
    eval_mode = "full" if (args.full or full_cases) else "fast"
    if args.category:
        eval_mode = args.category
    elif args.id:
        eval_mode = "single"
    passed_count = sum(1 for r in results if r.passed)
    rate = passed_count / len(results) if results else 0.0
    from harness.observation.regression import (
        append_history,
        check_regression,
        save_baseline,
    )
    for warning in check_regression(eval_mode, {"pass_rate": rate, "case_count": len(results)}):
        print(f"{RED}{warning}{RESET}")
    save_baseline(eval_mode, {"pass_rate": rate, "case_count": len(results)})
    append_history(eval_mode, {
        "pass_rate": rate,
        "case_count": len(results),
        "passed": passed_count,
        "total": len(results),
        "model": args.model if full_cases else "",
        "judge": bool(args.judge),
        "failed_ids": [r.case.id for r in results if not r.passed],
    })

    # ── Judge: LLM-as-Judge 打分（使用 Kimi，独立于被测模型） ──
    judge_by_id: dict[str, dict] = {}
    if args.judge:
        failed = [(r, full_answers.get(r.case.id, "")) for r in results if not r.passed]
        if not failed:
            print(f"{GREEN}所有用例通过，无需 judge 评分。{RESET}")
        else:
            judge_client = _make_kimi_client()
            if not judge_client:
                print(f"{RED}judge 模式需要 KIMI_API_KEY{RESET}")
            else:
                print_header(f"LLM-as-Judge 评分 (Kimi {KIMI_MODEL}, {len(failed)} 条失败用例)")
                scores = await judge_results(judge_client, failed, model=KIMI_MODEL)
                for (result, _ans), score in zip(failed, scores):
                    judge_by_id[result.case.id] = score
                passed_judge = sum(1 for s in scores if s.get("verdict") == "pass")
                print(f"\n  Judge 判定: {GREEN}{passed_judge} pass{RESET} / {RED}{len(scores) - passed_judge} fail{RESET} (共 {len(scores)} 条)")

    # ── Suggest: Kimi 模型修改建议 ──
    if args.suggest:
        failed = [(r, full_answers.get(r.case.id, "")) for r in results if not r.passed]
        if not failed:
            print(f"{GREEN}所有用例通过，无需修改建议。{RESET}")
        else:
            suggest_client = _make_kimi_client()
            if not suggest_client:
                print(f"{RED}suggest 模式需要 KIMI_API_KEY{RESET}")
            else:
                print_header(f"修改建议 (Kimi {KIMI_MODEL})")
                suggestion = await suggest_fixes(suggest_client, failed, model=KIMI_MODEL)
                print(suggestion)
                print()

    # ── Opik: sync dataset + upload experiment ──
    if use_opik:
        mode = eval_mode
        try:
            from harness.observation.opik_eval import sync_eval_dataset, upload_eval_experiment
            from tests.eval_cases import ALL_CASES as _ALL

            print_header("Opik Experiment 上传")
            id_map = sync_eval_dataset(_ALL)
            print(f"  Dataset synced: {len(id_map)} cases")
            opik_out = upload_eval_experiment(
                results=results,
                answers=full_answers,
                judge_scores=judge_by_id or None,
                mode=mode,
                model=args.model if full_cases else "",
            )
            if opik_out.get("ok"):
                print(f"  Experiment: {opik_out.get('experiment_name')}")
                print(f"  ID:         {opik_out.get('experiment_id')}")
                print(f"  Items:      {opik_out.get('item_count')}  pass_rate={opik_out.get('pass_rate', 0):.0%}")
                print("  Open Opik UI → Experiments → latest eval-*")
                print("  Dataset:    Datasets → db-agent-eval-cases")
            else:
                print(f"{RED}  Opik upload failed: {opik_out.get('error')}{RESET}")
        except Exception as exc:
            print(f"{RED}  Opik upload error: {exc}{RESET}")

    # 返回码：有失败返回 1
    if any(not r.passed for r in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
