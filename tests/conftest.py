"""共享测试夹具：让测试不依赖 .env / 本机环境，离线可重复。

背景：查库类测试需要有 run_query 权限。权限收紧后仅 dba/manager 可访问数据库，
这里统一把测试会话钉为 dba（权限测试自己会显式 delenv/setenv 覆盖），
保证任何环境行为一致。
"""


import os

import pytest

# ── 必须在任何 test 模块被 import 之前执行 ──────────────────────────────
# 入口模块（server/main.py、main.py、app.py）在 import 时就会 load_dotenv()，
# 会把开发机 .env 的真实配置灌进测试进程，破坏"离线可重复"：
#   - OPENROUTER_API_KEY / JEV_API_KEY → Router 改走 Jev，跳过测试里 mock 的 LLM
#   - EMBEDDING_API_KEY / EMBEDDING_BASE_URL → 启动时 bootstrap 真去建向量索引
# python-dotenv 默认不覆盖已存在的 key，所以这里先占位成空串把它们挡在门外。
# 想跑真链路请用 tests/eval_*.py（那些脚本自己 load_dotenv），或显式设
# DB_AGENT_TEST_USE_DOTENV=1 把这层挡板关掉。
if not os.getenv("DB_AGENT_TEST_USE_DOTENV"):
    for _env_guard in (
        "OPENROUTER_API_KEY",
        "JEV_API_KEY",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
    ):
        os.environ[_env_guard] = ""


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """固定测试会话角色为 dba，消除对 .env 的隐式依赖。"""
    monkeypatch.setenv("AGENT_USER", "dba")
    # 向量库落盘到用例临时目录：不写共享的生产 chroma_db
    monkeypatch.setenv("VECTOR_PERSIST_DIR", str(tmp_path / "vector_store"))
    # 测试默认不走 Redis（保持离线可跑）；Redis 专项测试自己 setenv
    monkeypatch.delenv("REDIS_URL", raising=False)
    # 别在测试里触发启动建索引：会写共享持久化向量库（无 key 留空集合，
    # 有 key 会在测试中真调 embedding）。
    monkeypatch.setenv("KB_INDEX_AUTOBUILD", "0")


@pytest.fixture(autouse=True)
def _reset_global_guards():
    """每个用例前重置全局状态（熔断器 / 幂等守卫 / 告警器 / 会话存储），避免污染。"""
    from harness.constraints.idempotency import reset_idempotency_guard
    from harness.constraints.retry import reset_circuit_breaker
    from harness.observation.alerts import reset_alert_notifier
    reset_circuit_breaker()
    reset_idempotency_guard()
    reset_alert_notifier()
    import server.endpoints.sessions as sessions_mod
    sessions_mod._redis_client = None
    sessions_mod.clear_sessions()
    from harness.observation.ops_metrics import reset_metrics
    reset_metrics()
    # 知识库向量索引是模块级单例：清掉 Python 侧引用，避免上个用例的
    # 索引（可能指向生产 collection）泄漏到下一个用例。
    from harness.tools import knowledge as knowledge_mod
    knowledge_mod._kb_memory = None
    knowledge_mod.reset_runtime_docs()
