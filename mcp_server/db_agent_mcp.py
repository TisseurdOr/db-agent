"""db-agent MCP server —— 把整个 db-agent 包成一个 MCP 工具（路线 B）。

外部 MCP host（Claude Desktop / Cursor / Claude Code / 自研 host）连上这个 server 后，
用自然语言就能调用完整的 db-agent：多 Agent 编排、Schema 发现、SQL 生成、只读查询、
INSERT 脚本生成 + HITL 审批。Agent 自己的 LLM、Router、护栏、记忆、权限都在服务端跑完，
host 只负责收发，不需要有自己的 prompt 或编排逻辑。

暴露的工具：
  ask_db_agent       跑一轮完整 agent，返回答案 / SQL / 执行计划 / token 用量
  resume_db_agent    HITL 审批或补充澄清后，继续上一次暂停的查询
  db_agent_sessions  列出当前仍活跃（含等待审批）的会话
  db_agent_tools     列出底层 24 个原子工具，便于 host 了解能力边界

启动（stdio，供 Claude Desktop / Cursor 本地连接）：
    uv run python mcp_server/db_agent_mcp.py

启动（streamable HTTP，供远程 / 多客户端连接）：
    uv run python mcp_server/db_agent_mcp.py --transport streamable-http --port 8765

权限：走 db-agent 自己的 RBAC。默认用户取 DB_AGENT_MCP_USER → AGENT_USER → viewer；
也可以在每次 ask_db_agent 调用里显式传 user_id。

注意：本模块 import 时**不加载 .env、不建索引、不连库**——这些都在 `main()` 或首次
工具调用时才发生。否则 pytest 里 import 就会把 .env 灌进进程、污染其它用例。
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402


def _load_env() -> None:
    """加载 .env，并给 Opik 兜底 URL（缺 URL 时 SDK 会交互式询问，MCP 进程会卡死）。"""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    os.environ.setdefault("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
    os.environ.setdefault("OPIK_PROJECT_NAME", "db-agent")


@asynccontextmanager
async def _lifespan(_server):
    """server 生命周期：退出时在同一事件循环里关掉 checkpointer 连接。

    在同一个 loop 里关很关键——AsyncSqliteSaver 的连接绑定了创建它的 loop，
    换个 loop 去 close 关不掉，进程会卡在解释器退出阶段不结束。
    """
    try:
        yield {}
    finally:
        try:
            from server.runner_wrapper import runner_registry

            await runner_registry.close_all()
        except Exception:
            pass


mcp = FastMCP("db-agent", lifespan=_lifespan)

# 惰性初始化：MCP host 启动进程时要秒回，不能在建连阶段就去建向量索引 / 要 API key。
_client = None
_bootstrapped = False


def _ensure_bootstrapped() -> None:
    """首次真正调用工具时再 seed 数据 + 预热索引（与 server/main.py 行为一致）。"""
    global _bootstrapped
    if _bootstrapped:
        return
    from harness.bootstrap import bootstrap_data
    from server.storage import init_feedback_db

    bootstrap_data()
    init_feedback_db()
    _bootstrapped = True


def _get_client():
    """惰性创建 Anthropic client；缺 key 时抛 RuntimeError，由工具层转成友好错误。"""
    global _client
    if _client is None:
        from harness.llm_client import get_anthropic_client

        _client = get_anthropic_client()
    return _client


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def _drive(runner, *, mode: str, question: str = "", approved: bool = True, clarified_query: str = "") -> dict:
    """跑一轮 agent，并把 SSE 事件流收敛成一个结果 dict。

    MCP 工具是请求-响应式的（没有 SSE 通道），所以这里等整轮跑完再一次性返回：
    中间步骤折叠进 ``steps``，最终答案 / SQL / 计划 / token 放进顶层字段。
    """
    from server.runner_wrapper import StreamingRunner

    queue: asyncio.Queue = asyncio.Queue()
    streaming = StreamingRunner(runner)
    if mode == "ask":
        await streaming.run_streaming(question, queue)
    else:
        await streaming.resume_streaming(approved, queue, clarified_query)

    steps: list[dict] = []
    result: dict = {"status": "error", "message": "agent 没有返回任何结果"}
    while not queue.empty():
        name, data = queue.get_nowait()
        if name == "step_start":
            steps.append({"node": data.get("node"), "task": data.get("task"), "status": "start"})
        elif name == "step_end":
            steps.append({
                "node": data.get("node"),
                "task": data.get("task"),
                "status": "end",
                "elapsed": data.get("elapsed"),
            })
        elif name == "interrupt":
            result = {"status": "needs_approval", "approval": data.get("data") or {}, "steps": steps}
        elif name == "error":
            result = {
                "status": "error",
                "message": data.get("message", ""),
                "detail": data.get("detail", ""),
                "steps": steps,
            }
        elif name == "done":
            info = runner.get_execution_info()
            result = {
                "status": "ok",
                "answer": data.get("answer", ""),
                "sql": data.get("sql") or info.get("sql", ""),
                "plan": data.get("plan") or info.get("plan", []),
                "tokens": data.get("tokens") or info.get("tokens", 0),
                "elapsed": data.get("total_elapsed"),
                "trace_id": data.get("trace_id", ""),
                "steps": steps,
            }
    return result


def _target_user(user_id: str) -> str:
    if user_id.strip():
        return user_id.strip()
    if os.getenv("DB_AGENT_MCP_USER"):
        return os.environ["DB_AGENT_MCP_USER"]
    from harness.constraints.entitlement import resolve_user_id

    return resolve_user_id(None)


@mcp.tool()
async def ask_db_agent(
    question: str,
    session_id: str = "mcp-default",
    user_id: str = "",
    enable_data_quality: bool = False,
) -> str:
    """用自然语言向 db-agent 提一个数据问题，返回答案、SQL 与执行计划。

    可以问：查数 / 对账 / 看表结构 / 生成只读 SQL / 生成 INSERT 脚本。
    如果问题需要写库，会返回 status=needs_approval，此时把 SQL 交给用户确认，
    再用 resume_db_agent 传 approved=true 继续执行。

    参数:
        question: 自然语言问题，例如「华东区上个月销售额 top10 门店」
        session_id: 会话标识；同一个 session 共享上下文与待审批状态（默认 mcp-default）
        user_id: RBAC 用户；留空则取 DB_AGENT_MCP_USER / AGENT_USER（默认 viewer）
        enable_data_quality: 是否在首次查询自动跑数据质量检查（默认关，首答更快）
    """
    from harness.config import DEFAULT_MODEL

    try:
        _ensure_bootstrapped()
        client = _get_client()
    except Exception as e:  # 缺 key / 数据未就绪
        return _json({"status": "error", "message": str(e)})

    target_user = _target_user(user_id)
    try:
        from server.runner_wrapper import runner_registry

        runner = await runner_registry.get_or_create(
            session_id=session_id,
            client=client,
            model=DEFAULT_MODEL,
            enable_data_quality=enable_data_quality,
            user_id=target_user,
        )
        payload = await _drive(runner, mode="ask", question=question)
    except Exception as e:
        return _json({"status": "error", "message": f"{type(e).__name__}: {e}"})

    payload["session_id"] = session_id
    payload["user"] = target_user
    return _json(payload)


@mcp.tool()
async def resume_db_agent(
    session_id: str = "mcp-default",
    approved: bool = True,
    clarified_query: str = "",
) -> str:
    """审批上一次 ask_db_agent 暂停的写操作，或补充澄清信息后继续执行。

    参数:
        session_id: 与 ask_db_agent 相同的会话标识
        approved: true = 批准执行（如写库 / 高成本查询），false = 拒绝
        clarified_query: 若暂停原因是需要澄清，把用户的补充说明放这里
    """
    try:
        from server.runner_wrapper import runner_registry

        runner = runner_registry.get(session_id)
        if runner is None:
            return _json({
                "status": "error",
                "message": f"会话 {session_id} 没有待审批或待澄清的查询，请先调用 ask_db_agent",
            })
        payload = await _drive(runner, mode="resume", approved=approved, clarified_query=clarified_query)
    except Exception as e:
        return _json({"status": "error", "message": f"{type(e).__name__}: {e}"})

    payload["session_id"] = session_id
    return _json(payload)


@mcp.tool()
def db_agent_sessions() -> str:
    """列出当前仍活跃（含等待审批）的 db-agent 会话，便于续接上下文。"""
    from server.runner_wrapper import runner_registry

    return _json({"sessions": runner_registry.list_sessions()})


@mcp.tool()
def db_agent_tools() -> str:
    """列出 db-agent 底层可用的原子工具（名字 + 说明），便于了解能力边界。"""
    from harness.orchestration.single.tools_bundle import TOOLS

    return _json({
        "count": len(TOOLS),
        "tools": [
            {"name": t.get("name"), "description": t.get("description", "")}
            for t in TOOLS
            if isinstance(t, dict)
        ],
    })


def _shutdown() -> None:
    """进程退出兜底：lifespan 没跑到的场景（被 import 后直接退出）也尽量清干净。"""
    try:
        from server.runner_wrapper import runner_registry

        asyncio.run(runner_registry.close_all())
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="db-agent MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="stdio 给本地 host；streamable-http 给远程/多客户端",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 传输时的监听地址")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 传输时的端口")
    args = parser.parse_args()

    # 真正启动服务时才读 .env——把配置副作用关在 server 启动路径里。
    _load_env()
    atexit.register(_shutdown)

    if args.transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
