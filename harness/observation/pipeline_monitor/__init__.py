"""Pipeline Monitor —— 轻量级数据管道监控面。

只读监控，不调度：任何调度器的 Metadata DB 接进来就能用。

用法:
    python -m pipeline_monitor.dashboard --seed    # 生成模拟数据 + 总览
    python -m pipeline_monitor.dashboard --job ods_order_sync  # 单个作业
    python -m pipeline_monitor.dashboard --live    # 实时刷新
"""
