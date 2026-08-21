"""种子数据生成器 —— 模拟真实管道运行历史。

生成 ~500 条运行记录，分布在 8 个管道作业上，包含：
  - 正常成功（~70%）
  - 偶发失败（~15%）
  - 连续失败（制造 2 个异常作业）
  - 当前仍在运行中的（2 个 running + 1 个 delayed）
  - 不同耗时和行数的合理分布

用法:
    python -m pipeline_monitor.seed          # 生成 7 天数据
    python -m pipeline_monitor.seed --days 30  # 生成 30 天
    python -m pipeline_monitor.seed --reset    # 清空重建
"""

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 允许从项目根 python -m pipeline_monitor.seed
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness.observation.pipeline_monitor.models import (
    init_db, register_job, start_run, finish_run, create_alert, get_db,
)


JOBS = [
    {"name": "ods_order_sync",      "schedule": "hourly",  "sla": 15,  "desc": "ODS 层订单数据同步",             "owner": "数据平台组"},
    {"name": "dwd_user_behavior",   "schedule": "hourly",  "sla": 30,  "desc": "DWD 层用户行为日志清洗",         "owner": "数据平台组"},
    {"name": "dws_sales_agg",       "schedule": "daily",   "sla": 60,  "desc": "DWS 层销售汇总（日）",            "owner": "商业分析组"},
    {"name": "dws_user_retention",  "schedule": "daily",   "sla": 45,  "desc": "DWS 层用户留存计算",              "owner": "增长分析组"},
    {"name": "ads_executive_report","schedule": "daily",   "sla": 90,  "desc": "ADS 层高管日报（含图表）",        "owner": "商业分析组"},
    {"name": "etl_product_catalog", "schedule": "daily",   "sla": 30,  "desc": "产品目录 ETL 全量刷新",           "owner": "数据平台组"},
    {"name": "data_quality_check",  "schedule": "daily",   "sla": 20,  "desc": "全库数据质量扫描（NULL/异常值）", "owner": "数据治理组"},
    {"name": "hive_partition_clean","schedule": "weekly",  "sla": 120, "desc": "Hive 历史分区清理（保留 90 天）", "owner": "数据平台组"},
]

# 每个作业的"性格"：(平均耗时秒, 标准差, 平均行数, 失败率)
JOB_PROFILES = {
    "ods_order_sync":       (45,  15,  80000,  0.05),
    "dwd_user_behavior":    (120, 40,  500000, 0.08),
    "dws_sales_agg":        (300, 90,  5000,   0.03),
    "dws_user_retention":   (180, 60,  20000,  0.05),
    "ads_executive_report": (420, 120, 200,    0.02),
    "etl_product_catalog":  (90,  30,  3000,   0.04),
    "data_quality_check":   (60,  20,  0,      0.01),
    "hive_partition_clean": (600, 180, 0,      0.06),
}

FAIL_MESSAGES = [
    "Connection timeout to source database after 30s",
    "ORA-00001: unique constraint violated on merge",
    "HDFS NameNode is in safe mode, cannot write",
    "Memory limit exceeded: task used 4.2GB of 4GB max",
    "Parquet schema mismatch: column 'user_id' type changed from INT to BIGINT",
    "Foreign key violation: referenced order_id not found in ods_orders",
    "Disk quota exceeded on /data/warehouse/dwd",
    "Kerberos ticket expired, re-authentication required",
    "Divide by zero in aggregation: total_users=0 for segment",
    "java.lang.OutOfMemoryError: GC overhead limit exceeded",
]


def seed(days: int = 7, reset: bool = False):
    if reset:
        db = Path(__file__).resolve().parent / "monitor.db"
        if db.exists():
            db.unlink()
    init_db()

    # 注册作业
    job_ids = {}
    for j in JOBS:
        jid = register_job(j["name"], j["schedule"], j["sla"], j["desc"], j["owner"])
        job_ids[j["name"]] = jid

    now = datetime.now()
    start_date = now - timedelta(days=days)

    # 制造异常：ods_order_sync 最近 3 次连续失败
    anomaly_job = "ods_order_sync"
    # 制造异常：dws_sales_agg 当前有一个 running 超 SLA
    delayed_job = "dws_sales_agg"

    for job_name, jid in job_ids.items():
        profile = JOB_PROFILES[job_name]
        avg_dur, std_dur, avg_rows, fail_rate = profile
        schedule = next(j["schedule"] for j in JOBS if j["name"] == job_name)

        # 确定每天的运行次数
        if schedule == "hourly":
            runs_per_day = 24
        elif schedule == "daily":
            runs_per_day = 1
        else:  # weekly
            runs_per_day = 1 / 7

        # 生成运行记录
        current = start_date
        while current <= now:
            if runs_per_day >= 1:
                for h in range(int(runs_per_day)):
                    run_time = current + timedelta(hours=h)
                    if run_time > now:
                        break
                    _gen_run(jid, job_name, profile, run_time, fail_rate, anomaly_job, delayed_job, job_ids)
            else:
                if current.weekday() == 0:  # weekly 任务周一跑
                    _gen_run(jid, job_name, profile, current, fail_rate, anomaly_job, delayed_job, job_ids)
            current += timedelta(days=1)

    # 生成当前仍在运行的作业（模拟实时场景）
    _gen_running_job(job_ids["ods_order_sync"], "ods_order_sync", now, running_minutes=8)
    _gen_running_job(job_ids["dws_user_retention"], "dws_user_retention", now, running_minutes=25)
    _gen_running_job(job_ids[delayed_job], delayed_job, now - timedelta(minutes=75), running_minutes=75)

    # 生成一些告警
    _gen_sample_alerts(job_ids)

    print(f"种子数据生成完成: {days} 天, {len(JOBS)} 个作业")
    _print_summary(job_ids)


