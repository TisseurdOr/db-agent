"""Schema Discovery: 自动表发现（Schema Linking）。

对标 nl-db-agent 和 OpenChatBI 的 schema-linking：
- 启动时预计算每张表每个字段的 embedding
- 查询时向量检索 top-K 相关字段 → 组装精简 schema context
- 替代手动 list_tables + describe_table 两步流程

面试话术：
"做了 schema-linking——用户问'华东销售'，系统自动检索
sales、orders、regions 三张表的相关字段，而不是把全部表丢给 LLM。"
"""

import sqlite3
import os
from pathlib import Path
from typing import Optional

import chromadb
from openai import OpenAI

from db.seed import DB_PATH

# 表+字段的中文注释，让 embedding 更准确
_COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "departments": {
        "id": "部门ID，主键",
        "name": "部门名称，如'华东销售部'、'技术研发部'",
        "budget": "部门年度预算（万元）",
        "headcount": "部门编制人数",
    },
    "employees": {
        "id": "员工ID，主键",
        "name": "员工姓名",
        "dept_id": "所属部门ID，外键关联departments.id",
        "title": "职位，如'销售经理'、'高级工程师'",
        "salary": "月薪（元）",
        "hire_date": "入职日期 YYYY-MM-DD",
        "status": "员工状态: active/inactive",
    },
    "products": {
        "id": "产品ID，主键",
        "name": "产品名称",
        "category": "产品类别，如'软件'、'硬件'、'服务'",
        "unit_price": "单价（元）",
        "cost": "成本（元）",
    },
    "customers": {
        "id": "客户ID，主键",
        "name": "客户公司名称",
        "region": "所在区域: 华东/华南/华北/华中/西南/西北",
        "city": "所在城市",
        "industry": "所属行业，如'金融'、'互联网'、'制造'",
        "tier": "客户等级: A/B/C",
    },
    "orders": {
        "id": "订单ID，主键",
        "dept_id": "负责部门ID，外键关联departments.id",
        "product_id": "产品ID，外键关联products.id",
        "customer_id": "客户ID，外键关联customers.id",
        "total": "订单金额（元）",
        "quantity": "购买数量",
        "status": "订单状态: completed/pending/cancelled",
        "created_at": "创建时间 YYYY-MM-DD HH:MM:SS",
    },
    "ods_orders_hive": {
        "dt": "分区日期 YYYY-MM-DD",
        "region": "分区区域: 华东/华南/华北",
        "order_id": "订单ID",
        "customer_id": "客户ID",
        "product_id": "产品ID",
        "total": "订单金额",
        "quantity": "购买数量",
        "status": "订单状态",
        "created_at": "创建时间",
        "store_format": "存储格式: PARQUET/ORC",
    },
    "dwd_user_events": {
        "dt": "分区日期",
        "user_id": "用户ID",
        "event_type": "事件类型: page_view/click/purchase/add_cart",
        "event_props": "事件属性 JSON（模拟Hive MAP类型）",
        "event_time": "事件时间",
        "store_format": "存储格式: ORC",
    },
    "dim_products_hive": {
        "product_id": "产品ID",
        "name": "产品名称",
        "category": "产品类别",
        "unit_price": "单价",
        "supplier": "供应商",
        "tags": "标签 JSON数组（模拟Hive ARRAY类型）",
        "store_format": "存储格式: PARQUET",
    },
}

# collection 名。
# v2: 加入值级索引（低基数 TEXT 列的真实取值进 embedding 文本和 metadata）。
# 改名让旧索引自动失效重建一次，旧 collection 留着无害。
_COLLECTION = "schema_columns_v2"

# 值级索引（value-level indexing）：
#   用户问"华东的订单"，模型怎么知道 region 列存的是 '华东' 还是 'east'？
#   对低基数 TEXT 列（≤15 个不同值）自动 SELECT DISTINCT，把真实取值
#   写进字段描述——WHERE 条件直接抄真实值，专名题不再靠猜。
#   高基数列（如客户名）不做——枚举没有意义还会撑爆 embedding 文本。
_VALUE_PROFILE_MAX = 15
_TEXT_TYPES = ("TEXT", "CHAR", "VARCHAR")


def profile_column_values(conn: sqlite3.Connection, table: str, column: str,
                          col_type: str) -> list[str]:
    """低基数 TEXT 列返回真实取值列表；高基数/非文本/查询失败返回 []。"""
    if not any(t in (col_type or "").upper() for t in _TEXT_TYPES):
        return []
    try:
        rows = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT {_VALUE_PROFILE_MAX + 1}'
        ).fetchall()
    except sqlite3.Error:
        return []
    values = [str(r[0]) for r in rows]
    if not values or len(values) > _VALUE_PROFILE_MAX:
        return []
    return sorted(values)


