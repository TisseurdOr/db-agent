"""Pipeline Monitor 数据模型 —— SQLite 持久化层。

三张表:
  - jobs: 注册的管道作业（名称、调度、SLA、负责人）
  - pipeline_runs: 每次执行的记录（状态、耗时、行数、错误）
  - alerts: 告警事件（超时、连续失败、SLA 错过）

设计意图：只读监控面——不调度、不执行，只观察和告警。
"""

import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent / "monitor.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """建表。幂等。"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            schedule TEXT NOT NULL DEFAULT 'daily',    -- daily / hourly / weekly
            sla_minutes INTEGER NOT NULL DEFAULT 60,    -- 预期完成时限（分钟）
            owner TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            status TEXT NOT NULL CHECK(status IN ('success','failed','running','delayed')),
            start_time TEXT NOT NULL DEFAULT (datetime('now')),
            end_time TEXT,
            duration_seconds REAL,
            row_count INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            triggered_by TEXT DEFAULT 'scheduler',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,  -- 可为空：告警不一定关联到具体 run
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            alert_type TEXT NOT NULL CHECK(alert_type IN ('timeout','consecutive_failures','sla_miss','anomaly_duration')),
            severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
            message TEXT NOT NULL DEFAULT '',
            resolved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_job_id ON pipeline_runs(job_id);
        CREATE INDEX IF NOT EXISTS idx_runs_status ON pipeline_runs(status);
        CREATE INDEX IF NOT EXISTS idx_runs_start ON pipeline_runs(start_time);
        CREATE INDEX IF NOT EXISTS idx_alerts_job_id ON alerts(job_id);
        CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);
    """)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Job CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def register_job(name: str, schedule: str = "daily", sla_minutes: int = 60,
                 description: str = "", owner: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT OR REPLACE INTO jobs (name, description, schedule, sla_minutes, owner) VALUES (?,?,?,?,?)",
        (name, description, schedule, sla_minutes, owner))
    conn.commit()
    job_id = cur.lastrowid
    conn.close()
    return job_id


def get_all_jobs() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM jobs WHERE enabled=1 ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Run 写入（由外部调度器/模拟器调用）
# ═══════════════════════════════════════════════════════════════════════════════

def start_run(job_id: int, triggered_by: str = "scheduler") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO pipeline_runs (job_id, status, start_time, triggered_by) VALUES (?, 'running', datetime('now'), ?)",
        (job_id, triggered_by))
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def finish_run(run_id: int, status: str, row_count: int = 0,
               error_message: str = "", end_time: Optional[str] = None):
    conn = get_db()
    end = end_time or datetime.now().isoformat()
    conn.execute(
        "UPDATE pipeline_runs SET status=?, end_time=?, duration_seconds=ROUND((julianday(?)-julianday(start_time))*86400,1), row_count=?, error_message=? WHERE id=?",
        (status, end, end, row_count, error_message, run_id))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# 监控查询
# ═══════════════════════════════════════════════════════════════════════════════

def get_recent_runs(job_id: int = None, limit: int = 20) -> list[dict]:
    conn = get_db()
    if job_id:
        rows = conn.execute(
            "SELECT r.*, j.name as job_name FROM pipeline_runs r JOIN jobs j ON r.job_id=j.id WHERE r.job_id=? ORDER BY r.start_time DESC LIMIT ?",
            (job_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT r.*, j.name as job_name FROM pipeline_runs r JOIN jobs j ON r.job_id=j.id ORDER BY r.start_time DESC LIMIT ?",
            (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run_stats(job_id: int, window_hours: int = 24) -> dict:
    """过去 N 小时内的运行统计。"""
    conn = get_db()
    since = (datetime.now() - timedelta(hours=window_hours)).isoformat()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_runs,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success_count,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed_count,
            ROUND(AVG(CASE WHEN status='success' THEN duration_seconds END),1) as avg_duration,
            MAX(duration_seconds) as max_duration
        FROM pipeline_runs
        WHERE job_id=? AND start_time >= ?
    """, (job_id, since)).fetchone()
    conn.close()
    if not row:
        return {}
    d = dict(row)
    total = d["total_runs"] or 0
    d["success_rate"] = round(d["success_count"] / total * 100, 1) if total > 0 else 0
    return d


def get_delayed_jobs() -> list[dict]:
    """找出所有超 SLA 仍在运行的作业。"""
    conn = get_db()
    rows = conn.execute("""
        SELECT j.name, j.sla_minutes, r.id as run_id, r.start_time,
               ROUND((julianday('now')-julianday(r.start_time))*24*60,0) as elapsed_minutes
        FROM pipeline_runs r
        JOIN jobs j ON r.job_id=j.id
        WHERE r.status='running'
          AND (julianday('now')-julianday(r.start_time))*24*60 > j.sla_minutes
        ORDER BY elapsed_minutes DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_consecutive_failures(threshold: int = 3) -> list[dict]:
    """找出连续失败 >= threshold 次的作业。"""
    conn = get_db()
    # 用窗口函数：从最近一次成功或第一行开始，数连续失败次数
    rows = conn.execute("""
        WITH ranked AS (
            SELECT r.job_id, j.name, r.status, r.start_time,
                   ROW_NUMBER() OVER (PARTITION BY r.job_id ORDER BY r.start_time DESC) as rn
            FROM pipeline_runs r JOIN jobs j ON r.job_id=j.id
        ),
        fail_streak AS (
            SELECT job_id, name,
                   COUNT(*) FILTER (WHERE status='failed') as consecutive,
                   MAX(start_time) as last_failed
            FROM ranked
            WHERE rn <= ?
            GROUP BY job_id
            HAVING COUNT(*) FILTER (WHERE status='failed') = ?
        )
        SELECT * FROM fail_streak WHERE consecutive >= ?
    """, (threshold * 2, threshold, threshold)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Alert CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def create_alert(run_id: int, job_id: int, alert_type: str, severity: str,
                 message: str) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO alerts (run_id, job_id, alert_type, severity, message) VALUES (?,?,?,?,?)",
        (run_id, job_id, alert_type, severity, message))
    conn.commit()
    alert_id = cur.lastrowid
    conn.close()
    return alert_id


def get_active_alerts(limit: int = 50) -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, j.name as job_name
        FROM alerts a JOIN jobs j ON a.job_id=j.id
        WHERE a.resolved=0
        ORDER BY a.created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_alert(alert_id: int):
    conn = get_db()
    conn.execute(
        "UPDATE alerts SET resolved=1, resolved_at=datetime('now') WHERE id=?",
        (alert_id,))
    conn.commit()
    conn.close()
