import sqlite3
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationCapabilities
import mcp.server.stdio
import mcp.types as types
'''
哪些 Tool 适合放进 MCP Server？哪些留在 tools/ 里更好？
核心判断标准：Tool 是不是纯函数。

适合 MCP Server 的（搬出去）：
run_query          → 输入 SQL，输出数据。零内部状态。
list_tables        → 输入无，输出表名列表。
describe_table     → 输入表名，输出结构。

这三个是无状态的数据库能力。任何 Agent、IDE、平台接入都能用，不依赖项目上下文。

留在 tools/ 的：
analyze_results    → 分析逻辑和你的业务绑定（排名、趋势判断方式）
compare_periods    → "同比环比"的定义是你项目决定的
search_knowledge_base → 查的是你项目的知识库文档，不是通用能力
save_to_memory     → 依赖 VectorMemory + ConversationManager 内部状态
read_memory        → 同上，数据结构是你项目特有的
search_memory      → 同上，Self-Query 的解析逻辑是你定制的

这六个是有状态、有业务语义的。换个项目就没法用。

数据库查询搬进 MCP（通用），分析/记忆留在 tools/（项目特有）。
'''
DB_PATH = "db/demo.db"

server = Server("db-agent-server")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """告诉 Host 我会什么。"""
    return [
        types.Tool(
            name="run_query",
            description="在 SQLite 数据库上执行 SELECT 查询",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "只允许 SELECT 语句"
                    }
                },
                "required": ["sql"]
            }
        ),
        types.Tool(
            name="list_tables",
            description="列出所有表名",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="describe_table",
            description="查看表的列信息",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "表名"}
                },
                "required": ["table_name"]
            }
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent]:
    """执行 Tool 调用。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if name == "list_tables":
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return [types.TextContent(
            type="text",
            text=str([r["name"] for r in rows])
        )]

    elif name == "describe_table":
        rows = conn.execute(f"PRAGMA table_info({arguments['table_name']})")
        return [types.TextContent(
            type="text",
            text=str([dict(r) for r in rows.fetchall()])
        )]

    elif name == "run_query":
        sql = arguments["sql"].strip().upper()
        if not sql.startswith("SELECT"):
            return [types.TextContent(
                type="text",
                text="错误：只允许 SELECT 查询"
            )]
        try:
            rows = conn.execute(arguments["sql"]).fetchall()
            return [types.TextContent(
                type="text",
                text=str([dict(r) for r in rows])
            )]
        except Exception as e:
            return [types.TextContent(
                type="text",
                text=f"SQL 错误: {e}"
            )]

    conn.close()


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            InitializationCapabilities(
                sampling={}, experimental={}, roots={}
            ),
            instructions="DB Agent 数据库服务——提供 SQLite 查询和 schema 探索"
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())