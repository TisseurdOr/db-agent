"""Opik Agent Playground 入口进程。

Playground 通过参数 introspection 发现入口函数；实例方法上的
`@track_entrypoint` 会把 `self` 标成 required，Run 按钮一直灰掉。
这里用模块级 `ask(query: str)` 作为唯一 entrypoint。

用法:
    source .venv/bin/activate
    opik endpoint --project "db-agent" -- python scripts/opik_playground.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from anthropic import Anthropic

from db.seed import init_db
from harness.config import DEFAULT_MODEL
from harness.context.schema_discovery import get_schema_discovery
from harness.context.template_matcher import init_metric_registry
from harness.observation.opik_tracing import flush_opik, track_entrypoint, wrap_anthropic_client
from harness.orchestration.multi.orchestrator import MultiAgentRunner
from harness.tools.hbase import _seed_hbase_store

_runner: MultiAgentRunner | None = None
_loop = asyncio.new_event_loop()


def _start_loop() -> None:
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def _boot() -> MultiAgentRunner:
    init_db()
    _seed_hbase_store()
    init_metric_registry()
    try:
        get_schema_discovery().build_index()
    except Exception as e:
        print(f"[schema_discovery] 索引构建跳过: {e}")

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
    )
    client = wrap_anthropic_client(client)
    model = DEFAULT_MODEL

    fut = asyncio.run_coroutine_threadsafe(
        MultiAgentRunner.create(client, model=model, enable_data_quality=False),
        _loop,
    )
    return fut.result(timeout=120)


@track_entrypoint
def ask(query: str) -> str:
    """Opik Playground 入口：只收自然语言问题。"""
    if _runner is None:
        return "[error] runner not initialized"
    fut = asyncio.run_coroutine_threadsafe(_runner.run(query), _loop)
    result: Any = fut.result(timeout=300)
    flush_opik()
    if isinstance(result, dict) and result.get("__interrupt__"):
        return f"[HITL] 需要审批: {result.get('data')}"
    return str(result)


def main() -> None:
    global _runner
    threading.Thread(target=_start_loop, daemon=True).start()
    _runner = _boot()
    # 注册 entrypoint，供 Opik Playground introspection 发现
    _ = ask
    print("Opik playground ready. Entrypoint: ask(query). Keep this process running.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        flush_opik()
        _loop.call_soon_threadsafe(_loop.stop)


if __name__ == "__main__":
    main()
