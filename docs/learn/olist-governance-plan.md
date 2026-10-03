# Olist 当前数据表的数据治理方案

## 1. 治理目标

对现有 ODS、DIM、DWD、DWS、ADS 五层建立最小可运行的治理闭环：

1. 每张表有负责人和用途说明。
2. 每个指标有统一名称、口径、单位和血缘。
3. 每条关键数据链路有质量规则和告警。
4. 每个角色只能访问职责范围内的数据。
5. 每次数据版本更新都能追溯、复现和回滚。

## 2. 表资产目录与负责人

| 层 | 表 | 主要用途 | 默认负责人 | 数据等级 |
|---|---|---|---|---|
| ODS | `ods_olist_orders` | 原始订单 | 数据接入负责人 | 内部/准敏感 |
| ODS | `ods_olist_order_items` | 原始订单商品 | 数据接入负责人 | 内部 |
| ODS | `ods_olist_customers` | 原始客户 | 数据接入负责人 | 敏感 |
| ODS | `ods_olist_products` | 原始商品 | 数据接入负责人 | 内部 |
| ODS | `ods_olist_sellers` | 原始卖家 | 数据接入负责人 | 内部 |
| ODS | `ods_olist_payments` | 原始支付 | 数据接入负责人 | 敏感 |
| ODS | `ods_olist_reviews` | 原始评价 | 数据接入负责人 | 敏感 |
| ODS | `ods_olist_geolocation` | 原始地域 | 数据接入负责人 | 敏感 |
| ODS | `ods_olist_category_translation` | 品类翻译 | 数据接入负责人 | 公开 |
| DIM | `dim_olist_date` | 统一日期口径 | 数据建模负责人 | 公开 |
| DIM | `dim_olist_customer` | 客户维度 | 主数据负责人 | 敏感 |
| DIM | `dim_olist_product` | 商品维度 | 主数据负责人 | 内部 |
| DIM | `dim_olist_seller` | 卖家维度 | 主数据负责人 | 内部 |
| DIM | `dim_olist_geography` | 地域维度 | 主数据负责人 | 敏感 |
| DWD | `dwd_olist_order_items` | 订单商品明细事实 | 数仓开发负责人 | 内部 |
| DWD | `dwd_olist_payments` | 支付事实 | 数仓开发负责人 | 敏感 |
| DWD | `dwd_olist_reviews` | 评价事实 | 数仓开发负责人 | 敏感 |
| DWD | `dwd_olist_order_fulfillment` | 履约事实 | 数仓开发负责人 | 内部 |
| DWS | `dws_olist_sales_daily` | 日销售汇总 | 指标负责人 | 内部 |
| DWS | `dws_olist_sales_period` | 周期销售汇总 | 指标负责人 | 内部 |
| ADS | `ads_olist_metric_catalog` | 指标目录 | 指标负责人 | 公开 |
| ADS | `ads_olist_period_metrics` | 期间指标 | 应用负责人 | 内部 |
| ADS | `ads_olist_period_comparison` | 期间对比结果 | 应用负责人 | 内部 |

一个人维护时可以兼任多个角色，但职责必须分开记录，避免自己定义、自己开发、自己验收后无法追责。

## 3. 分层治理规则

### ODS

- 保留源文件、数据集版本、文件哈希和下载时间。
- 每行列必须有 `_dataset_version`、`_batch_id`、`_ingested_at`。
- 不修正源数据，只在 DIM/DWD 做清洗。
- 监控字段新增、删除、类型变化和空值率变化。
- 明确许可证、保留期限和是否允许商用。

### DIM

- 主键必须唯一：客户、商品、卖家、日期。
- 日期维度必须连续，不能缺失月份。
- 地域维度按邮编前缀聚合，保留聚合规则。
- 所有事实表必须以标准维度为属性来源，禁止各自复制一套客户/商品口径。
- 后续引入历史属性变化时，明确使用全量覆盖还是 SCD2。

### DWD

- 先写清楚每张表的事实粒度。
- `dwd_olist_order_items`：一行一个订单商品。
- `dwd_olist_payments`：一行一次支付。
- `dwd_olist_reviews`：一行一条评价。
- `dwd_olist_order_fulfillment`：一行一个订单。
- 金额使用 `DECIMAL/REAL` 统一单位，明确是否含运费。
- 订单、商品、客户、卖家必须能关联到主数据。
- 取消、不可用订单在事实层保留，在指标层通过有效销售标记过滤。

### DWS

- 每个汇总指标必须能回算到 DWD。
- 不同周期使用统一日期维度。
- 明确订单量是 `COUNT(DISTINCT order_id)`，不是明细行数。
- 按品类汇总订单量时可能重复计数，必须在指标定义中说明。
- 比较月、季度、半年时必须检查 `day_count` 和 `expected_days`。

### ADS

- `ads_olist_metric_catalog` 是唯一指标定义入口。
- `ads_olist_period_metrics` 的指标名称必须出现在指标目录。
- `ads_olist_period_comparison` 必须输出零基期、缺失期和不完整期警告。
- 每次重建写入 `data_version`。
- ADS 是应用契约，字段变化必须先做影响分析。

## 4. 指标治理

