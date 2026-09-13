"""GET /api/eval — 评测结果；POST /api/eval/run — 触发评测（默认 --fast）。

数据源：
  - logs/eval_baseline.json —— 每个 mode 的最近一次通过率
  - logs/eval_history.jsonl —— 每次评测追加的历史记录
Ops 指标则由对话查询自动写入进程内计数器（无需按钮）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from harness.observation.regression import load_baseline, load_history

router = APIRouter()

_job_lock = asyncio.Lock()
_job: dict = {
    "status": "idle",  # idle | running | ok | error
    "mode": None,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "message": "",
    "pass_rate": None,
}


class EvalRunRequest(BaseModel):
    mode: Literal["fast", "full"] = Field(
        "fast",
        description="fast=零 LLM 秒级；full=调 LLM 完整用例（较慢）",
    )


@router.get("/eval")
async def eval_overview():
    """返回 {baseline, history, modes} 供前端 Eval 页渲染。"""
    baseline = load_baseline()
    return {
        "baseline": baseline,
        "history": load_history(),
        "modes": sorted(baseline.keys()),
        "job": dict(_job),
    }


@router.get("/eval/run/status")
async def eval_run_status():
    return dict(_job)


@router.get("/eval/retrieval-ablation")
async def retrieval_ablation():
    """返回检索消融召回率（logs/ablation_results.json），文件不存在则返回空。"""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(root, "logs", "ablation_results.json")
    if not os.path.exists(path):
        return {"generated_at": None, "n_pos": 0, "n_neg": 0, "configs": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.post("/eval/run")
async def eval_run(req: EvalRunRequest):
    """后台跑 tests.eval_runner；同一时间只允许一个任务。"""
    async with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(409, detail={
                "error": "busy",
                "message": "已有评测在跑，请稍后再试。",
                "job": dict(_job),
            })
        _job.update({
            "status": "running",
            "mode": req.mode,
            "started_at": time.time(),
            "finished_at": None,
            "exit_code": None,
            "message": f"running eval --{req.mode}",
            "pass_rate": None,
        })

    asyncio.create_task(_run_eval("tests.eval_runner", [f"--{req.mode}"], req.mode))
    return {"ok": True, "status": "started", "mode": req.mode, "job": dict(_job)}


@router.post("/eval/selflearn")
async def eval_selflearn():
    """触发控制齿轮：三阶段自学习评测 + 退化回滚（后台跑 tests.eval_selflearn）。

    先 --reset 清到 seed 基线再学，保证基线（phase 1）不受上次残留污染。
    结果写 baseline/history（mode=sql_selflearn），前端 Eval 页趋势图可直接显示。
    """
    async with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(409, detail={
                "error": "busy",
                "message": "已有评测在跑，请稍后再试。",
                "job": dict(_job),
            })
        _job.update({
            "status": "running",
            "mode": "sql_selflearn",
            "started_at": time.time(),
            "finished_at": None,
            "exit_code": None,
            "message": "running eval_selflearn",
            "pass_rate": None,
        })
    asyncio.create_task(_run_eval("tests.eval_selflearn", ["--reset", "--category", "output_quality"], "sql_selflearn"))
    return {"ok": True, "status": "started", "mode": "sql_selflearn", "job": dict(_job)}


@router.post("/eval/confidence")
async def eval_confidence():
    """触发置信度校准评测（后台跑 tests.eval_confidence）。

    结果写 logs/confidence_calibration.json，由 GET /eval/confidence 读取。
    """
    async with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(409, detail={
                "error": "busy",
                "message": "已有评测在跑，请稍后再试。",
                "job": dict(_job),
            })
        _job.update({
            "status": "running",
            "mode": "sql_confidence",
            "started_at": time.time(),
            "finished_at": None,
            "exit_code": None,
            "message": "running eval_confidence",
            "pass_rate": None,
        })
    asyncio.create_task(_run_eval("tests.eval_confidence", ["--category", "output_quality"], "sql_confidence"))
    return {"ok": True, "status": "started", "mode": "sql_confidence", "job": dict(_job)}


@router.get("/eval/confidence")
async def eval_confidence_result():
    """读置信度校准结果（logs/confidence_calibration.json），文件不存在则返回空。"""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(root, "logs", "confidence_calibration.json")
    if not os.path.exists(path):
        return {"generated_at": None, "scored": 0, "gap": None, "rows": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def _run_eval(module: str, extra_args: list[str], mode: str) -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    py = sys.executable
    args = [py, "-m", module] + extra_args
    env = os.environ.copy()
    env.setdefault("AGENT_USER", "dba")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out_b, _ = await proc.communicate()
        text = (out_b or b"").decode("utf-8", errors="replace")
        # 粗略解析通过率行
        pass_rate = None
        for line in text.splitlines()[::-1]:
            if "通过率" in line or "pass_rate" in line.lower() or "Pass rate" in line:
                # keep message short
                break
        # from baseline after run
        baseline = load_baseline()
        pass_rate = None
        if mode in baseline:
            pass_rate = baseline[mode].get("pass_rate")

        ok = proc.returncode == 0
        _job.update({
            "status": "ok" if ok else "error",
            "finished_at": time.time(),
            "exit_code": proc.returncode,
            "message": ("pass" if ok else "some cases failed") + (f" · last lines: {text.strip()[-280:]}" if text.strip() else ""),
            "pass_rate": pass_rate,
        })
    except Exception as exc:
        _job.update({
            "status": "error",
            "finished_at": time.time(),
            "exit_code": -1,
            "message": str(exc),
            "pass_rate": None,
        })