def _gen_run(jid, job_name, profile, run_time, fail_rate, anomaly_job, delayed_job, job_ids):
    avg_dur, std_dur, avg_rows, _ = profile

    # 异常作业：最近 3 次强制失败
    if job_name == anomaly_job and run_time >= datetime.now() - timedelta(hours=3):
        status = "failed"
    else:
        status = "failed" if random.random() < fail_rate else "success"

    run_id = start_run(jid, "scheduler")
    # 模拟运行时间
    duration = max(1, random.gauss(avg_dur, std_dur))
    rows = max(0, int(random.gauss(avg_rows, avg_rows * 0.3))) if avg_rows > 0 else 0
    error = random.choice(FAIL_MESSAGES) if status == "failed" else ""

    # 更新 start_time 为 run_time
    conn = get_db()
    conn.execute(
        "UPDATE pipeline_runs SET start_time=? WHERE id=?",
        (run_time.isoformat(), run_id))
    conn.commit()
    conn.close()

    finish_run(run_id, status, rows, error, end_time=(run_time + timedelta(seconds=duration)).isoformat())


def _gen_running_job(jid, job_name, start_time, running_minutes):
    """生成一个正在运行的作业。"""
    run_id = start_run(jid, "scheduler")
    run_start = start_time - timedelta(minutes=running_minutes)
    conn = get_db()
    conn.execute(
        "UPDATE pipeline_runs SET start_time=? WHERE id=?",
        (run_start.isoformat(), run_id))
    conn.commit()
    conn.close()


def _gen_sample_alerts(job_ids: dict):
    """生成一些示例告警。"""
    now = datetime.now().isoformat()
    sample_alerts = [
        (job_ids["ods_order_sync"], "consecutive_failures", "critical",
         "ods_order_sync 连续失败 3 次，上次成功: 4h 前，请检查源库连接"),
        (job_ids["dws_sales_agg"], "sla_miss", "warning",
         "dws_sales_agg 运行已超 SLA (60min)，当前耗时 75min，可能影响高管日报"),
        (job_ids["dwd_user_behavior"], "anomaly_duration", "warning",
         "dwd_user_behavior 最近 5 次平均耗时 180s，比 7 日均值 120s 增长 50%"),
        (job_ids["hive_partition_clean"], "timeout", "critical",
         "hive_partition_clean 超时 (SLA 120min)，上次运行耗时 185min"),
    ]
    for job_id, atype, severity, msg in sample_alerts:
        conn = get_db()
        conn.execute(
            "INSERT INTO alerts (job_id, alert_type, severity, message) VALUES (?,?,?,?)",
            (job_id, atype, severity, msg))
        conn.commit()
        conn.close()


def _print_summary(job_ids: dict):
    conn = get_db()
    for name, jid in job_ids.items():
        total = conn.execute("SELECT COUNT(*) FROM pipeline_runs WHERE job_id=?", (jid,)).fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM pipeline_runs WHERE job_id=? AND status='failed'", (jid,)).fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM pipeline_runs WHERE job_id=? AND status='running'", (jid,)).fetchone()[0]
        print(f"  {name:25s}  {total:4d} runs  {failed} failed  {running} running")
    alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE resolved=0").fetchone()[0]
    print(f"\n  活跃告警: {alerts}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Monitor 种子数据生成")
    parser.add_argument("--days", type=int, default=7, help="生成多少天的历史数据")
    parser.add_argument("--reset", action="store_true", help="清空数据库重新生成")
    args = parser.parse_args()
    seed(args.days, args.reset)