以 `gross_sales` 为例，指标卡片至少包含：

```text
指标名称：gross_sales
业务名称：成交金额
定义：有效订单明细的 price + freight_value
排除：canceled、unavailable
粒度：订单商品
单位：BRL
上游：dwd_olist_order_items
下游：dws_olist_sales_daily、dws_olist_sales_period、ads_olist_period_metrics
公式：SUM(price + freight_value)
负责人：指标负责人
版本：dataset_version + data_version
```

新增指标必须同时提交：

1. 指标目录记录。
2. SQL 实现。
3. 血缘关系。
4. 数据质量规则。
5. 评测样例。
6. 权限和发布说明。

## 5. 数据质量规则

| 编号 | 规则 | 目标表 | 失败条件 | 级别 |
|---|---|---|---|---|
| DQ01 | 主键唯一 | 所有 DIM | 重复主键 | 高 |
| DQ02 | 日期连续 | `dim_olist_date` | 日期断档 | 高 |
| DQ03 | 订单外键完整 | `dwd_olist_order_items` | 无对应订单 | 高 |
| DQ04 | 金额可回算 | `dwd_olist_order_items` | `item_value != price + freight_value` | 高 |
| DQ05 | 支付金额非负 | `dwd_olist_payments` | `payment_value < 0` | 高 |
| DQ06 | 评分范围 | `dwd_olist_reviews` | 评分不在 1～5 | 中 |
| DQ07 | 周期完整性 | `ads_olist_period_metrics` | `day_count < expected_days` | 高 |
| DQ08 | 指标目录覆盖 | `ads_olist_period_metrics` | 指标名不在目录 | 高 |
| DQ09 | 零基期保护 | `ads_olist_period_comparison` | 基期为 0 却输出增长率 | 高 |
| DQ10 | 刷新时效 | 所有 ADS | 超过 SLA 未刷新 | 中 |

示例 SQL：

```sql
-- DQ01：商品维度主键唯一
SELECT product_id, COUNT(*) AS n
FROM dim_olist_product
GROUP BY product_id
HAVING COUNT(*) > 1;

-- DQ03：订单明细必须有订单
SELECT COUNT(*) AS orphans
FROM dwd_olist_order_items i
LEFT JOIN ods_olist_orders o ON o.order_id = i.order_id
WHERE o.order_id IS NULL;

-- DQ07：周期不完整
SELECT period_type, period_key, day_count, expected_days
FROM ads_olist_period_metrics
WHERE day_count < expected_days
ORDER BY expected_days - day_count DESC;
```

## 6. 血缘治理

当前血缘包含：

- demo.db 的 SQL 外键血缘。
- warehouse.db 的 ETL 血缘。
- `ads_olist_metric_catalog` 到期间指标的参考血缘。
- 上游和下游查询。
- ETL 业务说明。

使用规则：

1. 新增或修改表时，必须同步更新血缘。
2. 修改 ODS 字段前，先查询全部下游。
3. 修改指标时，先查字段级影响范围。
4. 发布 ADS 前，必须能回算到 DWS 和 DWD。
5. 血缘边区分 `FK`、`ETL` 和 `REF`，不能把参考关系伪装成物理加工。

当前缺口：还没有字段级血缘。下一步应解析 DWS/ADS SQL，记录每个字段来自哪些上游字段。

## 7. 权限治理

建议权限：

| 角色 | ODS | DIM | DWD | DWS | ADS | SQL Console |
|---|---|---|---|---|---|---|
| 数仓管理员 | 全量 | 全量 | 全量 | 全量 | 全量 | 可写 |
| 数据开发 | 读 | 读 | 读 | 读 | 读 | 只读 |
| 分析师 | 禁止 | 读 | 读 | 读 | 读 | 只读 |
| 业务用户 | 禁止 | 部分 | 禁止 | 汇总 | 读 | 禁止 |
| 外部用户 | 禁止 | 禁止 | 禁止 | 授权指标 | 授权指标 | 禁止 |

敏感数据至少包括：客户标识、地域、支付、评价文本和联系信息。

## 8. 生命周期与版本

- 原始 CSV 按数据集版本保存，不覆盖旧版本。
- `warehouse.db` 可重建，重建脚本必须幂等。
- ODS 使用 `_batch_id` 标识批次。
- ADS 使用 `data_version` 标识生成版本。
- 数据质量不通过时，不允许覆盖上一版 ADS。
- 保留最近一个可用版本，支持回滚。

## 9. 单人治理运行节奏

每天：

- 自动跑主键、外键、金额、周期完整性检查。
- 记录失败数量和影响范围。

每周：

- 处理失败规则。
- 检查新增或异常指标。
- 更新血缘和指标目录。

每月：

- 复审权限。
- 复审保留策略和版本。
- 输出一页治理报告。

## 10. 推荐实施顺序

1. 建立表资产目录和负责人。
2. 建立指标目录，统一定义 `gross_sales`、`net_sales`、`order_count`、`item_count`。
3. 为每条 ETL 链路补业务描述和血缘。
4. 落地 DQ01～DQ10 质量规则。
5. 建立角色权限矩阵。
6. 引入字段级血缘。
7. 固化每日、每周、每月治理节奏。
