"""GET /api/dashboard/latest — 返回最近一次 render_chart 生成的大屏相对 URL。"""

import os

from fastapi import APIRouter

from harness.tools.chart import CHART_DIR, get_latest_dashboard_url

router = APIRouter()


@router.get("/dashboard/latest")
async def latest_dashboard():
    url = get_latest_dashboard_url()
    if url is None and os.path.isdir(CHART_DIR):
        # 进程重启后内存指针丢失，回退到 charts/ 目录里最新的大屏文件
        files = sorted(
            (f for f in os.listdir(CHART_DIR) if f.startswith("dashboard_") and f.endswith(".html")),
            reverse=True,
        )
        if files:
            url = f"/charts/{files[0]}"
    return {"url": url}
