# 应用层 SQL 改写而非数据库原生 RLS

权限逻辑集中在 Harness 层（entitlement.py），通过 `rewrite_sql()` 在 SQL 文本中注入 WHERE dept_id=X，而非依赖各数据源原生的行级安全策略（如 SQLite CREATE POLICY）。

**Considered Options**

- **数据库原生 RLS**：SQLite 支持 CREATE POLICY 做行级过滤，性能更好且覆盖复杂查询（子查询、UNION）。但策略绑定在具体数据库实例上，换库需重新配置。
- **应用层 SQL 改写**：权限元数据（角色、dept_id、白名单）集中在 `agent_roles` / `agent_users` 表。`rewrite_sql()` 在 run_query 执行前改 SQL 文本，换库换用户只改配置表，不动数据库策略。正则提取表名后在 GROUP BY / ORDER BY / LIMIT 前插入 WHERE/AND 子句。

**Consequences**

- 一套权限逻辑跨 SQLite + HBase + 知识库等多数据源统一生效。
- 不能覆盖复杂子查询、UNION、CTE 等场景——代码注释明确写了"生产环境应使用数据库 RLS Policy"。当前定位是**应用层补充**而非替代。
- 纯文本操作，不修改数据库数据——面试中需明确区分"改 SQL 文本"和"改数据库数据"。
