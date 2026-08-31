"""ECharts 数据大屏 Tool — 生成暗色主题交互式 HTML 仪表盘。

替代旧 matplotlib PNG 方案。
支持单图和多面板 dashboard 两种模式，浏览器直接打开。
基于 ECharts 5.5 CDN，零依赖安装。
"""

import json
import os
from datetime import datetime

CHART_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "charts"))

# 最近一次生成的大屏相对 URL（前端 Dashboard tab 用，单机 demo 够用）
_latest_dashboard_url: str | None = None


def get_latest_dashboard_url() -> str | None:
    return _latest_dashboard_url


# 暗色主题调色板
PALETTE = ["#5470c6", "#fac858", "#ee6666", "#91cc75", "#73c0de", "#3ba272",
           "#fc8452", "#9a60b4", "#ea7ccc", "#48b8d0"]

# ECharts 暗色主题
DARK_THEME = {
    "color": PALETTE,
    "backgroundColor": "transparent",
    "textStyle": {"color": "#e0e6f0"},
    "title": {"textStyle": {"color": "#e0e6f0"}},
    "legend": {"textStyle": {"color": "#a0a8b8"}},
    "tooltip": {"backgroundColor": "rgba(20,24,36,0.95)", "borderColor": "#333",
                "textStyle": {"color": "#e0e6f0"}},
}

TOOL_SCHEMA = {
    "name": "render_chart",
    "description": (
        "生成 ECharts 数据大屏 HTML 文件并返回路径。暗色主题、鼠标交互、自适应布局。\n"
        "两种模式：\n"
        "1. 单图模式：传 chart_type / title / labels / values\n"
        "2. 大屏模式：传 panels 数组，每个面板 {type, title, labels, values}\n"
        "type: line(折线趋势), bar(柱状对比), pie(饼图占比)\n"
        "适用：趋势、占比、排名、对比等需要可视化的场景。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "大屏/图表标题",
            },
            "chart_type": {
                "type": "string",
                "description": "单图模式：line / bar / pie。大屏模式传 panels 时忽略",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "单图模式：X轴或扇区标签",
            },
            "values": {
                "type": "array",
                "items": {"type": "number"},
                "description": "单图模式：数值列表",
            },
            "panels": {
                "type": "array",
                "description": (
                    "大屏模式：多个图表面板。每项 {type, title, labels, values}。"
                    "推荐 2-6 个面板。type 可选 line/bar/pie"
                ),
            },
        },
        "required": ["title"],
    },
}

# ── ECharts 配置生成 ──

def _line_option(labels, values, panel_title):
    return {
        "title": {"text": panel_title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": labels,
                  "axisLabel": {"rotate": len(labels) > 6 and 30 or 0}},
        "yAxis": {"type": "value"},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "series": [{"type": "line", "data": values, "smooth": True,
                    "symbol": "circle", "symbolSize": 6,
                    "areaStyle": {"color": {"type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                        "colorStops": [{"offset": 0, "color": "rgba(84,112,198,0.25)"},
                                       {"offset": 1, "color": "rgba(84,112,198,0.02)"}]}}}],
    }


def _bar_option(labels, values, panel_title):
    return {
        "title": {"text": panel_title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "xAxis": {"type": "category", "data": labels,
                  "axisLabel": {"rotate": len(labels) > 6 and 30 or 0}},
        "yAxis": {"type": "value"},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "series": [{"type": "bar", "data": values, "barWidth": "60%",
                    "itemStyle": {"borderRadius": [4, 4, 0, 0],
                        "color": {"type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                            "colorStops": [{"offset": 0, "color": "#5470c6"},
                                           {"offset": 1, "color": "#3b5bb5"}]}}}],
    }


def _pie_option(labels, values, panel_title):
    data = [{"name": label, "value": v} for label, v in zip(labels, values)]
    return {
        "title": {"text": panel_title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"bottom": 0, "textStyle": {"fontSize": 10}},
        "series": [{"type": "pie", "radius": ["40%", "70%"], "center": ["50%", "50%"],
                    "itemStyle": {"borderRadius": 6, "borderColor": "rgba(20,24,36,1)", "borderWidth": 2},
                    "label": {"show": False},
                    "emphasis": {"label": {"show": True, "fontSize": 14, "fontWeight": "bold"}},
                    "data": data}],
    }


_BUILDERS = {"line": _line_option, "bar": _bar_option, "pie": _pie_option}


# ── HTML 页面生成 ──