class SchemaDiscovery:
    """预计算表字段 embedding，查询时语义检索相关字段。"""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)
        self._embed_client: Optional[OpenAI] = None
        self._chroma_client: Optional[chromadb.PersistentClient] = None
        self._collection = None

    def _ensure_clients(self):
        if self._embed_client is not None:
            return
        self._embed_client = OpenAI(
            api_key=os.environ["EMBEDDING_API_KEY"],
            base_url=os.environ["EMBEDDING_BASE_URL"],
        )
        chroma_dir = Path(__file__).resolve().parent.parent / "memory" / "chroma_db"
        self._chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
        self._collection = self._chroma_client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def build_index(self, force: bool = False):
        """扫描所有表结构，为每个 (表.字段) 生成 embedding 存入 ChromaDB。

        Args:
            force: True 时清空重建；False 时如果已有数据则跳过。
        """
        self._ensure_clients()
        if not force:
            existing = self._collection.count()
            if existing > 0:
                return  # 已有索引，跳过

        conn = sqlite3.connect(self.db_path)
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'agent_%' AND name != 'user_memory' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        conn.close()

        ids_list = []
        docs_list = []
        metas_list = []

        for table in tables:
            cols = _COLUMN_DESCRIPTIONS.get(table, {})
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.execute(f"PRAGMA table_info('{table}')")
                for row in cursor.fetchall():
                    col_name = row[1]
                    col_type = row[2]
                    desc = cols.get(col_name, "")
                    # 文本描述: "departments.name (TEXT): 部门名称"
                    doc = f"{table}.{col_name} ({col_type})"
                    if desc:
                        doc += f": {desc}"
                    # 值级索引：真实取值进 embedding 文本（"华东"能直接命中该字段）
                    values = profile_column_values(conn, table, col_name, col_type)
                    if values:
                        doc += f"，取值: {'/'.join(values)}"
                    col_id = f"{table}.{col_name}"
                    ids_list.append(col_id)
                    docs_list.append(doc)
                    metas_list.append({
                        "table": table,
                        "column": col_name,
                        "type": col_type,
                        "values": "/".join(values),  # 空串表示未做值索引
                    })
            finally:
                conn.close()

        if not docs_list:
            return

        # 批量 embedding
        embed_model = os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding")
        batch_size = 20
        for i in range(0, len(docs_list), batch_size):
            batch = docs_list[i:i + batch_size]
            vectors = self._embed([b for b in batch])
            self._collection.add(
                ids=ids_list[i:i + batch_size],
                documents=batch,
                embeddings=vectors,
                metadatas=metas_list[i:i + batch_size],
            )

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._embed_client.embeddings.create(
            model=os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            input=texts,
        )
        return [d.embedding for d in resp.data]

    def search(self, query: str, top_k: int = 15) -> list[dict]:
        """语义检索：用户 query → top-K 相关字段。

        Returns:
            [{table, column, type, score}, ...]
        """
        self._ensure_clients()
        if self._collection.count() == 0:
            return []

        q_vec = self._embed([query])[0]
        results = self._collection.query(
            query_embeddings=[q_vec],
            n_results=min(top_k, self._collection.count()),
            include=["metadatas", "documents", "distances"],
        )

        fields = []
        if results["ids"] and results["ids"][0]:
            for i, col_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 1.0
                fields.append({
                    "column_id": col_id,
                    "table": meta.get("table", ""),
                    "column": meta.get("column", ""),
                    "type": meta.get("type", ""),
                    "values": meta.get("values", ""),  # 值级索引：低基数列的真实取值
                    "score": round(1.0 - min(dist, 1.0), 3),  # cosine distance → similarity
                })
        return fields

    def build_schema_context(self, query: str, top_k: int = 15) -> str:
        """根据 query 检索相关字段，组装成精简 schema context。

        返回可直接注入 sql_agent system prompt 的文本。
        """
        fields = self.search(query, top_k)
        if not fields:
            return ""

        # 按表分组
        tables: dict[str, list[dict]] = {}
        for f in fields:
            t = f["table"]
            if t not in tables:
                tables[t] = []
            tables[t].append(f)

        # 去重（同一列可能重复出现），保留 score 最高的
        for t in tables:
            seen = set()
            deduped = []
            for f in tables[t]:
                if f["column"] not in seen:
                    seen.add(f["column"])
                    deduped.append(f)
            tables[t] = deduped

        lines = ["[相关表结构]（「取值:」为该列真实枚举值，WHERE 条件请直接使用）"]
        for table_name, cols in tables.items():
            # 尝试从 _COLUMN_DESCRIPTIONS 获取注释
            desc_map = _COLUMN_DESCRIPTIONS.get(table_name, {})
            lines.append(f"\n## {table_name}")
            for c in cols:
                notes = []
                desc = desc_map.get(c["column"], "")
                if desc:
                    notes.append(desc)
                if c.get("values"):
                    notes.append(f"取值: {c['values']}")
                comment = f"  -- {'；'.join(notes)}" if notes else ""
                lines.append(f"  {c['column']} {c['type']}{comment}")

        return "\n".join(lines)


# 全局单例
_schema_discovery: Optional[SchemaDiscovery] = None


def get_schema_discovery() -> SchemaDiscovery:
    global _schema_discovery
    if _schema_discovery is None:
        _schema_discovery = SchemaDiscovery()
        _schema_discovery.build_index()
    return _schema_discovery


def discover_schema_for_query(query: str, top_k: int = 15) -> str:
    """工具函数：给 sql_agent 用的 schema 检索入口。"""
    sd = get_schema_discovery()
    return sd.build_schema_context(query, top_k)
