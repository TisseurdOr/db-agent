"""Pipeline Monitor 仪表盘 —— CLI 彩色监控面板。

用法:
    python -m pipeline_monitor.dashboard              # 总览
    python -m pipeline_monitor.dashboard --job ods_order_sync  # 单个作业详情
    python -m pipeline_monitor.dashboard --alerts     # 查看告警
    python -m pipeline_monitor.dashboard --history 50 # 最近 50 条运行记录
    python -m pipeline_monitor.dashboard --live       # 每 10s 刷新

设计定位:
"不是做另一个 Airflow——是做一个只读监控面。任何调度器的
Metadata DB 接进来就能看状态、成功率、告警。轻到只有 3 个模块。"
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness.observation.pipeline_monitor.models import (
    init_db, get_all_jobs, get_recent_runs, get_run_stats, get_active_alerts, resolve_alert,
)
from harness.observation.pipeline_monitor.monitor import MonitorEngine


# ── ANSI 颜色 ──

C = {
    "reset": "\033[0m",   "bold": "\033[1m",    "dim": "\033[2m",
    "red": "\033[31m",     "green": "\033[32m",  "yellow": "\033[33m",
    "blue": "\033[34m",    "magenta": "\033[35m","cyan": "\033[36m",
    "white": "\033[37m",   "bg_red": "\033[41m", "bg_green": "\033[42m",
}

STATUS_ICONS = {
    "success": f"{C['green']}✓{C['reset']}",
    "failed":  f"{C['red']}✗{C['reset']}",
    "running": f"{C['blue']}◉{C['reset']}",
    "delayed": f"{C['yellow']}⚠{C['reset']}",
}

SEVERITY_COLORS = {
    "critical": C["red"],
    "warning":  C["yellow"],
    "info":     C["dim"],
}


def _bar(value: float, max_val: float = 100, width: int = 20) -> str:
    """画一个彩色进度条。"""
    ratio = min(value / max_val, 1.0) if max_val > 0 else 0
    filled = int(ratio * width)
    if ratio >= 0.95:
        color = C["green"]
    elif ratio >= 0.70:
        color = C["yellow"]
    else:
        color = C["red"]
    return f"{color}{'█' * filled}{C['dim']}{'░' * (width - filled)}{C['reset']}"


def overview():
    """总览面板。"""
    init_db()
    engine = MonitorEngine()
    report = engine.check_all()

    print(f"\n{C['bold']}{C['cyan']}═══ Pipeline Monitor · 管道健康总览 ═══{C['reset']}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ·  "
          f"{report['total_jobs']} jobs  ·  "
          f"{C['green']}{report['healthy']} healthy{C['reset']}  "
          f"{C['yellow']}{report['warning']} warning{C['reset']}  "
          f"{C['red']}{report['critical']} critical{C['reset']}")

    # ── 作业状态表 ──
    print(f"\n{C['bold']}作业状态{C['reset']}")
    header = f"  {'作业名':28s} {'调度':8s} {'SLA':5s} {'24h成功率':12s} {'平均耗时':9s} {'状态'}"
    print(C["dim"] + header + C["reset"])

    for job in get_all_jobs():
        stats = get_run_stats(job["id"], 24)
        success_rate = stats.get("success_rate", 0)
        avg_dur = f"{stats.get('avg_duration', 0):.0f}s" if stats.get("avg_duration") else "-"

        # 判断当前状态
        last = get_recent_runs(job["id"], 1)
        if last:
            status = last[0]["status"]
            icon = STATUS_ICONS.get(status, "?")
        else:
            status = "no_data"
            icon = "-"

        bar = _bar(success_rate)
        print(f"  {job['name']:28s} {job['schedule']:8s} {job['sla_minutes']:3d}min "
              f"{bar} {success_rate:5.1f}%  {avg_dur:>7s}  {icon}")

    # ── 告警 ──
    _show_alerts(report["alerts"])

    # ── 延迟作业 ──
    if report["delayed_jobs"]:
        print(f"\n{C['bold']}{C['yellow']}超时运行中{C['reset']}")
        for d in report["delayed_jobs"]:
            print(f"  {C['yellow']}⚠{C['reset']} {d['name']} — 已运行 {d['elapsed_minutes']}min (SLA {d['sla_minutes']}min)")

    # ── 连续失败 ──
    if report["consecutive_failures"]:
        print(f"\n{C['bold']}{C['red']}连续失败{C['reset']}")
        for c in report["consecutive_failures"]:
            print(f"  {C['red']}✗{C['reset']} {c['name']} — 连续 {c['consecutive']} 次失败")


def _show_alerts(alerts: list):
    if not alerts:
        print(f"\n{C['green']}✓ 无告警{C['reset']}")
        return
    print(f"\n{C['bold']}{C['red']}告警 ({len(alerts)}){C['reset']}")
    for a in alerts:
        color = SEVERITY_COLORS.get(a.get("severity", "info"), "")
        print(f"  {color}[{a['severity']}]{C['reset']} {a.get('message', '')[:100]}")


def show_job(job_name: str):
    """单个作业详情。"""
    init_db()
    jobs = get_all_jobs()
    job = next((j for j in jobs if j["name"] == job_name), None)
    if not job:
        print(f"作业不存在: {job_name}")
        return

    stats = get_run_stats(job["id"])
    runs = get_recent_runs(job["id"], 20)

    print(f"\n{C['bold']}{C['cyan']}═══ {job_name} · 详情 ═══{C['reset']}")
    print(f"  描述: {job['description']}")
    print(f"  调度: {job['schedule']}  ·  SLA: {job['sla_minutes']}min  ·  负责人: {job['owner']}")
    if stats:
        print(f"  24h: {stats.get('total_runs', 0)} runs  ·  "
              f"成功率 {stats.get('success_rate', 0)}%  ·  "
              f"平均 {stats.get('avg_duration', 0):.0f}s  ·  "
              f"最长 {stats.get('max_duration', 0):.0f}s")

    print(f"\n{C['bold']}最近 20 次运行{C['reset']}")
    header = f"  {'时间':19s} {'状态':8s} {'耗时':9s} {'行数':>10s} {'错误'}"
    print(C["dim"] + header + C["reset"])
    for r in runs:
        start = r["start_time"][:19] if r["start_time"] else "-"
        status = r["status"]
        icon = STATUS_ICONS.get(status, "?")
        dur = f"{r['duration_seconds']:.0f}s" if r.get("duration_seconds") else "-"
        rows = f"{r['row_count']:,}" if r.get("row_count") else "-"
        err = (r.get("error_message") or "")[:40]
        print(f"  {start}  {icon} {status:6s} {dur:>7s}  {rows:>10s}  {C['dim']}{err}{C['reset']}")


def show_alerts_panel():
    """查看所有活跃告警。"""
    init_db()
    alerts = get_active_alerts()
    if not alerts:
        print(f"\n{C['green']}✓ 无活跃告警{C['reset']}")
        return

    print(f"\n{C['bold']}{C['red']}═══ 活跃告警 ({len(alerts)}) ═══{C['reset']}")
    for a in alerts:
        color = SEVERITY_COLORS.get(a["alert_type"] == "critical" and "critical" or a.get("severity", "info"), "")
        icon = "⚠" if a["severity"] == "critical" else "●"
        print(f"  {color}{icon} [{a['alert_type']}] {a['job_name']}{C['reset']}")
        print(f"    {a['message']}")
        print(f"    {C['dim']}{a['created_at'][:19]}{C['reset']}")
        print()


def show_history(limit: int = 50):
    """最近运行历史。"""
    init_db()
    runs = get_recent_runs(limit=limit)
    print(f"\n{C['bold']}最近 {len(runs)} 次运行{C['reset']}")
    header = f"  {'时间':19s} {'作业':28s} {'状态':8s} {'耗时':8s}"
    print(C["dim"] + header + C["reset"])
    for r in runs:
        start = r["start_time"][:19] if r["start_time"] else "-"
        icon = STATUS_ICONS.get(r["status"], "?")
        dur = f"{r['duration_seconds']:.0f}s" if r.get("duration_seconds") else "-"
        print(f"  {start}  {r['job_name']:28s} {icon} {r['status']:6s} {dur:>6s}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Monitor 仪表盘")
    parser.add_argument("--job", type=str, help="查看单个作业详情")
    parser.add_argument("--alerts", action="store_true", help="查看活跃告警")
    parser.add_argument("--history", type=int, help="查看最近 N 条运行记录")
    parser.add_argument("--live", action="store_true", help="每 10s 自动刷新总览")
    parser.add_argument("--seed", action="store_true", help="先生成种子数据再显示")
    parser.add_argument("--resolve", type=int, help="解决指定告警 ID")
    args = parser.parse_args()

    if args.seed:
        from harness.observation.pipeline_monitor.seed import seed
        seed(days=7)

    if args.resolve:
        init_db()
        resolve_alert(args.resolve)
        print(f"告警 #{args.resolve} 已解决")

    elif args.job:
        show_job(args.job)

    elif args.alerts:
        show_alerts_panel()

    elif args.history:
        show_history(args.history)

    elif args.live:
        print("实时监控中... (Ctrl+C 退出)")
        try:
            while True:
                # 清屏
                print("\033[2J\033[H", end="")
                overview()
                print(f"\n{C['dim']}每 10s 刷新 | Ctrl+C 退出{C['reset']}")
                time.sleep(10)
        except KeyboardInterrupt:
            print(f"\n{C['dim']}监控已停止{C['reset']}")

    else:
        overview()
