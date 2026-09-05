"""GET /api/ops/series — ops 指标时间序列（进程内存环形缓冲）+ 当前快照。

数据源：harness.observation.ops_metrics（进程内计数器，重启清零）。
前端 Ops 页每 ~5s 轮询一次，sample_series() 按间隔自动追加采样点。
"""
from fastapi import APIRouter

from harness.observation.ops_metrics import sample_series, snapshot

router = APIRouter()


@router.get("/ops/series")
async def ops_series():
    """返回 {series: [...], current: snapshot()} 供前端绘图。"""
    return {"series": sample_series(), "current": snapshot()}
