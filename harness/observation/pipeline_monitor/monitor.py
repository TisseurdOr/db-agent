"""监控引擎 —— 健康检查 + 异常检测 + 告警生成。

核心逻辑:
  1. 检查所有已注册作业
  2. 对每个作业，查看最近 N 次运行
  3. 按规则检测异常：超时 / 连续失败 / SLA 错过 / 耗时异常
  4. 生成告警记录，避免重复告警

用法:
    from harness.observation.pipeline_monitor.monitor import MonitorEngine
    engine = MonitorEngine()
    report = engine.check_all()  # 返回 (healthy, alerts)
"""

from harness.observation.pipeline_monitor.models import (
    get_db, get_all_jobs, get_run_stats, get_delayed_jobs,
    get_consecutive_failures, create_alert, get_active_alerts,
)


class MonitorEngine:
    """管道健康检查引擎。"""

    def __init__(self):
        self.alerts_generated = []

    def check_all(self) -> dict:
        """对所有作业执行一次健康检查，返回报告。"""
        self.alerts_generated = []
        jobs = get_all_jobs()
        delayed = get_delayed_jobs()
        consecutive = get_consecutive_failures(threshold=3)

        findings = {
            "total_jobs": len(jobs),
            "healthy": 0,
            "warning": 0,
            "critical": 0,
            "delayed_jobs": delayed,
            "consecutive_failures": consecutive,
            "alerts": [],
        }

        for job in jobs:
            status = self._check_job(job, delayed, consecutive)
            findings[status] = findings.get(status, 0) + 1

        findings["alerts"] = self.alerts_generated
        return findings

    def _check_job(self, job: dict, delayed: list[dict], consecutive: list[dict]) -> str:
        """检查单个作业，返回 healthy/warning/critical。"""
        jid = job["id"]
        name = job["name"]
        sla = job["sla_minutes"]
        stats = get_run_stats(jid, window_hours=24)
        if not stats:
            return "healthy"

        severity = "healthy"

        # 规则 1: 当前是否有 delayed 作业
        for d in delayed:
            if d["name"] == name:
                self._alert(jid, 0, "sla_miss", "critical",
                            f"{name} 运行超 SLA ({sla}min)，当前耗时 {d['elapsed_minutes']}min")
                severity = "critical"

        # 规则 2: 连续失败
        for c in consecutive:
            if c["name"] == name:
                self._alert(jid, 0, "consecutive_failures", "critical",
                            f"{name} 连续失败 {c['consecutive']} 次，上次失败: {c['last_failed']}")
                severity = "critical"

        # 规则 3: 24h 内成功率低于 80%
        if stats.get("total_runs", 0) >= 5 and stats.get("success_rate", 100) < 80:
            self._alert(jid, 0, "consecutive_failures", "warning",
                        f"{name} 24h 成功率 {stats['success_rate']}%（{stats['failed_count']}/{stats['total_runs']}）")
            severity = "warning"

        # 规则 4: 平均耗时比历史均值增长 > 50%
        if stats.get("avg_duration") and stats.get("total_runs", 0) >= 3:
            # 与全局均值比较
            conn = get_db()
            global_avg = conn.execute(
                "SELECT AVG(duration_seconds) FROM pipeline_runs WHERE job_id=? AND status='success'",
                (jid,)).fetchone()[0]
            conn.close()
            if global_avg and global_avg > 0 and stats["avg_duration"] > global_avg * 1.5:
                self._alert(jid, 0, "anomaly_duration", "warning",
                            f"{name} 平均耗时 {stats['avg_duration']}s，比全局均值 {global_avg:.0f}s 增长 {((stats['avg_duration']/global_avg)-1)*100:.0f}%")
                if severity == "healthy":
                    severity = "warning"

        return severity

    def _alert(self, job_id: int, run_id: int, alert_type: str, severity: str, message: str):
        """生成告警（去重：同样 job + type 且未解决的，不重复生成）。"""
        existing = get_active_alerts()
        for a in existing:
            if a["job_id"] == job_id and a["alert_type"] == alert_type and not a["resolved"]:
                return  # 已存在未解决的同类型告警，跳过

        create_alert(run_id, job_id, alert_type, severity, message)
        self.alerts_generated.append({"job_id": job_id, "type": alert_type, "severity": severity, "message": message})
