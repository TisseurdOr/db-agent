#!/usr/bin/env python3
"""重画 functional_layers.png —— 短版功能叠层（适配 README 图 1）。

用法:
    .venv/bin/python scripts/draw_functional_layers.py

输出:
    docs/diagrams/functional_layers.png
"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "docs", "diagrams", "functional_layers.png")

for f in (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
):
    if os.path.exists(f):
        fm.fontManager.addfont(f)
        break
plt.rcParams["font.family"] = [
    "Heiti TC",
    "PingFang SC",
    "STHeiti",
    "Arial Unicode MS",
    "sans-serif",
]
plt.rcParams["axes.unicode_minus"] = False

NAVY, BLUE, TEAL = "#0F2A43", "#2E86DE", "#17A2B8"
ORANGE, GREEN, RED, PURPLE = "#F0A030", "#2E8B57", "#C0392B", "#7B2FBE"
MUTED, WHITE, LIGHT = "#5A6B7B", "#FFFFFF", "#F2F5F8"


def rounded(ax, x, y, w, h, fc, ec=None, lw=1.8, rs=0.45):
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0.02,rounding_size={rs}",
            fc=fc,
            ec=ec or fc,
            lw=lw,
            zorder=2,
        )
    )


def main():
    # 横向短图，避免 README 里又高又窄
    fig, ax = plt.subplots(figsize=(12, 6.2), dpi=160)
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(
        50,
        94,
        "db-agent 功能分层",
        ha="center",
        va="center",
        fontsize=22,
        fontweight="bold",
        color=NAVY,
    )
    ax.text(
        50,
        88,
        "问数能力叠权限、自愈、记忆、评测",
        ha="center",
        va="center",
        fontsize=13,
        color=MUTED,
    )

    # 自下而上：底座 → 能力 → 入口（更符合“分层叠”）
    layers = [
        (8, ORANGE, "评测与观测", "Trace / Opik · 47 Eval · Judge 与被测分离"),
        (24, GREEN, "记忆与上下文", "短/长期记忆 · Schema Linking · few-shot 自学习"),
        (40, RED, "权限与自愈", "RBAC / HITL / 护栏 · 重试 → 熔断 → 幂等"),
        (56, BLUE, "问数与编排", "Single ReAct · Multi LangGraph · SQL / Hive / HBase"),
        (72, NAVY, "入口", "CLI · FastAPI + SSE · Streamlit"),
    ]

    for y, color, title, body in layers:
        rounded(ax, 8, y, 84, 12, LIGHT, color, lw=2.4, rs=0.55)
        rounded(ax, 8, y, 22, 12, color, color, lw=0, rs=0.55)
        ax.text(
            19,
            y + 6,
            title,
            ha="center",
            va="center",
            fontsize=14,
            fontweight="bold",
            color=WHITE,
            zorder=4,
        )
        ax.text(
            62,
            y + 6,
            body,
            ha="center",
            va="center",
            fontsize=12,
            color=NAVY,
            zorder=4,
        )

    # 右侧小箭头示意“向上叠加”
    for y in (20, 36, 52, 68):
        ax.add_patch(
            FancyArrowPatch(
                (95.5, y),
                (95.5, y + 4),
                arrowstyle="-|>",
                mutation_scale=12,
                color=MUTED,
                lw=1.4,
                zorder=3,
            )
        )
    ax.text(95.5, 82, "叠", ha="center", va="center", fontsize=10, color=MUTED)

    ax.text(
        50,
        3.5,
        "核心先能查数，再叠硬约束与可靠性，最后用记忆和评测把质量闭环",
        ha="center",
        va="center",
        fontsize=11,
        color=MUTED,
    )

    fig.savefig(OUT, facecolor=WHITE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
