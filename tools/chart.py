"""图表渲染 Tool — 确定性输出，不调 LLM。

Analysis Agent 用它把数据可视化：折线图、饼图、柱状图。
渲染完返回图片路径，Agent 可以把路径展示给用户。
"""

import os
import warnings
from datetime import datetime
import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

from tools import tool

CHARTS_DIR = os.path.join(os.path.dirname(__file__), "..", "charts")

# 尝试加载中文字体，否则中文会变方块
try:
    _font_family = "sans-serif"
    _fonts = [f for f in fm.findSystemFonts() if "PingFang" in f or "Heiti" in f or "STHeiti" in f]
    if _fonts:
        _font_prop = fm.FontProperties(fname=_fonts[0])
        _font_family = _font_prop.get_name()
    plt.rcParams["font.family"] = _font_family
except Exception:
    pass  # 字体加载失败用默认，中文可能显示异常但不会崩
plt.rcParams["axes.unicode_minus"] = False


@tool(description=(
    "将结构化数据渲染成图表。支持折线图(line)、饼图(pie)、柱状图(bar)。"
    "数据传入 labels 和 values 两个平级列表。"
    "返回 {image_path, chart_type}；生成失败返回 {error, hint}。"
    "适用：用户问趋势、占比、排名等需要可视化的问题。"
))
def render_chart(
    chart_type: str,
    title: str,
    labels: list,
    values: list,
    x_label: str = "",
    y_label: str = "",
) -> dict:
    """chart_type: line / pie / bar
    title: 图表标题
    labels: X 轴或扇区标签列表，如 ['华东', '华南', '华北']
    values: 数值列表，如 [280000, 195000, 320000]
    x_label, y_label: 轴标签（饼图忽略）"""
    valid = {"line", "pie", "bar"}
    chart_type = chart_type.lower().strip()
    if chart_type not in valid:
        return {"error": True, "hint": f"chart_type 必须是 {valid} 之一"}

    if len(labels) != len(values):
        return {"error": True, "hint": "labels 和 values 长度必须一致"}
    if not labels:
        return {"error": True, "hint": "数据为空"}

    os.makedirs(CHARTS_DIR, exist_ok=True)

    try:
        fig, ax = plt.subplots(figsize=(10, 5))

        if chart_type == "line":
            ax.plot(labels, values, marker="o", linewidth=2, markersize=6)
            ax.set_xlabel(x_label or "X")
            ax.set_ylabel(y_label or "Y")
            ax.grid(True, alpha=0.3)

        elif chart_type == "bar":
            colors = ["#1a56db", "#e6c940", "#4caf50", "#e8553d", "#9c27b0", "#ff9800"]
            bar_colors = [colors[i % len(colors)] for i in range(len(labels))]
            ax.bar(labels, values, color=bar_colors)
            ax.set_xlabel(x_label or "")
            ax.set_ylabel(y_label or "")
            ax.grid(True, alpha=0.2, axis="y")

        elif chart_type == "pie":
            colors = ["#1a56db", "#e6c940", "#4caf50", "#e8553d", "#9c27b0", "#ff9800",
                      "#00bcd4", "#795548", "#607d8b", "#cddc39"]
            wedges, texts, autotexts = ax.pie(
                values, labels=labels, autopct="%1.1f%%",
                colors=colors[:len(labels)], startangle=90,
            )
            for t in autotexts:
                t.set_fontsize(9)

        ax.set_title(title, fontsize=14, fontweight="bold")
        fig.tight_layout()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{chart_type}_{ts}.png"
        filepath = os.path.join(CHARTS_DIR, filename)
        fig.savefig(filepath, dpi=120, bbox_inches="tight")
        plt.close(fig)

        return {
            "image_path": filepath,
            "chart_type": chart_type,
            "title": title,
            "data_points": len(labels),
        }
    except Exception as e:
        plt.close("all")
        return {"error": True, "hint": f"渲染失败: {e}"}
