"""MCP Server: 把 db-agent 的数据库能力暴露给任何 MCP Host。

启动方式:
    uv run python mcp_servers/db_server.py

Claude Desktop 配置 (claude_desktop_config.json):
    {
      "mcpServers": {
        "db-agent": {
          "command": "uv",
          "args": ["run", "python", "mcp_servers/db_server.py"],
          "cwd": "/Users/cailin/junior-to-senior/db-agent"
        }
      }
    }
"""

import sqlite3
import os
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "demo.db")

server = Server("db-agent-server")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_tables",
            description="列出 SQLite 数据库中所有的表名",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="describe_table",
            description="查看指定表的结构（列名、类型）",
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "要查看的表名",
                    },
                },
                "required": ["table_name"],
            },
        ),
        types.Tool(
            name="run_query",
            description="在 SQLite 数据库上执行 SELECT 查询（只读）",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL SELECT 语句",
                    },
                },
                "required": ["sql"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        if name == "list_tables":
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            return [types.TextContent(
                type="text",
                text=str([r["name"] for r in rows]),
            )]

        elif name == "describe_table":
            table = arguments["table_name"]
            rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            return [types.TextContent(
                type="text",
                text=str([dict(r) for r in rows]),
            )]

        elif name == "run_query":
            sql = arguments["sql"].strip()
            if not sql.upper().startswith("SELECT"):
                return [types.TextContent(
                    type="text",
                    text="错误：只允许 SELECT 查询。当前语句被拒绝。",
                )]
            rows = conn.execute(sql).fetchall()
            text = str([dict(r) for r in rows])
            if len(text) > 4000:
                text = text[:4000] + f"...(共 {len(rows)} 行，已截断)"
            return [types.TextContent(type="text", text=text)]

    except Exception as e:
        return [types.TextContent(type="text", text=f"错误: {e}")]
    finally:
        conn.close()


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
