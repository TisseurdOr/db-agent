# scripts/demo_feedback.py — 自学习闭环现场演示
#
# 用法:
#   .venv/bin/python scripts/demo_feedback.py           # 两幕全跑
#   .venv/bin/python scripts/demo_feedback.py quality   # 只跑质量门（零 API）
#   .venv/bin/python scripts/demo_feedback.py loop      # 写回流 → 再检索命中（需 embedding）

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("AGENT_USER", "dba")


def banner(title: str):
    print(f"\n{'═' * 60}\n  {title}\n{'═' * 60}")


def demo_quality():
    """第 1 幕：质量门（零 API）——该拦的拦、该放的放。"""
    banner("第 1 幕 · 质量门：脏样例绝不入库")
    from harness.memory.feedback import extract_sql, should_learn

    cases = [
        ("超时垃圾", "各部门订单", "(Agent 在 8 轮内未完成)", False),
        ("纯错误 JSON", "各部门订单", '{"error": "no such column: x", "retryable": true}', False),
        ("用户拒绝", "查工资", "用户拒绝了该查询", False),
        (
            "自愈成功",
            "订单总金额",
            "首次失败 no such column: total_amount\nSELECT SUM(total) FROM orders\n结果: 13490700",
            True,
        ),
        (
            "正常成功",
            "各部门的订单总金额是多少",
            "```sql\nSELECT d.name, SUM(o.total) FROM orders o "
            "JOIN departments d ON o.dept_id = d.id GROUP BY d.name;\n```",
            True,
        ),
    ]
    for name, q, text, expect in cases:
        ok = should_learn(q, text)
        mark = "✅" if ok == expect else "❌"
        sql = extract_sql(text)
        print(f"   {mark} {name}: should_learn={ok} (期望 {expect})"
              + (f" 抽出: {sql[:50]}…" if sql and len(sql) > 50 else (f" 抽出: {sql}" if sql else "")))


async def demo_loop():
    """第 2 幕：真写入 → 真召回（需 EMBEDDING_API_KEY）。"""
    banner("第 2 幕 · 闭环：回流写入 → 相似问法命中 few-shot")
    if not os.getenv("EMBEDDING_API_KEY"):
        print("   ⏭ 未配置 EMBEDDING_API_KEY，跳过本幕")
        return

    from harness.context.sql_examples import get_sql_fewshot
    from harness.memory.feedback import learn_from_success

    # 用唯一问法，避免和种子样例混淆
    tag = uuid.uuid4().hex[:6]
    question = f"市场部今年完成了多少订单金额_{tag}"
    result_text = """
查询成功：
```sql
SELECT SUM(o.total) AS 订单金额
FROM orders o JOIN departments d ON o.dept_id = d.id
WHERE d.name = '市场部'
```
市场部订单金额已汇总。
"""
    print(f"   写入问法: {question}")
    ok = learn_from_success(question, result_text, source="auto")
    if not ok:
        print("   ❌ 回流失败（检查 embedding / Chroma）")
        return

    # 用近似问法检索——不应要求字面一致
    probe = f"市场部的订单总金额是多少_{tag}"
    print(f"   探测问法: {probe}")
    fewshot = get_sql_fewshot(probe, top_k=3)
    if not fewshot:
        print("   ❌ 召回为空——闭环未打通")
        return
    if "市场部" in fewshot and "SUM" in fewshot.upper():
        print("   ✅ 命中回流样例（含市场部 + SUM）")
        print("   --- few-shot 注入预览 ---")
        for line in fewshot.splitlines()[:8]:
            print(f"   {line}")
    else:
        print("   ⚠️ 召回了内容但未看到刚写入的特征：")
        print(fewshot[:400])

    # 顺带验证 HITL 入口
    from harness.memory.feedback import learn_from_hitl
    hitl_q = f"员工平均工资_{tag}"
    hitl_sql = "SELECT ROUND(AVG(salary), 0) AS 平均工资 FROM employees WHERE status = 'active'"
    if learn_from_hitl(hitl_q, hitl_sql):
        print(f"   ✅ HITL 回流也写成功: {hitl_q}")


async def demo_live_agent():
    """第 3 幕（可选）：真跑一次 SQL Agent，观察自动回流日志。"""
    banner("第 3 幕 · 真实 SQL Agent 跑通后应打印 📥 自学习")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("   ⏭ 未配置 ANTHROPIC_API_KEY，跳过")
        return
    from anthropic import Anthropic

    from harness.orchestration.multi.orchestrator import MultiAgentRunner

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    runner = await MultiAgentRunner.create(
        client,
        model=os.getenv("ANTHROPIC_MODEL", "deepseek-chat"),
        enable_data_quality=False,
        checkpoint_db="/tmp/demo_feedback_state.db",
        thread_id=f"demo-feedback-{os.getpid()}",
    )
    print("   问题: 产品部有多少笔已完成订单\n")
    answer = await runner.run("产品部有多少笔已完成订单")
    print(f"\n   回答摘要: {str(answer)[:200]}")
    print("   ↳ 上方日志若出现「📥 自学习: 回流样例 [auto]」即闭环接线成功")
    await runner.aclose()


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("quality", "all"):
        demo_quality()
    if which in ("loop", "all"):
        await demo_loop()
    if which in ("live", "all"):
        await demo_live_agent()
    print()


if __name__ == "__main__":
    asyncio.run(main())
