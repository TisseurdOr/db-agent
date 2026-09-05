"""生成《Agent 可复用模块库》讲解 PPT。

用法:
    python scripts/generate_reusable_modules_deck.py
输出:
    docs/项目介绍/Agent可复用模块库.pptx
"""

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

NAVY = RGBColor(0x0F, 0x2A, 0x43)
BLUE = RGBColor(0x2E, 0x86, 0xDE)
TEAL = RGBColor(0x17, 0xA2, 0xB8)
ORANGE = RGBColor(0xF0, 0xA0, 0x30)
GREEN = RGBColor(0x2E, 0x8B, 0x57)
RED = RGBColor(0xC0, 0x39, 0x2B)
LIGHT = RGBColor(0xF2, 0xF5, 0xF8)
MUTED = RGBColor(0x5A, 0x6B, 0x7B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARK = RGBColor(0x22, 0x2B, 0x35)
SW, SH = Inches(13.333), Inches(7.5)
FONT = "PingFang SC"


def _set_run(run, text, size, color, bold=False):
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = FONT
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", FONT)


def add_text(slide, x, y, w, h, lines, size=14, color=DARK, bold=False,
             align=PP_ALIGN.LEFT, spacing=1.15):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    first = True
    for line in lines:
        item = line if isinstance(line, tuple) else (line, 0, size, bold, color)
        text, level, sz, bd, col = item
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.level = level
        p.line_spacing = spacing
        p.space_after = Pt(4)
        run = p.add_run()
        _set_run(run, text, sz, col, bd)
    return box


def add_rect(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    sp = slide.shapes.add_shape(shape, x, y, w, h)
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid()
        sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(1)
    sp.shadow.inherit = False
    return sp


def new_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def add_header(slide, title, subtitle="", page=None):
    add_rect(slide, 0, 0, SW, Inches(0.16), fill=BLUE)
    add_text(slide, Inches(0.55), Inches(0.35), Inches(11.0), Inches(0.65),
             [(title, 0, 30, True, NAVY)])
    if subtitle:
        add_text(slide, Inches(0.57), Inches(1.02), Inches(11.0), Inches(0.4),
                 [(subtitle, 0, 14, False, MUTED)])
    if page is not None:
        add_text(slide, Inches(12.3), Inches(7.05), Inches(0.8), Inches(0.35),
                 [(str(page), 0, 12, False, MUTED)], align=PP_ALIGN.RIGHT)


def add_card(slide, x, y, w, h, title, bullets, fill=LIGHT, title_color=NAVY,
             body_size=13, title_size=16):
    add_rect(slide, x, y, w, h, fill=fill)
    add_text(slide, x + Inches(0.25), y + Inches(0.18), w - Inches(0.5), Inches(0.4),
             [(title, 0, title_size, True, title_color)])
    lines = [(("• " + b), 0, body_size, False, DARK) for b in bullets]
    add_text(slide, x + Inches(0.25), y + Inches(0.62), w - Inches(0.5), h - Inches(0.8),
             lines, size=body_size, spacing=1.12)


def build():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH

    # 1 封面
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.7), SW, Inches(0.06), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(1.8), Inches(11.3), Inches(1.2),
             [("Agent 可复用模块库", 0, 56, True, WHITE)])
    add_text(s, Inches(1.0), Inches(3.0), Inches(11.3), Inches(0.7),
             [("从 db-agent 复盘提炼的拼图式 Agent 工程经验", 0, 24, True, TEAL)])
    add_text(s, Inches(1.0), Inches(3.8), Inches(11.3), Inches(1.0),
             [("不是从零搭 Agent，而是“选积木 + 接管线 + 配规则”", 0, 18, False, LIGHT),
              ("复盘文章：docs/项目介绍/项目复盘-可复用模块库.md", 0, 13, False, MUTED)])
    add_text(s, Inches(1.0), Inches(6.6), Inches(11.3), Inches(0.5),
             [("2026 · 经验复盘", 0, 13, False, MUTED)])

    # 2 复盘结论
    s = new_slide(prs)
    add_header(s, "复盘结论", "五条可移植原则", 2)
    principles = [
        ("确定性优先", "规则 > 缓存 > LLM，LLM 当兜底"),
        ("循环有预算", "重试 / 重写 / 重规划都设上限"),
        ("能力可降级", "增强组件挂了，主流程不挂"),
        ("质量门前置", "写库、回流、发布先校验"),
        ("评测驱动", "先量化，再优化，改完跑回归"),
    ]
    y = Inches(1.55)
    for i, (t, d) in enumerate(principles):
        add_rect(s, Inches(0.8), y, Inches(11.7), Inches(0.9), fill=LIGHT)
        add_rect(s, Inches(0.8), y, Inches(0.18), Inches(0.9), fill=BLUE)
        add_text(s, Inches(1.2), y + Inches(0.1), Inches(4.0), Inches(0.7),
                 [(t, 0, 18, True, NAVY)])
        add_text(s, Inches(5.3), y + Inches(0.1), Inches(7.0), Inches(0.7),
                 [(d, 0, 14, False, MUTED)])
        y += Inches(1.05)

    # 3 拼图理念
    s = new_slide(prs)
    add_header(s, "拼图理念", "模块 = 积木，业务 = 拼装说明书", 3)
    add_card(s, Inches(0.7), Inches(1.6), Inches(3.9), Inches(4.8),
             "直接用", [
                 "工具框架 @tool",
                 "重试 / 熔断 / 幂等",
                 "三层护栏 + RBAC + HITL",
                 "Trace / Opik / 指标 / 告警",
                 "TokenBudget / 窗口压缩",
                 "评测框架 + LLM-as-Judge",
             ], fill=LIGHT, title_color=GREEN)
    add_card(s, Inches(4.8), Inches(1.6), Inches(3.9), Inches(4.8),
             "改配置", [
                 "Router 三层短路",
                 "短期 / 向量记忆 / Self-Query",
                 "HyDE + Rerank",
                 "Task board / Checkpointer",
                 "自学习质量门",
             ], fill=LIGHT, title_color=ORANGE)
    add_card(s, Inches(8.9), Inches(1.6), Inches(3.9), Inches(4.8),
             "按业务重写", [
                 "Agent Prompt + 工具绑定",
                 "Schema Linking / 模板 / 样例库",
                 "具体数据源工具",
                 "业务种子数据 / 前端",
             ], fill=LIGHT, title_color=RED)
    add_text(s, Inches(0.7), Inches(6.6), Inches(12.0), Inches(0.5),
             [("关键：业务语义和流程永远重写，工程机制可以带走。", 0, 14, True, NAVY)])

    # 4 模块复用度总览
    s = new_slide(prs)
    add_header(s, "模块复用度总览", "16 个模块，按复用度分三档", 4)
    rows = [
        ("直接用", GREEN, "@tool / 重试熔断幂等 / 三层护栏 / RBAC+HITL / Trace-Opik-指标 / TokenBudget / 评测框架"),
        ("改配置", ORANGE, "Router / 记忆 / HyDE+Rerank / Task board / Checkpointer / 自学习质量门"),
        ("重写", RED, "Agent Prompt / Schema Linking / 模板 / 样例库 / 数据源工具 / 种子数据 / 前端"),
    ]
    y = Inches(1.55)
    for title, color, desc in rows:
        add_rect(s, Inches(0.7), y, Inches(12.0), Inches(1.45), fill=LIGHT)
        add_rect(s, Inches(0.7), y, Inches(2.3), Inches(1.45), fill=color)
        add_text(s, Inches(0.7), y + Inches(0.5), Inches(2.3), Inches(0.5),
                 [(title, 0, 16, True, WHITE)], align=PP_ALIGN.CENTER)
        add_text(s, Inches(3.3), y + Inches(0.18), Inches(9.2), Inches(1.1),
                 [(desc, 0, 14, False, DARK)])
        y += Inches(1.6)

    # 5 直接复用模块
    s = new_slide(prs)
    add_header(s, "直接复用模块", "不依赖业务，拿来即用", 5)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(2.7),
             "工程底座", [
                 "@tool 自动 schema",
                 "重试 + 熔断 + 幂等",
                 "TokenBudget + 窗口压缩",
             ], fill=LIGHT, title_color=GREEN)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(2.7),
             "安全与观测", [
                 "三层护栏",
                 "RBAC + HITL",
                 "Trace / Opik / 指标 / 告警",
             ], fill=LIGHT, title_color=GREEN)
    add_card(s, Inches(0.7), Inches(4.45), Inches(12.0), Inches(2.1),
             "评测框架", [
                 "零 API 冒烟 / 单元 / 集成分层",
                 "Eval + LLM-as-Judge（Judge 与被测模型分开）",
                 "检索消融评测，先量化再优化",
             ], fill=LIGHT, title_color=GREEN)

    # 6 改配置模块
    s = new_slide(prs)
    add_header(s, "改配置即可用模块", "换规则、换模型、换存储", 6)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(2.7),
             "路由与状态", [
                 "Router：硬规则表换业务关键词",
                 "Task board：落盘目录可配",
                 "Checkpointer：SQLite / Redis 可切",
             ], fill=LIGHT, title_color=ORANGE)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(2.7),
             "记忆与检索", [
                 "短期 / 向量记忆：换后端",
                 "Self-Query：字段白名单可配",
                 "HyDE + Rerank：换模型 / 本地 reranker",
             ], fill=LIGHT, title_color=ORANGE)
    add_card(s, Inches(0.7), Inches(4.45), Inches(12.0), Inches(2.1),
             "自学习质量门", [
                 "从工具层捕获成功执行事实",
                 "质量门 + dry-run 校验",
                 "HITL 批准样本打“人工背书”标记回流",
             ], fill=LIGHT, title_color=ORANGE)

    # 7 重写模块
    s = new_slide(prs)
    add_header(s, "按业务重写模块", "业务语义和流程必须重写", 7)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(2.7),
             "Agent 层", [
                 "Prompt + 工具绑定",
                 "职责边界跟着业务走",
                 "多 Agent 分工表",
             ], fill=LIGHT, title_color=RED)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(2.7),
             "数据层", [
                 "Schema Linking / 表描述",
                 "指标口径 / 模板 / 样例库",
                 "具体数据源连接工具",
             ], fill=LIGHT, title_color=RED)
    add_card(s, Inches(0.7), Inches(4.45), Inches(12.0), Inches(2.1),
             "交付层", [
                 "业务种子数据",
                 "前端交互",
                 "Golden Set 评测用例（业务的验收标准）",
             ], fill=LIGHT, title_color=RED)

    # 8 拼新 Agent 示例
    s = new_slide(prs)
    add_header(s, "拼一个新 Agent", "三个示例：NL2SQL / 客服 / 研报", 8)
    add_card(s, Inches(0.7), Inches(1.55), Inches(3.9), Inches(4.9),
             "NL2SQL 分析", [
                 "直接用：工具框架 + 护栏 + RBAC/HITL + 评测",
                 "改配置：Router / Schema Linking / few-shot / 记忆",
                 "重写：表描述、指标口径、SQL 工具",
             ], fill=LIGHT, title_color=BLUE, body_size=12)
    add_card(s, Inches(4.8), Inches(1.55), Inches(3.9), Inches(4.9),
             "企业客服", [
                 "直接用：重试熔断幂等 + 护栏 + 记忆 + 观测 + 评测",
                 "改配置：Router 意图表 / RAGPipeline / 质量门",
                 "重写：知识库加载、工单/CRM 工具、前端",
             ], fill=LIGHT, title_color=TEAL, body_size=12)
    add_card(s, Inches(8.9), Inches(1.55), Inches(3.9), Inches(4.9),
             "研报分析", [
                 "直接用：编排骨架 + 工具框架 + 评测",
                 "改配置：多 Agent 分工 / 检索 / Trace",
                 "重写：研报解析、结构化提取、指标口径",
             ], fill=LIGHT, title_color=GREEN, body_size=12)

    # 9 关键工程经验
    s = new_slide(prs)
    add_header(s, "关键工程经验", "带得走的五条", 9)
    lessons = [
        "确定性优先：高频请求用规则和缓存，LLM 只当兜底",
        "所有循环都有预算：Reflection ≤2、重规划 ≤1，防死循环烧钱",
        "能力可降级：embedding / Redis / 检索挂了，主流程不挂",
        "质量门前置：写库、回流、发布前先校验",
        "评测驱动改动：先量化每个组件贡献，再优化，改完跑回归",
    ]
    y = Inches(1.55)
    for i, lesson in enumerate(lessons, 1):
        add_rect(s, Inches(0.8), y, Inches(11.7), Inches(0.88), fill=LIGHT)
        add_text(s, Inches(1.1), y + Inches(0.18), Inches(0.9), Inches(0.5),
                 [(f"0{i}", 0, 18, True, BLUE)])
        add_text(s, Inches(2.1), y + Inches(0.12), Inches(10.2), Inches(0.7),
                 [(lesson, 0, 14, False, DARK)])
        y += Inches(1.02)

    # 10 常见坑
    s = new_slide(prs)
    add_header(s, "常见坑", "复盘里踩过的", 10)
    pits = [
        "SQL 检查用正则不是 AST，复杂查询可能绕过",
        "行级权限只支持简单 SQL，子查询/CTE/UNION 会漏",
        "HyDE + Rerank 写好了但没接主链路，等于白写",
        "单机 SQLite 并发会锁库，HBase 内存模拟重启丢数据",
        "审计/Feedback 明文落盘，缺加密、保留策略和清理",
    ]
    y = Inches(1.55)
    for i, pit in enumerate(pits, 1):
        add_rect(s, Inches(0.8), y, Inches(11.7), Inches(0.88), fill=LIGHT)
        add_text(s, Inches(1.1), y + Inches(0.18), Inches(0.9), Inches(0.5),
                 [(f"0{i}", 0, 18, True, RED)])
        add_text(s, Inches(2.1), y + Inches(0.12), Inches(10.2), Inches(0.7),
                 [(pit, 0, 14, False, DARK)])
        y += Inches(1.02)

    # 11 落地路线
    s = new_slide(prs)
    add_header(s, "落地路线", "三步把项目变成模块库", 11)
    steps = [
        ("Step 1", "盘点模块", "按“直接用 / 改配置 / 重写”给现有代码分档"),
        ("Step 2", "抽公共层", "工具、护栏、记忆、评测、观测收敛成独立包"),
        ("Step 3", "接新业务", "新 Agent = 选模块 + 写业务语义 + 配评测"),
    ]
    y = Inches(1.6)
    for tag, title, desc in steps:
        add_rect(s, Inches(0.9), y, Inches(11.5), Inches(1.35), fill=LIGHT)
        add_rect(s, Inches(0.9), y, Inches(2.0), Inches(1.35), fill=NAVY)
        add_text(s, Inches(0.9), y + Inches(0.5), Inches(2.0), Inches(0.5),
                 [(tag, 0, 16, True, WHITE)], align=PP_ALIGN.CENTER)
        add_text(s, Inches(3.2), y + Inches(0.18), Inches(3.6), Inches(0.5),
                 [(title, 0, 18, True, NAVY)])
        add_text(s, Inches(6.9), y + Inches(0.18), Inches(5.3), Inches(1.0),
                 [(desc, 0, 13, False, MUTED)])
        y += Inches(1.55)

    # 12 结尾
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.6), SW, Inches(0.06), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(2.6), Inches(11.3), Inches(1.0),
             [("先搭积木库，再拼业务 Agent", 0, 40, True, WHITE)])
    add_text(s, Inches(1.0), Inches(4.0), Inches(11.3), Inches(0.6),
             [("工具、护栏、记忆、评测、观测可以带走；业务语义和流程必须重写。", 0, 18, True, TEAL)])
    add_text(s, Inches(1.0), Inches(6.2), Inches(11.3), Inches(0.5),
             [("复盘文章：docs/项目介绍/项目复盘-可复用模块库.md", 0, 13, False, MUTED)])

    out = "docs/项目介绍/Agent可复用模块库.pptx"
    prs.save(out)
    print(f"已生成: {out}")


if __name__ == "__main__":
    build()
