#!/usr/bin/env python3
"""为大字号 PPT 生成：全链路生命周期 + 工程机制全景。

用法:
    .venv/bin/python scripts/draw_ppt_diagrams.py

输出:
    docs/diagrams/ppt/query-flow-ppt.png
    docs/diagrams/ppt/mechanism-overview-ppt.png
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(ROOT, "docs", "diagrams", "ppt")

for f in ("/System/Library/Fonts/STHeiti Medium.ttc",
          "/System/Library/Fonts/PingFang.ttc",
          "/System/Library/Fonts/STHeiti Light.ttc"):
    if os.path.exists(f):
        fm.fontManager.addfont(f)
        break
plt.rcParams["font.family"] = ["Heiti TC", "PingFang SC", "STHeiti", "Arial Unicode MS", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

NAVY, BLUE, TEAL = "#0F2A43", "#2E86DE", "#17A2B8"
ORANGE, GREEN, RED = "#F0A030", "#2E8B57", "#C0392B"
LIGHT, MUTED, DARK, WHITE = "#F2F5F8", "#5A6B7B", "#222B35", "#FFFFFF"


def rounded(ax, x, y, w, h, fc, ec=None, lw=1.5, rs=0.4):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0.02,rounding_size={rs}",
        fc=fc, ec=ec or fc, lw=lw))


def arrow(ax, x1, y1, x2, y2, color=MUTED):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=28,
        color=color, lw=2.8))


def draw_row(ax, items, y, box_h=15):
    n = len(items)
    gap = 1.2
    box_w = (94 - (n - 1) * gap) / n
    x = 3.0
    edges = []
    for title, body, color in items:
        rounded(ax, x, y, box_w, box_h, LIGHT, color, lw=2.5, rs=0.6)
        rounded(ax, x, y + box_h - 4.4, box_w, 4.4, color, rs=0.5)
        ax.text(x + box_w / 2, y + box_h - 2.2, title, ha="center", va="center",
                fontsize=20, fontweight="bold", color=WHITE)
        ax.text(x + box_w / 2, y + (box_h - 4.4) / 2 + 0.2, body, ha="center", va="center",
                fontsize=15, color=DARK, linespacing=1.4)
        edges.append((x, x + box_w))
        x += box_w + gap
    mid_y = y + box_h / 2
    for i in range(len(edges) - 1):
        arrow(ax, edges[i][1] + 0.15, mid_y, edges[i + 1][0] - 0.15, mid_y)
    return edges


def draw_query_lifecycle():
    fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=160)
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(50, 95, "一条 Query 的全链路生命周期", ha="center", va="center",
            fontsize=32, fontweight="bold", color=NAVY)
    ax.text(50, 89.5, "入口 → 护栏 → Router → 执行 Agent → 闸门 → 分析反思 → 观测收尾",
            ha="center", va="center", fontsize=18, color=MUTED)

    row1 = [
        ("1 入口", "CLI / Web / Streamlit", BLUE),
        ("2 输入护栏", "注入 / 空 / 超长\n零 token 拦截", TEAL),
        ("3 Router", "硬规则 → LRU → LLM\n产出 plan", NAVY),
        ("4 DataQuality", "首查质量预检\n只报事实", ORANGE),
    ]
    row2 = [
        ("5 SQL Agent", "Schema · few-shot\n权限 · HITL · 执行", BLUE),
        ("6 置信度门", "6 项自评\n<0.7 → 人工确认", ORANGE),
        ("7 Analysis", "结论先行\n输出护栏", TEAL),
        ("8 Reflection", "完整性 / 真实性\n不通过回写 ≤2", GREEN),
        ("9 Done", "Trace · Opik\n记忆写入", NAVY),
    ]
    draw_row(ax, row1, 66, box_h=15)
    ax.annotate("", xy=(50, 62.5), xytext=(50, 65.5),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=2.8, mutation_scale=28))
    draw_row(ax, row2, 40, box_h=15)

    rounded(ax, 3, 8, 45, 26, "#FFF8F0", ORANGE, lw=2, rs=0.5)
    ax.text(25.5, 30, "分支与回路（全链路关键）", ha="center", va="center",
            fontsize=18, fontweight="bold", color=ORANGE)
    ax.text(5, 16.5,
            "• Router 低置信度 → Clarify interrupt → 回 Router\n"
            "• SQL 超时 → 重规划 ≤1（跳过脏缓存）\n"
            "• 置信度门低分 → HITL 审批\n"
            "• Reflection 不通过 → 回 Analysis ≤2\n"
            "• 敏感列 / HBase 写 → interrupt 等人确认",
            ha="left", va="center", fontsize=15, color=DARK, linespacing=1.55)

    rounded(ax, 52, 8, 45, 26, "#F0F7F4", GREEN, lw=2, rs=0.5)
    ax.text(74.5, 30, "横切能力（贯穿全链路）", ha="center", va="center",
            fontsize=18, fontweight="bold", color=GREEN)
    ax.text(54, 16.5,
            "• 约束：RBAC 行级改写 + 三层护栏\n"
            "• 可靠性：重试 / 自愈 / 熔断 / 幂等\n"
            "• 观测：Trace JSONL + Opik + 指标\n"
            "• 记忆：成功 SQL dry-run 回流样例库\n"
            "• Checkpointer：SQLite / Redis 断点续跑",
            ha="left", va="center", fontsize=15, color=DARK, linespacing=1.55)

    out = os.path.join(OUT_DIR, "query-flow-ppt.png")
    fig.savefig(out, facecolor=WHITE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print("saved:", out)


def draw_mechanism_overview():
    fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=160)
    fig.patch.set_facecolor(WHITE)
    ax.set_facecolor(WHITE)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(50, 95, "工程机制全景 · Harness 六维", ha="center", va="center",
            fontsize=32, fontweight="bold", color=NAVY)
    ax.text(50, 89.5, "三种入口共用一套运行时：编排驱动 · 六维协作 · 可靠性横切",
            ha="center", va="center", fontsize=18, color=MUTED)

    rounded(ax, 3, 76, 94, 10, NAVY, rs=0.5)
    ax.text(50, 83.5, "入口层", ha="center", va="center", fontsize=16, fontweight="bold", color=ORANGE)
    for i, label in enumerate(["CLI  main.py", "Web  FastAPI + SSE", "Streamlit  app.py"]):
        x = 8 + i * 30
        rounded(ax, x, 77.2, 26, 4.8, BLUE, rs=0.4)
        ax.text(x + 13, 79.6, label, ha="center", va="center",
                fontsize=17, fontweight="bold", color=WHITE)
    for x in (21, 51, 81):
        ax.annotate("", xy=(x, 72.5), xytext=(x, 75.5),
                    arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=2.5, mutation_scale=24))

    rounded(ax, 28, 62, 44, 9.5, TEAL, rs=0.5)
    ax.text(50, 68.8, "编排（核心）", ha="center", va="center",
            fontsize=20, fontweight="bold", color=WHITE)
    ax.text(50, 64.5, "LangGraph 多 Agent  ·  ReAct 单 Agent  ·  Router / Checkpointer",
            ha="center", va="center", fontsize=15, color=WHITE)

    dims = [
        ("工具", "SQL / Hive / HBase\n图表 / 知识 / 记忆", BLUE),
        ("上下文", "Schema Linking\nfew-shot / 模板 / Budget", TEAL),
        ("记忆", "短期摘要\n向量长期 / Self-Query", GREEN),
        ("约束", "RBAC / 护栏\nHITL / 置信度门", ORANGE),
        ("观测", "Trace / Opik\nPrometheus / 告警", NAVY),
        ("可靠性", "重试 / 熔断 / 幂等\n重规划 / Reflection", RED),
    ]
    for i, (t, d, c) in enumerate(dims):
        col, row = i % 3, i // 3
        x = 4 + col * 31.5
        y = 33 - row * 22
        rounded(ax, x, y, 29.5, 18, LIGHT, c, lw=3, rs=0.6)
        rounded(ax, x, y + 12.5, 29.5, 5.5, c, rs=0.5)
        ax.text(x + 14.75, y + 15.2, t, ha="center", va="center",
                fontsize=22, fontweight="bold", color=WHITE)
        ax.text(x + 14.75, y + 6.2, d, ha="center", va="center",
                fontsize=16, color=DARK, linespacing=1.45)

    rounded(ax, 3, 3, 94, 7, NAVY, rs=0.4)
    ax.text(50, 6.5,
            "外置：Checkpointer（SQLite / Redis TTL）  ·  指标 /api/metrics + Webhook  ·  评测防退化",
            ha="center", va="center", fontsize=16, color=WHITE)

    out = os.path.join(OUT_DIR, "mechanism-overview-ppt.png")
    fig.savefig(out, facecolor=WHITE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print("saved:", out)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    draw_query_lifecycle()
    draw_mechanism_overview()
