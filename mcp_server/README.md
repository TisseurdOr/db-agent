# db-agent MCP Server

把**整个 db-agent** 包成 MCP 工具：外部 host（Claude Desktop / Cursor / Claude Code / 自研客户端）
用自然语言就能调用完整的 agent —— 多 Agent 编排、Schema 发现、SQL 生成、只读查询、
INSERT 脚本生成 + HITL 审批，全部在服务端跑完，host 不需要自带 prompt 或编排逻辑。

> 和 `sidecar/mcp_servers/db_server.py` 的区别：那个是早期原型，只暴露 3 个只读工具、
> 写死 `db/demo.db`，已被归档。这里是正式版——暴露的是**完整 agent**，工具数 1 个
> （`ask_db_agent`），能力由 agent 自己在服务端调度。

## 暴露的工具

| 工具 | 作用 |
|------|------|
| `ask_db_agent(question, session_id, user_id, enable_data_quality)` | 跑一轮完整 agent，返回 `answer` / `sql` / `plan` / `tokens` / `steps` |
| `resume_db_agent(session_id, approved, clarified_query)` | HITL 审批或补充澄清后，继续上一次暂停的查询 |
| `db_agent_sessions()` | 列出当前活跃（含等待审批）的会话 |
| `db_agent_tools()` | 列出底层 24 个原子工具，便于了解能力边界 |

`ask_db_agent` 返回的 JSON 里 `status` 有三种：

- `ok` —— 正常完成，`answer` / `sql` / `plan` / `tokens` 都有值
- `needs_approval` —— 命中 HITL（要写库、或置信度不足），`approval` 字段带审批上下文；
  把 SQL 给用户确认后，用 `resume_db_agent(approved=true)` 继续
- `error` —— 参数/权限/依赖出错，`message` 说明原因（例如缺 `ANTHROPIC_API_KEY`）

## 接入方式

### Claude Desktop

编辑 `claude_desktop_config.json`（macOS 在 `~/Library/Application Support/Claude/`）：

```json
{
  "mcpServers": {
    "db-agent": {
      "command": "uv",
      "args": ["run", "python", "mcp_server/db_agent_mcp.py"],
      "cwd": "/Users/cailin/junior-to-senior/db-agent",
      "env": { "DB_AGENT_MCP_USER": "dba" }
    }
  }
}
```

### Cursor

`.cursor/mcp.json`（项目级）或 `~/.cursor/mcp.json`（全局）：

```json
{
  "mcpServers": {
    "db-agent": {
      "command": "uv",
      "args": ["run", "python", "mcp_server/db_agent_mcp.py"],
      "cwd": "/Users/cailin/junior-to-senior/db-agent",
      "env": { "DB_AGENT_MCP_USER": "dba" }
    }
  }
}
```

### Claude Code

```bash
claude mcp add db-agent -- uv run python mcp_server/db_agent_mcp.py
```

### 远程 / 多客户端（streamable HTTP）

```bash
uv run python mcp_server/db_agent_mcp.py --transport streamable-http --host 127.0.0.1 --port 8765
# MCP endpoint: http://127.0.0.1:8765/mcp
```

## 手动验证

```bash
uv run python mcp_server/db_agent_mcp.py    # stdio，回车无输出属正常，等 host 来连
```

跑自动化测试（不真调 LLM）：

```bash
uv run pytest tests/test_mcp_server.py -q
```

## 权限与配置

- **RBAC 走 db-agent 自己的**：默认用户 `DB_AGENT_MCP_USER` → `AGENT_USER` → `viewer`；
  也可以在每次 `ask_db_agent` 调用里显式传 `user_id`。
  `viewer` 查不了业务库，需要查数就把 `DB_AGENT_MCP_USER` 设成 `dba` / `manager` / `analyst`。
- **API key**：沿用 `.env` 里的 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`。
  缺 key 时 server 仍能启动，调用时才返回带说明的 `error`（不会让 host 连不上）。
- **写操作有边界**：生成 INSERT 脚本走 HITL，`ask_db_agent` 会先返回 `needs_approval`，
  必须显式 `resume_db_agent(approved=true)` 才继续 —— 这条护栏在 MCP 路径上没有被绕过。

## 已知限制

MCP 工具是**请求-响应式**的，没有 SSE 通道，所以：

- 中间步骤被折叠进返回值的 `steps` 字段，不是实时流式推送（host 侧看不到逐字输出）。
- 一轮长查询要等整轮跑完才返回，host 的超时时间要留够（默认实测一轮 10~20s）。
- 并发会话在同一进程内串行地各跑各的 `session_id`，HITL 状态按 `session_id` 定位。
