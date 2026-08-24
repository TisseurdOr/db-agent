"""告警通知——关键异常发生时异步推送（日志 + 可选 Webhook）。

默认只写日志（零依赖、零网络，落地即可用）；配置 ALERT_WEBHOOK_URL 后，
告警会 POST JSON 到 Webhook（Slack / 钉钉 / 飞书机器人等通用格式）。
发送在 daemon 线程里做，绝不阻塞 agent 事件循环；Webhook 失败只记日志，不抛异常。

用法:
    from harness.observation.alerts import send_alert
    send_alert("LLM 服务熔断", "连续失败 5 次，熔断器已打开", level="critical", tags={"breaker": "llm"})
"""

import json
import logging
import os
import threading
import urllib.request

logger = logging.getLogger("db-agent.alerts")


class AlertNotifier:
    """告警器：日志（必发）+ 可选的 HTTP Webhook（后台线程）。"""

    def __init__(self, webhook_url: str | None = None, enabled: bool | None = None):
        self.webhook_url = (
            webhook_url
            if webhook_url is not None
            else os.getenv("ALERT_WEBHOOK_URL", "").strip()
        )
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("ALERT_ENABLED", "1").strip().lower()
            in {"1", "true", "yes", "on"}
        )

    def send(self, title: str, message: str, level: str = "warning",
             tags: dict | None = None) -> None:
        """发送一条告警。日志必写；配置了 Webhook 时后台推送。"""
        record = {
            "title": title,
            "message": message,
            "level": level,
            "tags": tags or {},
        }
        log = logger.error if level == "critical" else logger.warning
        log("[ALERT] %s | %s | %s", level, title, message)

        if self.enabled and self.webhook_url:
            thread = threading.Thread(
                target=self._post_webhook, args=(record,), daemon=True,
            )
            thread.start()

    # ── Webhook ──────────────────────────────────────────────────────

    def _post_webhook(self, record: dict) -> None:
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
        except Exception as e:  # 告警失败不能影响主流程
            logger.warning("告警 Webhook 发送失败: %s", e)


# ── 全局默认告警器 ───────────────────────────────────────────────────

_notifier: AlertNotifier | None = None


def get_alert_notifier() -> AlertNotifier:
    global _notifier
    if _notifier is None:
        _notifier = AlertNotifier()
    return _notifier


def reset_alert_notifier() -> None:
    """重置默认告警器（测试隔离用）。"""
    global _notifier
    _notifier = None


def send_alert(title: str, message: str, level: str = "warning",
               tags: dict | None = None) -> None:
    """模块级入口：用默认告警器发送告警。"""
    get_alert_notifier().send(title, message, level=level, tags=tags)
