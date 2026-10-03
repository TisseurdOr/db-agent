# Olist 数仓与演示业务库分离

Olist 五层数据仓库使用独立的 `db/warehouse.db`，不写入会被测试和 seed 重置的 `db/demo.db`。演示业务库关注权限、HITL、自愈和评测；Olist 仓库关注 ODS→DIM→DWD→DWS→ADS 分层与双期指标查询。分离后测试可以安全重置业务库，同时保留约 500 MB 的公共数仓，代价是跨库 JOIN 需要由专用工具负责。
