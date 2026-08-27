#!/usr/bin/env python3
"""生成 db-agent 五阶段演进图（等高分栏、无重叠）。"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

for f in ("/System/Library/Fonts/STHeiti Medium.ttc",
          "/System/Library/Fonts/PingFang.ttc",
          "/System/Library/Fonts/STHeiti Light.ttc"):
    if os.path.exists(f):
        fm.fontManager.addfont(f)
        break
plt.rcParams["font.family"] = ["Heiti TC", "PingFang SC", "STHeiti", "Arial Unicode MS", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

STAGES = [
    {
        "title": "阶段一 · 单 Agent",
        "subtitle": "模型能干活",
        "items": [
            "手写 ReAct（弃 AgentExecutor）",
            "工具真执行 · SQLite 回喂",
            "结构化错误 {error, hint}",
            "Prompt Caching · 成本 ~1/200",
        ],
        "output": "产出：能稳定执行的 CLI Agent",
        "color": "#2E86DE",
    },
    {
        "title": "阶段二 · 多 Agent",
        "subtitle": "从一脑到一组",
        "items": [
            "LangGraph · 10 节点 / 6 Agent",
            "Router 四层：规则→LRU→LLM",
            "失败重规划（上限 1 次）",
            "按引擎拆分 SQL / Hive / HBase",
        ],
        "output": "产出：多引擎协同编排系统",
        "color": "#17A2B8",
    },
    {
        "title": "阶段三 · 权限安全",
        "subtitle": "Prompt 拦不住越权",
        "items": [
            "5 角色 RBAC · 工具/表/行级",
            "行级改写 WHERE dept_id=X",
            "三层护栏：输入 / SQL / 输出",
            "HITL · interrupt() 暂停审批",
        ],
        "output": "产出：权限下沉工具层闭环",
        "color": "#F0A030",
    },
    {
        "title": "阶段四 · 可靠性",
        "subtitle": "挂了也不崩",
        "items": [
            "指数退避重试 · 429 / 5xx",
            "SQL 自愈重写 + 失败重规划",
            "熔断降级 · open → 半开",
            "幂等 TTL + 告警 Webhook",
        ],
        "output": "产出：重试→自愈→熔断闭环",
        "color": "#C0392B",
    },
    {
        "title": "阶段五 · 工程化",
        "subtitle": "从 demo 到产品",
        "items": [
            "CLI 产品化 · db-agent 命令",
            "423 测试离线 · ~10s 真绿",
            "Eval + Judge · 防静默退化",
            "Redis / Milvus 可切换后端",
        ],
        "output": "产出：能上线的 CLI 产品",
        "color": "#7B2FBE",
    },
]

OUT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "docs", "diagrams", "five_stage_evolution.png"))

# 更宽画布，给五列留气口
fig, ax = plt.subplots(figsize=(22, 11), dpi=160)
fig.patch.set_facecolor("#FFFFFF")
ax.set_facecolor("#FFFFFF")
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis("off")

NAVY, MUTED, LIGHT, DARK = "#0F2A43", "#5A6B7B", "#F2F5F8", "#222B35"

# 顶栏：标题区独占，不与列重叠
ax.text(50, 96, "db-agent 五阶段演进", ha="center", va="center",
        fontsize=28, fontweight="bold", color=NAVY)
ax.text(50, 91.5, "让模型写 SQL 不出事  ·  ~2.1 万行  ·  423 测试  ·  47 评测",
        ha="center", va="center", fontsize=14, color=MUTED)

n = len(STAGES)
col_w = 17.2
gap = 2.2
total_w = n * col_w + (n - 1) * gap
x0 = (100 - total_w) / 2

# 列内容从 y=86 往下，固定槽位
COL_TOP = 86.0
HEADER_H = 7.5
SUB_GAP = 3.8
ITEM_H = 7.2
ITEM_GAP = 2.0
N_ITEMS = 4
OUT_H = 7.0
FOOT_GAP = 2.2

boxes = []
for i, s in enumerate(STAGES):
    x = x0 + i * (col_w + gap)
    boxes.append((x, x + col_w))
    cx = x + col_w / 2

    # 计算总高度并画列底
    body_h = HEADER_H + SUB_GAP + N_ITEMS * ITEM_H + (N_ITEMS - 1) * ITEM_GAP + FOOT_GAP + OUT_H + 2.5
    col_bottom = COL_TOP - body_h
    ax.add_patch(FancyBboxPatch(
        (x, col_bottom), col_w, body_h,
        boxstyle="round,pad=0.12,rounding_size=0.7",
        fc=LIGHT, ec="#D8DEE6", lw=1.2, zorder=1))

    # 标题条
    hy = COL_TOP - HEADER_H
    ax.add_patch(FancyBboxPatch(
        (x + 0.45, hy), col_w - 0.9, HEADER_H,
        boxstyle="round,pad=0.12,rounding_size=0.55",
        fc=s["color"], ec="none", zorder=2))
    ax.text(cx, hy + HEADER_H / 2, s["title"], ha="center", va="center",
            fontsize=14, fontweight="bold", color="white", zorder=3)

    # 副标题（单行，不换行）
    sy = hy - SUB_GAP / 2 - 0.2
    ax.text(cx, sy, s["subtitle"], ha="center", va="center",
            fontsize=12, color=MUTED, zorder=3)

    # 要点
    item_top = hy - SUB_GAP
    for j, item in enumerate(s["items"]):
        iy = item_top - (j + 1) * ITEM_H - j * ITEM_GAP
        ax.add_patch(FancyBboxPatch(
            (x + 0.55, iy), col_w - 1.1, ITEM_H,
            boxstyle="round,pad=0.1,rounding_size=0.45",
            fc="#FFFFFF", ec=s["color"], lw=1.6, zorder=2))
        ax.text(cx, iy + ITEM_H / 2, item, ha="center", va="center",
                fontsize=11.5, color=DARK, zorder=3)

    # 产出
    out_y = item_top - N_ITEMS * ITEM_H - (N_ITEMS - 1) * ITEM_GAP - FOOT_GAP - OUT_H
    ax.add_patch(FancyBboxPatch(
        (x + 0.55, out_y), col_w - 1.1, OUT_H,
        boxstyle="round,pad=0.1,rounding_size=0.45",
        fc=s["color"], ec="none", zorder=2))
    ax.text(cx, out_y + OUT_H / 2, s["output"], ha="center", va="center",
            fontsize=11.5, fontweight="bold", color="white", zorder=3)

    ax.text(cx, out_y - 1.8, f"Stage {i + 1}", ha="center", va="center",
            fontsize=10, color=MUTED, zorder=3)

# 箭头画在标题条中线
arrow_y = COL_TOP - HEADER_H / 2
for i in range(n - 1):
    x1, x2 = boxes[i][1], boxes[i + 1][0]
    ax.add_patch(FancyArrowPatch(
        (x1 + 0.1, arrow_y), (x2 - 0.1, arrow_y),
        arrowstyle="-|>", mutation_scale=20, color="#94A3B8", lw=2.0, zorder=4))

ax.text(50, 2.2,
        "核心主线：工具真执行 → 权限硬拦截 → 自愈熔断 → 状态外置 → 评测兜底 —— 驯服模型不确定性",
        ha="center", va="center", fontsize=13, color=MUTED)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
# 不用 tight，避免裁切后元素显得挤在一起
fig.savefig(OUT, facecolor="#FFFFFF", dpi=160)
plt.close(fig)
print("saved:", OUT)
