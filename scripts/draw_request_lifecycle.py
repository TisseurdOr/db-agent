#!/usr/bin/env python3
"""重画 request_lifecycle.png —— 去掉左侧碎字/裁切，对齐真实链路。

用法:
    .venv/bin/python scripts/draw_request_lifecycle.py

输出:
    docs/diagrams/request_lifecycle.png
"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "docs", "diagrams", "request_lifecycle.png")

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
ORANGE, GREEN, RED = "#F0A030", "#2E8B57", "#C0392B"
LIGHT, MUTED, DARK, WHITE = "#F2F5F8", "#5A6B7B", "#222B35", "#FFFFFF"
STAGE_BG = "#F7F9FB"
BOX = "#E8F1FB"
BOX_EC = "#6C8EBF"


def rounded(ax, x, y, w, h, fc, ec=None, lw=1.6, rs=0.35):
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0.015,rounding_size={rs}",
            fc=fc,
            ec=ec or fc,
            lw=lw,
            zorder=2,
        )
    )


def arrow(ax, x1, y1, x2, y2, color=MUTED, lw=1.8):
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            arrowstyle="-|>",
            mutation_scale=14,
            color=color,
            lw=lw,
            zorder=3,
        )
    )


def stage_frame(ax, x, y, w, h, title, color):
    ax.add_patch(
        Rectangle(
            (x, y),
            w,
            h,
            fill=True,
            fc=STAGE_BG,
            ec=color,
            lw=1.4,
            linestyle="--",
            zorder=1,
        )
    )
    ax.text(
        x + 1.2,
        y + h - 2.2,
        title,
        ha="left",
        va="center",
        fontsize=13,
        fontweight="bold",
        color=color,
        zorder=4,
    )


def box(ax, x, y, w, h, title, body="", fc=BOX, ec=BOX_EC, title_c=NAVY):
    rounded(ax, x, y, w, h, fc, ec, lw=1.5, rs=0.4)
    if body:
        ax.text(
            x + w / 2,
            y + h * 0.62,
            title,
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=title_c,
            zorder=4,
        )
        ax.text(
            x + w / 2,
            y + h * 0.28,
            body,
            ha="center",
            va="center",
            fontsize=9,
            color=MUTED,
            linespacing=1.25,
            zorder=4,
        )
    else:
        ax.text(
            x + w / 2,
            y + h / 2,
            title,
            ha="center",
            va="center",
            fontsize=11,
            fontweight="bold",
            color=title_c,
            zorder=4,
            linespacing=1.25,
        )


def main():
    fig, ax = plt.subplots(figsize=(14, 18), dpi=160)
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(
        50,
        97.5,
        "db-agent · 一条 Query 的请求生命周期",
        ha="center",
        va="center",
        fontsize=20,
        fontweight="bold",
        color=NAVY,
    )
    ax.text(
        50,
        95.2,
        "入口预处理 → 上下文装配 → LangGraph 编排 → 返回前加工",
        ha="center",
        va="center",
        fontsize=11,
        color=MUTED,
    )

    # 1 入口预处理
    stage_frame(ax, 4, 84.5, 92, 9.5, "① 入口预处理  ·  main.py CLI / Web / Streamlit", BLUE)
    box(ax, 8, 85.5, 18, 5.5, "用户输入", "input() / SSE")
    box(ax, 32, 85.5, 20, 5.5, "退出检测", "quit / 退出 / 错别字")
    box(ax, 58, 85.5, 16, 5.5, "分流", "闲聊 / 元问题 / 业务")
    box(ax, 78, 85.5, 14, 5.5, "进入装配", "", fc="#D5E8D4", ec=GREEN, title_c=GREEN)
    arrow(ax, 26.2, 88.2, 31.6, 88.2)
    arrow(ax, 52.2, 88.2, 57.6, 88.2)
    arrow(ax, 74.2, 88.2, 77.6, 88.2)

    # 2 上下文装配 — 标题写清意图，不再拆成左侧碎字
    stage_frame(ax, 4, 68.5, 92, 14.5, "② 上下文装配  ·  把问题加工成完整输入", TEAL)
    box(ax, 8, 72.5, 24, 7, "向量记忆召回", "embedding · score ≥ 0.3")
    box(ax, 38, 72.5, 24, 7, "模板优先匹配", "高频指标命中 → 零 LLM")
    box(ax, 68, 72.5, 24, 7, "短期压缩摘要", "ConversationManager")
    box(
        ax,
        22,
        69.5,
        56,
        2.6,
        "组装 System Prompt  =  元提示 + 模板提示 + 历史记忆",
        fc="#FFF3E0",
        ec=ORANGE,
        title_c=ORANGE,
    )
    arrow(ax, 20, 72.5, 40, 72.1, color=TEAL, lw=1.4)
    arrow(ax, 50, 72.5, 50, 72.2, color=TEAL, lw=1.4)
    arrow(ax, 80, 72.5, 60, 72.1, color=TEAL, lw=1.4)

    box(ax, 30, 63.8, 40, 3.8, "输入护栏 guard_input", "注入 / 空 / 超长 · 零 token", fc="#FFF0F0", ec=RED, title_c=RED)
    box(ax, 74, 63.8, 18, 3.8, "拦截 → 直接返回", "", fc="#F8CECC", ec=RED, title_c=RED)
    arrow(ax, 50, 68.5, 50, 67.7, color=RED)
    arrow(ax, 70.2, 65.7, 73.6, 65.7, color=RED)

    # 3 LangGraph
    stage_frame(ax, 4, 22.5, 92, 39.5, "③ LangGraph 多 Agent 编排  ·  每个节点 = 一步处理", NAVY)

    box(ax, 34, 55.5, 32, 4.2, "Router 四层短路", "硬规则 → 继承 → LRU → LLM", fc=NAVY, ec=NAVY, title_c=WHITE)
    box(ax, 72, 55.5, 20, 4.2, "Clarify（可选）", "低置信度 interrupt", fc="#FFF3E0", ec=ORANGE, title_c=ORANGE)
    arrow(ax, 66.2, 57.6, 71.6, 57.6, color=ORANGE)
    ax.annotate(
        "",
        xy=(66, 59.2),
        xytext=(82, 60.5),
        arrowprops=dict(arrowstyle="-|>", color=ORANGE, lw=1.2, connectionstyle="arc3,rad=-0.3"),
    )

    agents = [
        ("SQL", "Schema · few-shot\n权限 · HITL"),
        ("Strategy", "知识库 / 口径"),
        ("HBase", "KV · 写操作 HITL"),
        ("Hive", "数仓方言"),
        ("DQ", "质量扫描（可选）"),
    ]
    aw, gap = 15.5, 1.5
    ax0 = 8
    for i, (t, b) in enumerate(agents):
        x = ax0 + i * (aw + gap)
        box(ax, x, 47.5, aw, 5.5, t, b)
    arrow(ax, 50, 55.5, 50, 53.2)

    box(ax, 8, 40.5, 28, 5.2, "工具层执行", "仅 SELECT · Entitlement\n敏感列 HITL · 成功 SQL 捕获")
    box(ax, 40, 40.5, 24, 5.2, "失败重规划 ≤1", "超时带反馈回 Router\n跳过脏缓存", fc="#FFF3E0", ec=ORANGE, title_c=ORANGE)
    box(ax, 68, 40.5, 24, 5.2, "Confidence Gate", "自评 < 0.7 → interrupt", fc="#FFF3E0", ec=ORANGE, title_c=ORANGE)
    arrow(ax, 22, 47.5, 22, 45.9)
    arrow(ax, 36.2, 43.1, 39.6, 43.1, color=ORANGE)
    arrow(ax, 64.2, 43.1, 67.6, 43.1)

    box(ax, 16, 31.5, 30, 6.5, "Analysis", "记忆 / 摘要 / 上游结果\n→ final_answer + 输出护栏", fc="#E8F5E9", ec=GREEN, title_c=GREEN)
    box(ax, 54, 31.5, 30, 6.5, "Reflection ≤2", "完整性 / 真实性 / 可用性\n不通过 → 回 Analysis", fc="#E8F5E9", ec=GREEN, title_c=GREEN)
    arrow(ax, 50, 40.5, 31, 38.2)
    arrow(ax, 46.2, 34.7, 53.6, 34.7, color=GREEN)
    ax.annotate(
        "不合格回写",
        xy=(40, 31.3),
        xytext=(69, 29.5),
        fontsize=8,
        color=GREEN,
        ha="center",
        arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=1.2, connectionstyle="arc3,rad=0.35"),
    )

    box(ax, 36, 24, 28, 3.8, "Done · Trace / Opik / 运维指标", "", fc=NAVY, ec=NAVY, title_c=WHITE)
    arrow(ax, 50, 31.5, 50, 28.0)

    ax.annotate(
        "重规划",
        xy=(42, 57.5),
        xytext=(18, 43.5),
        fontsize=8,
        color=ORANGE,
        ha="center",
        arrowprops=dict(arrowstyle="-|>", color=ORANGE, lw=1.2, connectionstyle="arc3,rad=0.45"),
    )

    # 4 返回前
    stage_frame(ax, 4, 3.5, 92, 17.5, "④ 返回前的加工", GREEN)
    box(ax, 8, 12.5, 26, 5.5, "观测埋点", "span · token · error\n→ JSONL / Opik / metrics")
    box(ax, 37, 12.5, 26, 5.5, "自学习回流", "质量门 + 敏感列审计\n→ sql_examples")
    box(ax, 66, 12.5, 26, 5.5, "记忆写入", "ConversationManager\n+ VectorMemory.remember")
    box(ax, 28, 5.5, 44, 5.0, "返回用户 final_answer", "HITL 批准后 resume 继续", fc="#D5E8D4", ec=GREEN, title_c=GREEN)
    arrow(ax, 50, 22.5, 50, 17.8, color=GREEN)
    arrow(ax, 50, 12.5, 50, 10.7, color=GREEN)

    ax.text(
        50,
        1.6,
        "横切：API 重试 · SQL 自愈 · 熔断 / 幂等 · Checkpointer（SQLite / Redis）",
        ha="center",
        va="center",
        fontsize=10,
        color=MUTED,
    )

    fig.savefig(OUT, facecolor=WHITE, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print("saved:", OUT)


if __name__ == "__main__":
    main()