def _build_html(title, panels):
    n = len(panels)
    if n <= 1:
        cols = "1fr"
    elif n == 2:
        cols = "1fr 1fr"
    elif n <= 4:
        cols = "1fr 1fr"
    else:
        cols = "1fr 1fr 1fr"

    opts = []
    for i, p in enumerate(panels):
        build = _BUILDERS.get(p.get("type", "bar"), _bar_option)
        opt = build(p.get("labels", []), p.get("values", []), p.get("title", f"面板 {i+1}"))
        opts.append(json.dumps(opt, ensure_ascii=False))

    kpi = ""
    for p in panels:
        vals = p.get("values", [])
        if vals:
            label = p.get("title", "")[:6]
            total = sum(vals)
            display = total / len(vals) if len(vals) > 6 else total
            suffix = "(均值)" if len(vals) > 6 else ""
            kpi += (f'<div class="kpi-card"><span class="kpi-value">{display:,.0f}</span>'
                    f'<span class="kpi-label">{label}{suffix}</span></div>')

    chart_divs = "".join(f'<div class="panel"><div id="chart{i}" class="chart"></div></div>'
                         for i in range(n))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: "PingFang SC","Microsoft YaHei",sans-serif;
  background: linear-gradient(135deg, #0a0e1a 0%, #101830 50%, #0d1124 100%);
  color: #e0e6f0; min-height:100vh; overflow-x:hidden;
}}
.header {{ text-align:center; padding:24px 0 8px; }}
.header h1 {{
  font-size:28px; font-weight:600; letter-spacing:4px;
  background: linear-gradient(90deg, #5470c6, #91cc75, #fac858);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
}}
.header .sub {{ font-size:13px; color:#556; margin-top:6px; letter-spacing:2px; }}
.header::after {{
  content:''; display:block; width:60%; height:1px;
  background: linear-gradient(90deg, transparent, #334, transparent); margin:16px auto 0;
}}
.kpi-row {{ display:flex; justify-content:center; gap:16px; padding:12px 24px; flex-wrap:wrap; }}
.kpi-card {{
  background: rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);
  border-radius:8px; padding:12px 24px; text-align:center; min-width:100px;
}}
.kpi-value {{ display:block; font-size:22px; font-weight:700; color:#91cc75; }}
.kpi-label {{ display:block; font-size:11px; color:#667; margin-top:4px; }}
.grid {{ display:grid; grid-template-columns:{cols}; gap:16px; padding:12px 24px 24px; max-width:1400px; margin:0 auto; }}
.panel {{
  background: rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06);
  border-radius:10px; padding:12px; min-height:300px; transition: border-color 0.3s;
}}
.panel:hover {{ border-color: rgba(84,112,198,0.4); }}
.chart {{ width:100%; height:320px; }}
.footer {{ text-align:center; padding:8px; color:#334; font-size:11px; letter-spacing:2px; }}
@media (max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="header">
  <h1>{title}</h1>
  <div class="sub">REAL-TIME ANALYTICS DASHBOARD</div>
</div>
<div class="kpi-row">{kpi}</div>
<div class="grid">{chart_divs}</div>
<div class="footer">DB-AGENT &middot; DATA COMMAND CENTER</div>
<script>
echarts.registerTheme('dark', {json.dumps(DARK_THEME, ensure_ascii=False)});
var options = {json.dumps(opts, ensure_ascii=False)};
var charts = [];
window.addEventListener('resize', function() {{ charts.forEach(function(c) {{ c.resize(); }}); }});
options.forEach(function(opt, i) {{
  var dom = document.getElementById('chart' + i);
  if (!dom) return;
  var c = echarts.init(dom, 'dark');
  c.setOption(JSON.parse(opt));
  charts.push(c);
}});
</script>
</body>
</html>"""


# ── 工具函数 ──

def render_chart(
    title: str,
    chart_type: str = "",
    labels: list | None = None,
    values: list | None = None,
    panels: list | None = None,
) -> dict:
    labels = labels or []
    values = values or []
    panels = panels or []

    if panels:
        for p in panels:
            t = p.get("type", "bar")
            if t not in _BUILDERS:
                return {"error": True, "hint": f"面板 '{p.get('title', '?')}' type '{t}' 无效，仅支持 line/bar/pie"}
            if len(p.get("labels", [])) != len(p.get("values", [])):
                return {"error": True, "hint": f"面板 '{p.get('title', '?')}' labels/values 长度不一致"}
    elif chart_type and labels and values:
        chart_type = chart_type.lower().strip()
        if chart_type not in _BUILDERS:
            return {"error": True, "hint": f"chart_type 仅支持 line/bar/pie，收到: {chart_type}"}
        if len(labels) != len(values):
            return {"error": True, "hint": "labels 和 values 长度必须一致"}
        panels = [{"type": chart_type, "title": title, "labels": labels, "values": values}]
    else:
        return {"error": True, "hint": "请提供 panels（大屏模式）或 chart_type+labels+values（单图模式）"}

    os.makedirs(CHART_DIR, exist_ok=True)
    html = _build_html(title, panels)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"dashboard_{ts}.html"
    filepath = os.path.join(CHART_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    global _latest_dashboard_url
    _latest_dashboard_url = f"/charts/{filename}"

    return {
        "dashboard_path": filepath,
        "url": _latest_dashboard_url,
        "panels": len(panels),
        "title": title,
        "hint": f"大屏已生成，共 {len(panels)} 个面板。在浏览器中打开。",
    }


render_chart.tool_schema = TOOL_SCHEMA
# 兼容旧 import: from harness.tools.chart import render_chart; render_chart.tool_schema
tool_schema = TOOL_SCHEMA
