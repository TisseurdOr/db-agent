"""共享启动引导：seed 数据 + 预热索引。

CLI / Streamlit / FastAPI 三入口跑同一套初始化，集中在此避免各自复制。
"""

from db.seed import init_db
from harness.context.schema_discovery import get_schema_discovery
from harness.context.template_matcher import init_metric_registry
from harness.tools.hbase import _seed_hbase_store


def bootstrap_data() -> None:
    """初始化数据层：seed demo 库 + HBase 内存 + 指标模板 + schema 向量索引。"""
    init_db()
    _seed_hbase_store()
    init_metric_registry()
    try:
        get_schema_discovery().build_index()
    except Exception as e:
        # embedding 未配置时降级：Agent 改用 list_tables/describe_table
        print(f"[schema_discovery] 索引构建跳过: {e}")
