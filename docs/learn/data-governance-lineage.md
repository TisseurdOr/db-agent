# 数据治理与数据血缘学习手册（Olist 实战版）

## 1. 先看数据分层

### ODS：原始数据层

职责：尽量保留源数据原貌，负责可追溯地落地。

本项目的 ODS 表：

- `ods_olist_customers`
- `ods_olist_geolocation`
- `ods_olist_order_items`
- `ods_olist_payments`
- `ods_olist_reviews`
- `ods_olist_orders`
- `ods_olist_products`
- `ods_olist_sellers`
- `ods_olist_category_translation`

治理重点：

- 来源登记：数据来自哪个数据集、哪个版本。
- 批次追溯：`_batch_id`、`_ingested_at`。
- 许可证与隐私：是否允许使用、是否含个人信息。
- Schema Drift：源字段改名、类型变化、缺失值。
- 保留策略：原始数据保留多久。

### DIM：维度层

职责：描述业务实体，为事实表提供统一属性。

本项目的 DIM 表：

- `dim_olist_date`：日期、月份、季度、半年、年度。
- `dim_olist_customer`：客户与地区属性。
- `dim_olist_product`：商品与品类属性。
- `dim_olist_seller`：卖家和所在地。
- `dim_olist_geography`：邮编、城市、州、经纬度。

治理重点：

- 主键唯一性。
- 维度和业务含义保持一致。
- 命名标准统一。
- 慢变维处理：属性变化时保留历史还是覆盖当前值。
- 维表复用：多个事实表应共享统一维度。

### DWD：明细事实层

职责：完成清洗和关联，保留最细业务粒度。

本项目的 DWD 表：

- `dwd_olist_order_items`：订单商品明细。
- `dwd_olist_payments`：支付明细。
- `dwd_olist_reviews`：评价明细。
- `dwd_olist_order_fulfillment`：订单履约与延迟。

治理重点：

- 明确事实粒度，例如“一行一个订单商品”。
- 外键完整性。
- 金额、状态和时间口径统一。
- 异常值、重复值和空值处理。
- 敏感字段分级和脱敏。

### DWS：汇总层

职责：围绕主题和常用分析维度做预聚合。

本项目的 DWS 表：

- `dws_olist_sales_daily`：按日期、州、品类和订单状态汇总销售。
- `dws_olist_sales_period`：月、季度、半年和年度指标。

治理重点：

- 聚合粒度清晰。
- 分子、分母和去重规则明确。
- 汇总结果可回算到 DWD。
- 不同周期的口径一致。
- 指标命名和单位标准统一。

### ADS：应用层

职责：面向具体应用场景提供最终数据。

本项目的 ADS 表：

- `ads_olist_metric_catalog`：指标名称、定义和单位。
- `ads_olist_period_metrics`：期间指标。
- `ads_olist_period_comparison`：相邻期变化率与质量告警。

治理重点：

- 指标定义可追溯。
- 应用层字段和界面语义一一对应。
- 控制访问权限。
- 监控 SLA、刷新时间和数据版本。
- 指标异常时能追溯到 DWS、DWD 和 ODS。

## 2. 一条完整业务链路

```text
ods_olist_orders
  + ods_olist_order_items
  + dim_olist_product
  + dim_olist_customer
  + dim_olist_seller
        ↓
dwd_olist_order_items
        ↓
dws_olist_sales_daily
        ↓
dws_olist_sales_period
        ↓
ads_olist_period_metrics
        ↓
ads_olist_period_comparison
```

成交金额的业务定义：

```text
gross_sales =
SUM(order_item.price + order_item.freight_value)
WHERE order_status NOT IN ('canceled', 'unavailable')
```

这说明数据治理不能只描述“表是什么”，还要说明“指标怎么算”。

## 3. 数据治理的六个核心问题

### 元数据与口径

这张表是什么？字段是什么？指标怎么算？谁负责？

### 数据血缘

数据从哪里来？经过什么加工？会影响哪些下游？

### 数据质量

是否完整、准确、唯一、及时、一致、有效？

### 权限与安全

谁能看？谁能改？敏感数据如何处理？

### 标准与主数据

表名、字段名、状态值、指标单位和维度编码是否统一？

### 生命周期

保留多久？什么时候归档？旧版本如何回滚？

## 4. 数据血缘的四种层级

- 表级血缘：表与表之间的依赖。
- 字段级血缘：目标字段来自哪些源字段。
- 任务级血缘：ETL 或调度任务之间的依赖。
- 业务血缘：报表指标来自哪些业务过程。

本项目当前实现表级血缘。下一步可做字段级血缘。

## 5. 血缘的典型用途

### 影响分析

如果 `orders.total` 改名，需要检查：

```text
ods_olist_orders
→ dwd_olist_order_items
→ dws_olist_sales_daily
→ ads_olist_period_metrics
```

### 故障定位

如果 ADS 双期对比异常，沿着上游逐层检查：

```text
ads_olist_period_comparison
← ads_olist_period_metrics
← dws_olist_sales_period
← dws_olist_sales_daily
← dwd_olist_order_items
```

### 合规审计

查看敏感数据经过哪些表、任务和应用。

### 变更管理

上线前评估影响范围、权限和回滚方案。

## 6. 练习

1. 修改 `orders.total` 的口径，列出受影响的字段、表和指标。
2. 解释为什么 2018-H2 成交金额下降，并判断是否是数据不完整导致。
3. 为 `dwd_olist_reviews` 设计 3 条数据质量规则。
4. 为“运费率”增加字段级血缘。
5. 写出 `ads_olist_period_comparison.growth_rate` 的公式和上游依赖。
