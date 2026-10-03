# TOOLS.md — 操作 Vault 的工具说明

## 1. Obsidian CLI（本地，需要 Obsidian 已打开）

命令入口：`obsidian`（需先安装 CLI，见 https://help.obsidian.md/cli）

```bash
obsidian vault="<Vault名>" read file="<笔记名>"     # 读笔记（含正文）
obsidian vault="<Vault名>" search query="关键词" limit=10
obsidian vault="<Vault名>" backlinks file="<笔记名>"   # 反向链接
obsidian vault="<Vault名>" create name="新笔记" content="正文" silent
obsidian vault="<Vault名>" append file="笔记" content="追加内容"
obsidian vault="<Vault名>" property:set name="status" value="done" file="笔记"
obsidian vault="<Vault名>" tags sort=count counts
```

- `file=` 按笔记名解析（等价 wikilink），`path=` 用 Vault 根相对路径。
- 默认操作最近聚焦的 Vault；多 Vault 用 `vault=` 指定。

## 2. Obsidian 本体（本地 GUI）

- 负责维护链接、属性、索引、图谱与插件（dataview）。
- Agent 写入正文后，由 Obsidian 负责重建索引；不要手动改 `.obsidian/` 内部状态文件。

## 3. MCP（远程阶段，团队共享）

- 远程 Obsidian 插件提供 MCP Server（HTTP，示例端口 8301）；Agent 侧是 MCP Client。
- 读取返回：正文 + `links / backlinks / tags / frontmatter / unresolvedLinks`。
- 写入：`vault_patch` 携带 `ifMatch=version`；返回 `412` = 冲突，重新读取再提交。

## 4. 本 Vault 专属约定

- 规则见 [AGENTS.md](AGENTS.md)；主题索引模板见 [ai/主题Index模板.md](ai/主题Index模板.md)。
- 工作流：知识维护 / 深度调研 见 `ai/` 目录。
