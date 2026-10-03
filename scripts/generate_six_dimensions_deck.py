"""生成《db-agent · Harness 六维深度探索》PPT。

用法:
    cd junior-to-senior/db-agent && .venv/bin/python scripts/generate_six_dimensions_deck.py

输出:
    docs/面试/db-agent-六维深度探索.pptx

依赖:
    python-pptx（本地离线生成）
    素材: docs/diagrams/ppt/*.png（机制全景 / Query 链路 / 安全链路 / 三层自愈）
"""
from __future__ import annotations

import os
import struct

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def IMG(*p):
    return os.path.join(ROOT, "docs", "diagrams", "ppt", *p)
OUT = os.path.join(ROOT, "docs", "面试", "db-agent-六维深度探索.pptx")

# ── 配色 ─────────────────────────────────────────────────────────────
NAVY = RGBColor(0x0F, 0x2A, 0x43)
BLUE = RGBColor(0x2E, 0x86, 0xDE)
TEAL = RGBColor(0x17, 0xA2, 0xB8)
ORANGE = RGBColor(0xF0, 0xA0, 0x30)
GREEN = RGBColor(0x2E, 0x8B, 0x57)
RED = RGBColor(0xC0, 0x39, 0x2B)
PURPLE = RGBColor(0x7D, 0x3C, 0x98)
LIGHT = RGBColor(0xF2, 0xF5, 0xF8)
MUTED = RGBColor(0x5A, 0x6B, 0x7B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARK = RGBColor(0x22, 0x2B, 0x35)

SW, SH = Inches(13.333), Inches(7.5)
FONT = "PingFang SC"

PAGE = [0]


def _set_run(run, text, size, color, bold=False, font=FONT):
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", font)


def add_text(slide, x, y, w, h, lines, size: float = 14.0, color=DARK, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.12):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    first = True
    for line in lines:
        if isinstance(line, str):
            item = (line, 0, size, bold, color)
        else:
            item = line
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


def add_para_runs(slide, x, y, w, h, runs, align=PP_ALIGN.LEFT,
                  anchor=MSO_ANCHOR.TOP, spacing=1.18):
    """单段多 run 文本：runs = [(text, color, bold, size), ...]"""
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = spacing
    for text, color, bold, size in runs:
        run = p.add_run()
        _set_run(run, text, size, color, bold)
    return box


def add_rect(slide, x, y, w, h, fill=None, line=None, line_w=1.0,
             shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=None):
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
        sp.line.width = Pt(line_w)
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            sp.adjustments[0] = radius
        except Exception:
            pass
    sp.shadow.inherit = False
    return sp


def fill_shape_text(sp, text, size: float = 13.0, color=WHITE, bold=True,
                    align=PP_ALIGN.CENTER, font=FONT):
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.05)
    tf.margin_right = Inches(0.05)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    _set_run(run, text, size, color, bold, font)


def add_arrow(slide, x, y, w, h, color=MUTED, direction="right"):
    shape = MSO_SHAPE.RIGHT_ARROW if direction == "right" else MSO_SHAPE.DOWN_ARROW
    sp = slide.shapes.add_shape(shape, x, y, w, h)
    sp.fill.solid()
    sp.fill.fore_color.rgb = color
    sp.line.fill.background()
    sp.shadow.inherit = False
    return sp


def new_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def add_header(slide, title, subtitle="", page=None):
    add_rect(slide, 0, 0, SW, Inches(0.16), fill=BLUE)
    add_text(slide, Inches(0.55), Inches(0.32), Inches(11.6), Inches(0.65),
             [(title, 0, 27, True, NAVY)])
    if subtitle:
        add_text(slide, Inches(0.57), Inches(1.0), Inches(11.6), Inches(0.4),
                 [(subtitle, 0, 14, False, MUTED)])
    if page is not None:
        add_text(slide, Inches(12.35), Inches(7.06), Inches(0.8), Inches(0.35),
                 [(str(page), 0, 12, False, MUTED)], align=PP_ALIGN.RIGHT)


def next_page():
    PAGE[0] += 1
    return PAGE[0]


def add_bullet_card(slide, x, y, w, h, title, bullets, fill=LIGHT, title_color=NAVY,
                    title_size=16, body_size=12.5, title_bar=None):
    add_rect(slide, x, y, w, h, fill=fill)
    ty = y + Inches(0.14)
    if title_bar is not None:
        add_rect(slide, x, y, w, Inches(0.42), fill=title_bar)
        add_text(slide, x + Inches(0.2), y + Inches(0.05), w - Inches(0.4), Inches(0.32),
                 [(title, 0, title_size, True, WHITE)])
        ty = y + Inches(0.52)
    else:
        add_text(slide, x + Inches(0.22), ty, w - Inches(0.44), Inches(0.38),
                 [(title, 0, title_size, True, title_color)])
        ty = y + Inches(0.5)
    lines = [("• " + b, 0, body_size, False, DARK) for b in bullets]
    add_text(slide, x + Inches(0.22), ty, w - Inches(0.44), h - (ty - y) - Inches(0.12),
             lines, size=body_size, spacing=1.08)


def quote_bar(slide, text, color, y=6.62, x=0.7, w=11.93, size=15):
    x, y, w = Inches(x), Inches(y), Inches(w)
    add_rect(slide, x, y, w, Inches(0.64), fill=color)
    add_text(slide, x + Inches(0.35), y + Inches(0.06), w - Inches(0.7), Inches(0.52),
             [(text, 0, size, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE, spacing=1.0)


def section_slide(prs, tag, title, desc, color):
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.52), SW, Inches(0.07), fill=color)
    add_text(s, Inches(1.0), Inches(1.35), Inches(9.0), Inches(0.9),
             [(tag, 0, 30, True, color)])
    add_text(s, Inches(1.0), Inches(2.35), Inches(9.0), Inches(1.0),
             [(title, 0, 34, True, WHITE)])
    add_text(s, Inches(1.0), Inches(3.45), Inches(9.4), Inches(0.9),
             [(desc, 0, 16, False, LIGHT)])
    num = tag.split(" ")[0]
    add_text(s, Inches(10.2), Inches(1.0), Inches(2.8), Inches(2.8),
             [(num, 0, 130, True, color)])
    add_text(s, Inches(12.35), Inches(7.06), Inches(0.8), Inches(0.35),
             [(str(next_page()), 0, 12, False, MUTED)], align=PP_ALIGN.RIGHT)
    return s


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG 文件: {path}")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def add_diagram_slide(prs, title, subtitle, image_path, max_w=12.3, max_h=5.4):
    s = new_slide(prs)
    add_header(s, title, subtitle, next_page())
    w_px, h_px = png_size(image_path)
    aspect = w_px / h_px
    w_in = max_w
    h_in = w_in / aspect
    if h_in > max_h:
        h_in = max_h
        w_in = h_in * aspect
    x = (SW - Inches(w_in)) / 2
    y = Inches(1.62) + (Inches(max_h) - Inches(h_in)) / 2
    s.shapes.add_picture(image_path, x, y, width=Inches(w_in), height=Inches(h_in))
    add_text(s, Inches(0.7), Inches(7.02), Inches(12.0), Inches(0.35),
             [("素材：docs/diagrams/ppt/  ·  db-agent 六维深度探索", 0, 11, False, MUTED)],
             align=PP_ALIGN.CENTER)
    return s


def cover(prs):
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(5.0), SW, Inches(0.07), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(1.35), Inches(11.3), Inches(1.1),
             [("db-agent", 0, 64, True, WHITE)])
    add_text(s, Inches(1.0), Inches(2.6), Inches(11.3), Inches(0.8),
             [("Harness 六维深度探索", 0, 34, True, TEAL)])
    add_text(s, Inches(1.0), Inches(3.55), Inches(11.3), Inches(1.2),
             [("上下文管理 · 记忆管理 · 工具系统 · 系统编排 · 评估观测 · 约束与修复", 0, 18, False, LIGHT),
              ("自然语言 → SQLite / HBase / Hive，带权限、自愈与评测", 0, 14, False, MUTED)])
    add_text(s, Inches(1.0), Inches(5.35), Inches(11.3), Inches(1.1),
             [("定位：Agent = 模型 + Harness —— 本项目做的是后者", 0, 20, True, ORANGE),
              ("主线：确定性优先 —— 模板 > LLM · 正则 > LLM · 硬规则 > 语义理解", 0, 14, False, WHITE)])
    add_text(s, Inches(1.0), Inches(6.75), Inches(11.3), Inches(0.5),
             [("junior-to-senior/db-agent  ·  Python / LangGraph / Chroma / FastAPI / React", 0, 13, False, MUTED)])
    next_page()


def agenda(prs):
    s = new_slide(prs)
    add_header(s, "六维地图：从一条 Query 拆开 db-agent", "深度探索路线 —— 每维：解决什么 → 怎么做 → 关键数字 → 一句话", next_page())
    items = [
        ("01", "上下文管理", "让模型每次只看见该看见的：少塞、准塞、撑不住就压", BLUE),
        ("02", "记忆管理", "Working / Short / Long 三层 + 控制器防污染 + 成功 SQL 回流", TEAL),
        ("03", "工具系统", "@tool 注入注册，15 个原子工具，Multi 按 Agent 白名单挂载", ORANGE),
        ("04", "系统编排", "Single 是 ReAct Loop，Multi 是 LangGraph：Router → 专职 Agent → 闸门", GREEN),
        ("05", "评估观测", "观测回答『这次发生了什么』，评估回答『够不够好、回不回归』", PURPLE),
        ("06", "约束与修复", "出事之前六层护栏，出事之后三层自愈，熔断 / 幂等兜底", RED),
    ]
    x0, y0 = Inches(0.7), Inches(1.5)
    cw, ch = Inches(5.85), Inches(1.75)
    for i, (num, name, desc, color) in enumerate(items):
        col, row = i % 2, i // 2
        x = x0 + col * (cw + Inches(0.25))
        y = y0 + row * (ch + Inches(0.22))
        add_rect(s, x, y, cw, ch, fill=LIGHT)
        add_rect(s, x, y, Inches(0.14), ch, fill=color, shape=MSO_SHAPE.RECTANGLE)
        add_text(s, x + Inches(0.35), y + Inches(0.2), cw - Inches(0.6), Inches(0.8),
                 [(num + "  " + name, 0, 22, True, NAVY)])
        add_text(s, x + Inches(0.35), y + Inches(0.88), cw - Inches(0.6), Inches(0.8),
                 [(desc, 0, 12.5, False, DARK)])
    add_rect(s, Inches(0.7), Inches(6.62), Inches(11.93), Inches(0.6), fill=NAVY)
    add_text(s, Inches(1.0), Inches(6.72), Inches(11.4), Inches(0.4),
             [("主线：确定性优先 —— 能靠确定性（模板 / 正则 / 硬规则）搞定的，绝不先烧 token 或让模型猜", 0, 14, True, WHITE)])


def overview(prs):
    add_diagram_slide(prs, "工程机制全景：Harness 六维",
                      "入口 → 六维能力 → 可靠性横切（真实代码按此物理拆分：harness/ 下六个目录）",
                      IMG("mechanism-overview-ppt.png"))


def single_vs_multi(prs):
    s = new_slide(prs)
    add_header(s, "第 0 章 · 先看清全局：双模式架构", "同一套 Harness，两种编排：Single = ReAct Loop；Multi = LangGraph", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.45), Inches(5.85), Inches(4.05),
                    "Single（--mode single）· 一个大脑", [
                        "一个 ReAct Loop：while stop_reason == tool_use",
                        "15 个工具全挂，模型自己决定下一步",
                        "max_turns=10 · temperature=0（Tool 调用必须确定）",
                        "HybridWindow 在 loop 内按预算压缩上下文",
                        "HITL 退化为 run_query 返回『需要审批』标记",
                        "适用：探索性对话 / 简单查询 / 低延迟",
                    ], title_bar=BLUE)
    add_bullet_card(s, Inches(6.85), Inches(1.45), Inches(5.85), Inches(4.05),
                    "Multi（--mode multi）· LangGraph", [
                        "10 节点 · 6 专职 Agent（sql / strategy / hbase / hive / data_quality / analysis + router / clarify / confidence / reflection）",
                        "Router 出 plan（硬规则 > 缓存 > LLM），条件边决定走向",
                        "confidence_gate 0.7 · reflection ≤2 · replan ≤1",
                        "interrupt() + Checkpointer → 真暂停、可 resume",
                        "子 Agent 上下文隔离：只吃上游结果",
                        "适用：复杂分析 / 权限审批 / 可观测流水线",
                    ], title_bar=GREEN)
    add_text(s, Inches(0.7), Inches(5.68), Inches(11.93), Inches(0.8),
             [("共用入口（两种模式一样）：memory_controller（闲聊跳过 / 向量召回）→ template_matcher（可选模板捷径）→ 分叉 Single / Multi", 0, 13, False, MUTED)])
    quote_bar(s, "为什么拆？Single 的四个瓶颈：路由不准 · SQL 幻觉 · 敏感裸奔 · 结果没人信 —— 都指向同一个解：拆。", NAVY, y=6.12)


# ── 维度一 上下文管理 ────────────────────────────────────────────────
def ctx_a(prs):
    s = new_slide(prs)
    add_header(s, "01 · 上下文管理：少塞、准塞", "把『猜』换成『查』——只把相关的、真值的上下文放进 prompt", next_page())
    cards = [
        ("Schema Linking + 值级索引", BLUE, [
            "问题 → 相关列 embedding → top_k=15 紧凑 schema，不全库灌",
            "低基数列 distinct≤15：写入真实取值，WHERE region='华东' 不靠猜",
            "代码：harness/context/schema_discovery.py",
        ]),
        ("模板捷径", ORANGE, [
            "高频指标问法：关键词 + 槽位 → 毫秒级出 SQL",
            "MATCH_THRESHOLD=0.15，未命中才落 LLM（fail-open）",
            "零成本零幻觉：确定性路径能覆盖的绝不烧 token",
            "代码：harness/context/template_matcher.py",
        ]),
        ("检索式 few-shot", TEAL, [
            "Vanna 模式：相似 Q→SQL 样例注入 SQL Agent",
            "_MIN_SCORE=0.35：分数不够宁可不塞——错样例比没有更糟",
            "代码：harness/context/sql_examples.py",
        ]),
        ("Self-Query 两段式检索", PURPLE, [
            "语义相似 + 元数据过滤（memory_type / year）分开做",
            "先语义召回，再用结构化字段精确卡 → 防向量漂移",
            "代码：harness/context/self_query.py",
        ]),
    ]
    x0, y0 = Inches(0.7), Inches(1.5)
    cw, ch = Inches(5.85), Inches(2.28)
    for i, (title, color, bullets) in enumerate(cards):
        col, row = i % 2, i // 2
        x = x0 + col * (cw + Inches(0.25))
        y = y0 + row * (ch + Inches(0.2))
        add_bullet_card(s, x, y, cw, ch, title, bullets, title_bar=color, body_size=11.5)
    quote_bar(s, "一句话：schema 不只给列名，还给低基数列的真实取值 —— 模型写 WHERE 是照着真值写，不是猜枚举。", BLUE, y=6.42)


def ctx_b(prs):
    s = new_slide(prs)
    add_header(s, "01 · 上下文管理：撑不住就压 + 取舍", "Token 有预算、窗口有压缩、结果有截断 —— Lost in the middle 的两层解法", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.45), Inches(3.9), Inches(2.45),
                    "TokenBudget", [
                        "默认 50k · warn 水位 0.7",
                        "触发后进入压缩流程",
                        "代码：token_budget.py",
                    ], title_bar=BLUE, body_size=12)
    add_bullet_card(s, Inches(4.8), Inches(1.45), Inches(3.9), Inches(2.45),
                    "HybridWindow 四层压缩", [
                        "近 6 条原文保留",
                        "7–14 条成对摘要",
                        "更早 → 全局摘要",
                        "代码：hybrid_window_manager.py",
                    ], title_bar=TEAL, body_size=12)
    add_bullet_card(s, Inches(8.9), Inches(1.45), Inches(3.9), Inches(2.45),
                    "结果硬截断", [
                        "TOOL_RESULT_MAX_LEN≈8000",
                        "run_query 最多 50 行 + 统计摘要",
                        "太低会频繁补查，反而更贵",
                    ], title_bar=ORANGE, body_size=12)
    add_bullet_card(s, Inches(0.7), Inches(4.15), Inches(12.1), Inches(2.1),
                    "追问与取舍", [
                        "Q: Lost in the middle 怎么解？A: 结构上 Multi 拆 Agent 各自 prompt 短（SQL 只管查、Analysis 只管分析）；单 Agent 里靠预算水位触发 HybridWindow 压缩 + 重要约束放 system 前后两端。",
                        "Q: few-shot 为什么 0.35？A: 模型会模仿错误 SQL。fail-open：分数不够返回空串，让模型靠 schema 自己写。",
                        "Q: Multi 上下文怎么隔离？A: Router 只看 query+最近摘要；SQL 节点单独注入 few-shot；Analysis 拼上游 results+记忆+摘要+reflection 反馈；子 Agent 互不看见对方 tool 轨迹。",
                    ], title_bar=NAVY, body_size=12.5)
    quote_bar(s, "一句话：少塞（Schema Linking）→ 准塞（真值/模板/样例）→ 撑不住就压（预算 + 窗口 + 截断）。", BLUE, y=6.5)


# ── 维度二 记忆管理 ─────────────────────────────────────────────────
def mem_a(prs):
    s = new_slide(prs)
    add_header(s, "02 · 记忆管理：三层记忆 + 防污染控制器", "不同时间尺度用不同策略 —— 不要用一层解决所有", next_page())
    cards = [
        ("Working · 工作记忆", BLUE, [
            "当前轮 tool 中间结果 / graph state.results",
            "解决：单轮 tool 链要带着中间结果往下走",
        ]),
        ("Short-term · 短期", TEAL, [
            "ConversationManager：滑动窗口 max_recent=10 + 溢出 LLM 摘要",
            "解决：同会话指代（『刚才那个部门』）",
            "代码：harness/memory/short_term_memory.py",
        ]),
        ("Long-term · 长期", ORANGE, [
            "VectorMemory：ChromaDB / Milvus 双后端，recall top_k=5",
            "升级：BM25 + 向量 RRF 融合 + HyDE + LLM Rerank（long_term_memory.py）",
            "解决：跨会话偏好与历史问答",
        ]),
        ("memory_controller · 控制器", RED, [
            "is_chitchat：闲聊跳过召回（写了会污染向量库）",
            "is_meta_question：元问题走 list_recent 时间倒序，不语义检索",
            "should_remember：该不该写入的一道闸",
            "代码：harness/memory/memory_controller.py",
        ]),
    ]
    x0, y0 = Inches(0.7), Inches(1.5)
    cw, ch = Inches(5.85), Inches(2.3)
    for i, (title, color, bullets) in enumerate(cards):
        col, row = i % 2, i // 2
        x = x0 + col * (cw + Inches(0.25))
        y = y0 + row * (ch + Inches(0.2))
        add_bullet_card(s, x, y, cw, ch, title, bullets, title_bar=color, body_size=11.5)
    quote_bar(s, "一句话：三层各管一件事 —— Working 管『这一轮往下传』，Short 管指代，Long 管跨会话；防污染比多记更重要。", TEAL, y=6.42)


def mem_b(prs):
    s = new_slide(prs)
    add_header(s, "02 · 记忆管理：自学习闭环（越用越准）", "答对的 SQL 自动回流成 few-shot 样例 —— 但错误样例比没样例更毒", next_page())
    steps = [
        ("run_query 成功", "SELECT 真实执行通过"),
        ("质量门 should_learn", "只收 SELECT/WITH · 敏感列拦截 · 真实库 dry-run · 空结果拒绝"),
        ("写 Chroma sql_examples", "带 question↔sql 配对"),
        ("下次 get_sql_fewshot", "相似问题自动命中"),
        ("LRU 淘汰", "last_hit_at 最旧先删；seed 保留"),
    ]
    x = Inches(0.55)
    for i, (t, d) in enumerate(steps):
        w = Inches(2.28)
        box = add_rect(s, x, Inches(1.6), w, Inches(1.02), fill=TEAL if i % 2 == 0 else NAVY)
        fill_shape_text(box, t, 12.5, WHITE)
        add_text(s, x + Inches(0.05), Inches(2.7), w - Inches(0.1), Inches(1.0),
                 [(d, 0, 9.5, False, MUTED)], align=PP_ALIGN.CENTER)
        if i < len(steps) - 1:
            add_arrow(s, x + w + Inches(0.03), Inches(1.96), Inches(0.13), Inches(0.28))
        x += w + Inches(0.19)
    add_text(s, Inches(0.7), Inches(3.85), Inches(12.0), Inches(0.35),
             [("代码：harness/memory/feedback.py —— learn_from_success / learn_from_hitl；AUTO_LEARN_SQL=0 一键关回流", 0, 12.5, False, MUTED)])
    add_bullet_card(s, Inches(0.7), Inches(4.3), Inches(12.1), Inches(2.0),
                    "关键设计（面试高频）", [
                        "HITL 批准与用户点赞同源回流，带不同 source 标记；评测学习阶段在事实断言后 force 写入（质量门仍生效）",
                        "防膨胀：created_at / last_hit_at / hit_count 埋点 + LRU 删最久没用；seed 是回滚的锚点，永不删",
                        "并发 bug 故事：早期用全局变量存『最后成功 SQL』→ asyncio 单线程交错，A 的请求拿到 B 的 SQL；用 ContextVar 让每请求一个抽屉修复（代价：run_query 不能丢线程池）",
                    ], title_bar=NAVY, body_size=12.5)
    quote_bar(s, "一句话：答对的 SQL 回流成 few-shot，让系统越用越准；前置是质量门 —— 错误样例比没样例更毒。", TEAL, y=6.5)


# ── 维度三 工具系统 ─────────────────────────────────────────────────
def tool_a(prs):
    s = new_slide(prs)
    add_header(s, "03 · 工具系统：@tool 注册 + 15 个原子工具", "加能力 = 加一个函数 + 注册一行；Loop / Graph 不用改", next_page())
    add_rect(s, Inches(0.7), Inches(1.42), Inches(12.1), Inches(1.08), fill=LIGHT)
    add_para_runs(s, Inches(0.95), Inches(1.52), Inches(11.7), Inches(0.9),
                  [("@tool 注册机制  ", NAVY, True, 15),
                   ("@tool 装饰器读函数签名（类型注解 + docstring）→ 自动生成 JSON Schema，注册进 TOOL_HANDLERS（当前 15 个）；兼容旧手写 schema（tool_from_dict）。", DARK, False, 13),
                   ("  加能力 = 加函数 + 注册一行，编排层零改动。", NAVY, True, 13)])
    rows = [
        ("Schema", "list_tables · describe_table · get_schema_summary · discover_relevant_schema", "只读元数据", BLUE),
        ("查询", "run_query（SELECT 白名单 / RBAC / 敏感列 HITL）· match_sql_template", "只查不写", GREEN),
        ("分析", "analyze_results · compare_periods · render_chart", "不碰库", TEAL),
        ("知识 / 指标", "search_knowledge_base · lookup_metric", "文档 + 口径", PURPLE),
        ("记忆", "save_to_memory · read_memory · search_memory", "经 Self-Query", ORANGE),
        ("大数据", "generate_hbase_query · run_hbase · search_hive_syntax", "HBase 写操作 HITL", RED),
    ]
    y = Inches(2.72)
    for name, tools, note, color in rows:
        add_rect(s, Inches(0.7), y, Inches(1.9), Inches(0.5), fill=color)
        fill_shape_text(s.shapes[-1], name, 12.5, WHITE)
        add_rect(s, Inches(2.65), y, Inches(8.15), Inches(0.5), fill=LIGHT)
        add_text(s, Inches(2.85), y + Inches(0.05), Inches(7.85), Inches(0.4),
                 [(tools, 0, 11.5, False, DARK)], anchor=MSO_ANCHOR.MIDDLE)
        add_rect(s, Inches(10.85), y, Inches(1.95), Inches(0.5), fill=LIGHT)
        add_text(s, Inches(10.95), y + Inches(0.05), Inches(1.8), Inches(0.4),
                 [(note, 0, 10.5, True, NAVY)], anchor=MSO_ANCHOR.MIDDLE)
        y += Inches(0.58)
    quote_bar(s, "一句话：工具 schema 从类型注解自动生成、不手写 JSON —— 手写会漂；原子才可组合。", ORANGE, y=6.35)


def tool_b(prs):
    s = new_slide(prs)
    add_header(s, "03 · 工具系统：Agent × Tool 白名单", "全挂会串扰 —— 拆 tool 集合是硬约束，prompt 约束是软约束", next_page())
    add_text(s, Inches(0.7), Inches(1.42), Inches(6.0), Inches(0.4),
             [("Multi 内每个 Agent 只挂自己职责的工具", 0, 15, True, NAVY)])
    rows = [
        ("SQL Agent", "discover + list/describe + run_query", "只查不分析", BLUE),
        ("Analysis", "analyze / compare / render_chart", "不写 SQL", GREEN),
        ("Strategy", "search_knowledge_base + lookup_metric", "制度与口径", PURPLE),
        ("HBase", "generate_hbase_query + run_hbase", "KV 查询", ORANGE),
        ("Hive", "hive 表工具 + run_query + syntax", "数仓方言", TEAL),
        ("DataQuality", "list/describe + run_query", "质量扫描", RED),
    ]
    y = Inches(1.92)
    for name, tools, note, color in rows:
        add_rect(s, Inches(0.7), y, Inches(1.7), Inches(0.52), fill=color)
        fill_shape_text(s.shapes[-1], name, 12, WHITE)
        add_rect(s, Inches(2.45), y, Inches(4.4), Inches(0.52), fill=LIGHT)
        add_text(s, Inches(2.6), y + Inches(0.06), Inches(4.15), Inches(0.4),
                 [(tools, 0, 10.5, False, DARK)], anchor=MSO_ANCHOR.MIDDLE)
        y += Inches(0.6)
    add_bullet_card(s, Inches(7.15), Inches(1.42), Inches(5.6), Inches(4.35),
                    "白名单为什么是硬约束", [
                        "全挂会串扰：Analysis 开始 invent SQL，SQL Agent 开始写业务建议",
                        "拆集合 = 硬约束（代码层，绕不过）；prompt 约束 = 软约束",
                        "两层一起才稳 —— 『硬约束 > 软提示词』在工具层的实例",
                        "Single 挂全集（15 tools）—— 探索自由组合；Multi 按职责隔离 —— 生产可控",
                        "代码：harness/orchestration/multi/agents.py（ConfiguredAgent 打包 prompt + tools + handlers）",
                    ], title_bar=NAVY, body_size=12.5)
    quote_bar(s, "一句话：全挂会串扰；拆 tool 集合是硬约束、prompt 是软约束，两层一起才稳。", ORANGE)


# ── 维度四 系统编排 ─────────────────────────────────────────────────
def orch_a(prs):
    s = new_slide(prs)
    add_header(s, "04 · 系统编排：LangGraph 图 + 确定性 Router", "Multi 节点真实名字：router → clarify / 专职 Agent → confidence_gate → analysis → reflection → END", next_page())
    add_rect(s, Inches(0.7), Inches(1.42), Inches(12.1), Inches(1.5), fill=LIGHT)
    add_para_runs(s, Inches(0.95), Inches(1.5), Inches(11.65), Inches(0.85),
                  [("router", GREEN, True, 15), ("  →  ", MUTED, False, 15),
                   ("clarify", NAVY, True, 15), ("  ·  ", MUTED, False, 15),
                   ("data_quality", NAVY, True, 15), ("  ·  ", MUTED, False, 15),
                   ("sql", NAVY, True, 15), ("  ·  ", MUTED, False, 15),
                   ("strategy", NAVY, True, 15), ("  ·  ", MUTED, False, 15),
                   ("hbase", NAVY, True, 15), ("  ·  ", MUTED, False, 15),
                   ("hive", NAVY, True, 15),
                   ("   →  confidence_gate", GREEN, True, 15), ("（≥0.7？）", DARK, False, 13),
                   ("   →  analysis", NAVY, True, 15),
                   ("   →  reflection", GREEN, True, 15), ("（≤2）", DARK, False, 13),
                   ("   →  END", NAVY, True, 15)], spacing=1.25)
    add_text(s, Inches(0.95), Inches(2.42), Inches(11.7), Inches(0.45),
             [("失败路径：sql Agent 超时 → _maybe_replan（≤1 次）带反馈回 router，删除失败 results 防空转", 0, 12.5, False, RED)])
    add_bullet_card(s, Inches(0.7), Inches(3.0), Inches(6.0), Inches(2.35),
                    "Router：先确定，后花钱", [
                        "优先级：硬规则（正则）> 上下文继承 > LRU 缓存（max 100）> LLM",
                        "闲聊 / 元问题 / 带 HBase·Hive 关键词 → 正则 0ms 零幻觉",
                        "同 query 缓存命中不再二次分类；精确匹配『安全第一』",
                        "代码：harness/orchestration/multi/router.py · cache.py",
                    ], title_bar=GREEN, body_size=12.5)
    add_bullet_card(s, Inches(6.95), Inches(3.0), Inches(5.85), Inches(2.35),
                    "图上三个闸：把坏结果挡在用户前", [
                        "clarify：Router confidence=low → interrupt 问 2–3 个澄清问题",
                        "confidence_gate：SQL 六项标准自评，<0.7 暂停人工确认",
                        "reflection：完整性 / 真实性不通过退回 Analysis 重写，≤2 次",
                        "代码：harness/constraints/confidence.py · nodes.py",
                    ], title_bar=NAVY, body_size=12.5)
    add_text(s, Inches(0.7), Inches(5.55), Inches(12.0), Inches(0.45),
             [("Single 对照：streaming_agent max_turns=10 · temperature=0；子 Agent（ConfiguredAgent.run）max_turns=8", 0, 13, False, MUTED)])
    quote_bar(s, "一句话：Reflection 改答案便宜允许 2 次；Replan 整图重走贵、易抖振只允许 1 次 —— 给失败设上限，别死循环烧钱。", GREEN, y=6.08)


def orch_b(prs):
    s = new_slide(prs)
    add_header(s, "04 · 系统编排：HITL / Checkpointer / Task board", "人工审批真暂停、状态落盘真恢复、执行清单可见", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(4.3),
                    "HITL：interrupt() + resume", [
                        "敏感列 SQL（salary/cost/budget）、HBase put/delete、clarify、confidence_gate 低分 → 全部暂停",
                        "LangGraph interrupt() 暂停，Checkpointer 写 state；前端 / CLI /resume 用 Command(resume=…) 续跑",
                        "图外直调 tool 拦不住 interrupt → catch 成『需要审批』错误串",
                        "两边行为一致：绝不能静默执行",
                        "代码：harness/orchestration/multi/nodes.py",
                    ], title_bar=RED, body_size=12.5)
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.85), Inches(2.0),
                    "Checkpointer（机器恢复）", [
                        "db/agent_state.db；可配 Redis + TTL 自动清理",
                        "跨轮 graph state 落盘 → 重启 / 断点可续",
                        "运行时语义",
                    ], title_bar=TEAL, body_size=12)
    add_bullet_card(s, Inches(6.95), Inches(3.65), Inches(5.85), Inches(2.15),
                    "Task board（给人看）", [
                        "plan 落盘 .tasks/*.task（当前 1000+ 份）",
                        "每步 ✓ / ✗ 状态可见，Agent 完成后 claim 下一步",
                        "产品语义：执行清单",
                        "代码：harness/orchestration/multi/task_system.py",
                    ], title_bar=ORANGE, body_size=12)
    quote_bar(s, "一句话：任务清单给人看，checkpoint 给机器恢复 —— 一个产品语义，一个运行时语义。", GREEN)


# ── 维度五 评估观测 ─────────────────────────────────────────────────
def obs_a(prs):
    s = new_slide(prs)
    add_header(s, "05 · 评估观测：观测 —— 这次发生了什么", "本地 Trace + 平台 Opik + 成本 / 指标 / 告警，多路留痕", next_page())
    cards = [
        ("Trace JSONL", BLUE, [
            "logs/traces/YYYY-MM-DD.jsonl，按行追加好 grep",
            "Span：节点耗时 / token / turns / error",
            "SQL 参数自动脱敏（mask_sql：VALUES / password / phone / id）",
            "保留天数自动清理",
        ]),
        ("Opik 平台", PURPLE, [
            "wrap_langgraph / LLM span / Feedback 打分",
            "Dataset + Experiment 实验对比",
            "OPIK_ENABLED 开关；平台不可用自动降级",
        ]),
        ("成本 & 缓存效率", ORANGE, [
            "cost.py：按模型 token → 人民币 / 美元估算",
            "RouterCache hit_rate 可视化命中率",
        ]),
        ("Ops & 告警", RED, [
            "ops_metrics：Prometheus 文本指标 + 进程内存",
            "alerts：熔断 / Agent 超时告警，可接 Slack / 钉钉",
            "pipeline_monitor：管道健康看板",
        ]),
    ]
    x0, y0 = Inches(0.7), Inches(1.5)
    cw, ch = Inches(5.85), Inches(2.2)
    for i, (title, color, bullets) in enumerate(cards):
        col, row = i % 2, i // 2
        x = x0 + col * (cw + Inches(0.25))
        y = y0 + row * (ch + Inches(0.2))
        add_bullet_card(s, x, y, cw, ch, title, bullets, title_bar=color, body_size=11.5)
    add_text(s, Inches(0.7), Inches(6.15), Inches(12.0), Inches(0.4),
             [("代码：harness/observation/ —— tracer.py · opik_tracing.py · cost.py · ops_metrics.py · alerts.py · pipeline_monitor/", 0, 12.5, False, MUTED)])
    quote_bar(s, "一句话：观测在线回答『刚才发生了什么』—— 每步耗时多少、烧了多少 token、错在哪、SQL 留痕可审计。", PURPLE, y=6.6)


def obs_b(prs):
    s = new_slide(prs)
    add_header(s, "05 · 评估观测：评估 —— 够不够好、回不回归", "测试门禁 + Eval 三档 + 跨模型 Judge + Regression 防回退", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(2.4),
                    "pytest 门禁：435 条全离线", [
                        "smoke（零 API）+ 单元 + 集成三层",
                        "LLM / Embedding 用脚本化 fake，本地直接全绿",
                        "护栏三层测试（注入 / SQL / 泄露）接入 CI",
                    ], title_bar=BLUE, body_size=12)
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.85), Inches(2.4),
                    "Eval：47 条用例 · 三档", [
                        "fast：护栏零成本；full：调 LLM 全量",
                        "LLM-as-Judge：Kimi 评 DeepSeek —— 跨模型防自评偏袒",
                        "动态 ground-truth：expected_sql 实查，防 seed 日期推进",
                    ], title_bar=PURPLE, body_size=12)
    add_bullet_card(s, Inches(0.7), Inches(4.1), Inches(6.0), Inches(1.85),
                    "Regression：防回退", [
                        "跑分与上次 baseline 对比，退步告警",
                        "每次改代码跑一遍，证明『这次改进没学坏』",
                        "代码：harness/observation/regression.py · tests/eval_runner.py",
                    ], title_bar=ORANGE, body_size=12)
    add_bullet_card(s, Inches(6.95), Inches(4.1), Inches(5.85), Inches(1.85),
                    "观测 vs 评估", [
                        "观测：在线，回答『这次发生了什么』",
                        "评估：离线门禁，回答『整体够不够好、回不回归』",
                        "Judge 换一家模型：同一家会偏袒自己的文风与错误",
                    ], title_bar=NAVY, body_size=12)
    quote_bar(s, "一句话：我用分数说话，不用感觉说话 —— 观测回答发生了什么，评估回答够不够好、退没退步。", PURPLE, y=6.2)
    add_text(s, Inches(0.7), Inches(6.68), Inches(12.0), Inches(0.5),
             [("代码：tests/eval_cases.py · eval_runner.py · eval_confidence.py · eval_improve.py · eval_selflearn.py · eval_rag_retrieval.py", 0, 12, False, MUTED)])


# ── 维度六 约束与修复 ───────────────────────────────────────────────
def cons_a(prs):
    s = new_slide(prs)
    add_header(s, "06 · 约束与修复：出事之前 —— 六层纵深护栏", "攻击面不同，防线就得分开 —— 一层被绕，其它层还在", next_page())
    cards = [
        ("① 输入护栏 guard_input", BLUE, [
            "注入 / 超长 / 空 检测（INJECTION_PATTERNS 正则）",
            "打在最前面，非数据意图短路",
        ]),
        ("② 意图路由", GREEN, [
            "非数据问题直接短路，不烧 token、不碰库",
            "与 Router 联动",
        ]),
        ("③ Prompt 软约束", TEAL, [
            "SELECT only、Agent 职责边界写进 system prompt",
            "软约束：兜底提示，不替代硬拦截",
        ]),
        ("④ RBAC entitlement", ORANGE, [
            "5 角色：dba / manager / analyst / viewer / support",
            "工具 / 表 / 行级（rewrite_sql 注入 dept_id）/ 文档过滤",
            "敏感列：salary · cost · budget",
            "权限存 DB：改行即生效；代码留默认角色 fallback",
        ]),
        ("⑤ HITL 人工审批", RED, [
            "敏感列查询 + HBase 破坏性写 → 必须人工确认",
            "绝不静默执行（见 04 章）",
        ]),
        ("⑥ 输出护栏 guard_output", PURPLE, [
            "提示词泄露检测 + PII 告警（手机 / 身份证 / 邮箱 / 银行卡）",
        ]),
    ]
    x0, y0 = Inches(0.7), Inches(1.5)
    cw, ch = Inches(3.9), Inches(2.2)
    for i, (title, color, bullets) in enumerate(cards):
        col, row = i % 3, i // 3
        x = x0 + col * (cw + Inches(0.15))
        y = y0 + row * (ch + Inches(0.2))
        add_bullet_card(s, x, y, cw, ch, title, bullets, title_bar=color, body_size=11)
    quote_bar(s, "一句话：注入打输入层、越权打 RBAC、误写打 SQL 护栏、高风险打 HITL、泄露打输出层 —— 纵深防御。", RED, y=6.42)


def cons_b(prs):
    s = new_slide(prs)
    add_header(s, "06 · 约束与修复：出事之后 —— 三层自愈", "可恢复 ≠ 假装成功：每层有上限，到顶就暴露 / 重规划", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(2.1),
                    "L1 · API 重试（retry.py）", [
                        "只重试可恢复错误：429 / 500 / 502 / 503 / 529",
                        "指数退避；LLM_MAX_RETRIES 默认 3（可环境变量调）",
                        "测试可关：LLM_MAX_RETRIES=0",
                    ], title_bar=BLUE, body_size=12)
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.85), Inches(2.1),
                    "L2 · SQL 自愈", [
                        "执行报错 → 结构化返回 retryable + hint",
                        "Agent 按 hint 重写（prompt 约定 ≤2 次）",
                        "trace 记录重试次数，可审计",
                    ], title_bar=TEAL, body_size=12)
    add_bullet_card(s, Inches(0.7), Inches(3.8), Inches(6.0), Inches(2.1),
                    "L3 · 编排重规划（≤1）", [
                        "Agent 超时（超过 max_turns）→ _maybe_replan 带反馈回 Router",
                        "删除失败 results 防空转；提示『拆小任务 / 改派 Agent / 别原样重复』",
                        "代码：harness/orchestration/multi/helpers.py",
                    ], title_bar=ORANGE, body_size=12)
    add_bullet_card(s, Inches(6.95), Inches(3.8), Inches(5.85), Inches(2.1),
                    "横切兜底：熔断 + 幂等", [
                        "CircuitBreaker：连续失败达阈值 → 开闸拒请求 → 告警 → 半开探测恢复",
                        "Idempotency：写工具（save_to_memory / run_hbase）同参数 TTL 窗口内不重复执行",
                        "重试不会造成副作用；Reflection / confidence_gate / clarify 挡在用户前",
                    ], title_bar=RED, body_size=12)
    quote_bar(s, "一句话：每层有上限、重试次数留痕，到顶仍失败就暴露给用户 —— Harness 要可恢复，不是假装成功。", RED, y=6.12)
    add_text(s, Inches(0.7), Inches(6.66), Inches(12.0), Inches(0.5),
             [("代码：harness/constraints/ —— retry.py · circuit_breaker.py · idempotency.py · guardrails.py · entitlement.py", 0, 12, False, MUTED)])


# ── 收尾页 ──────────────────────────────────────────────────────────
def numbers(prs):
    s = new_slide(prs)
    add_header(s, "数字速记：一张表背下 db-agent", "面试默写版 —— 先数字后解释，全部来自真实代码", next_page())
    items = [
        ("6", "专职 Agent（Multi）"),
        ("15", "原子工具（TOOL_HANDLERS）"),
        ("5", "RBAC 角色"),
        ("10 / 8", "Single / 子 Agent max_turns"),
        ("3", "护栏 · 记忆 · 自愈 层数"),
        ("0.7", "confidence 阈值 & TokenBudget 水位"),
        ("2", "Reflection 重写上限"),
        ("1", "Replan 重规划上限"),
        ("0.35", "few-shot 最低命中分"),
        ("0.15", "模板匹配阈值"),
        ("50", "run_query 返回最大行数"),
        ("100", "Router LRU 缓存容量"),
        ("50k", "TokenBudget 默认预算"),
        ("47", "Eval 用例数"),
        ("435", "pytest 测试数（全离线）"),
    ]
    x0, y0 = Inches(0.7), Inches(1.5)
    cw, ch = Inches(2.36), Inches(1.05)
    for i, (num, label) in enumerate(items):
        col, row = i % 5, i // 5
        x = x0 + col * (cw + Inches(0.12))
        y = y0 + row * (ch + Inches(0.14))
        add_rect(s, x, y, cw, ch, fill=LIGHT)
        add_text(s, x + Inches(0.12), y + Inches(0.08), cw - Inches(0.24), Inches(0.5),
                 [(num, 0, 20, True, BLUE)])
        add_text(s, x + Inches(0.12), y + Inches(0.55), cw - Inches(0.24), Inches(0.45),
                 [(label, 0, 10, False, DARK)])
    quote_bar(s, "原则就一句：确定性能做的，绝不交给 LLM。", NAVY, y=6.55)


def story(prs):
    s = new_slide(prs)
    add_header(s, "90 秒串讲：把六维串成一条线", "db-agent = 数据库场景的 Harness，不是训练模型", next_page())
    rows = [
        ("上下文", "Schema Linking / 模板 / few-shot / 压缩 → 模型每次只看见该看见的", BLUE),
        ("记忆", "Working / Short / Long 三层 + controller 防污染 + 成功 SQL 回流样例库", TEAL),
        ("工具", "@tool 注入注册，15 个原子工具，Multi 按 Agent 白名单挂载", ORANGE),
        ("编排", "Single 是 ReAct Loop；Multi 是 LangGraph：Router → 专职 Agent → confidence → Analysis → Reflection，Checkpointer 支持 HITL resume", GREEN),
        ("评估观测", "JSONL Trace + Opik + pytest 435 + Eval 47 + 跨模型 Judge（Kimi 评 DeepSeek）", PURPLE),
        ("约束修复", "RBAC + 六层护栏 + HITL；失败走 API 重试 → SQL 重写 → 有限次重规划", RED),
    ]
    y = Inches(1.5)
    for name, desc, color in rows:
        add_rect(s, Inches(0.7), y, Inches(1.85), Inches(0.72), fill=color)
        fill_shape_text(s.shapes[-1], name, 16, WHITE)
        add_rect(s, Inches(2.6), y, Inches(10.0), Inches(0.72), fill=LIGHT)
        add_text(s, Inches(2.85), y + Inches(0.06), Inches(9.6), Inches(0.6),
                 [(desc, 0, 12.5, False, DARK)], anchor=MSO_ANCHOR.MIDDLE)
        y += Inches(0.84)
    quote_bar(s, "主线：确定性优先 —— 模板 > LLM · 正则 > LLM · 硬规则 > 语义理解；Harness 用确定性约束包住不可靠的 LLM。", NAVY, y=6.62)


def boundary(prs):
    s = new_slide(prs)
    add_header(s, "诚实边界：我知道系统停在哪", "演示 / 面试口径 —— README 已标注，主动说反而加分", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(4.6),
                    "当前形态（诚实口径）", [
                        "本地演示系统，不是已上线业务环境",
                        "SQLite 是真实本地库；HBase / Hive 是内存模拟器（API 对齐，接真实集群只换连接器）",
                        "Web 默认无鉴权：支持可选 WEB_API_TOKEN；会话默认内存，配 REDIS_URL 存 Redis",
                        "向量库 ChromaDB / Milvus 双后端；Redis / Milvus 均可选后端 + 自动降级",
                        "行级隔离是应用层 rewrite_sql（字符串改写），不是数据库 RLS",
                    ], title_bar=NAVY, body_size=12.5)
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.85), Inches(4.6),
                    "自述 gap 与补法（不藏着）", [
                        "密钥在 .env 明文 → 生产接 secrets manager",
                        "purge 等运维接口面向演示 → 补授权校验",
                        "语义层断连机制 → 已有降级路径继续加固",
                        "learned 样例缺少归因（来自哪次问答）→ 加 provenance",
                        "质量门以事实断言为主 → semantic_verify 已引入，语义级覆盖继续补",
                        "每条 gap 都带补的方向 —— 主动暴露的洞是准备好的战场",
                    ], title_bar=ORANGE, body_size=12.5)
    quote_bar(s, "能说清『行级隔离是字符串改写、不是 RLS』的人，比吹『我做了完整安全』的人懂十倍。", RED, y=6.4)


def closing(prs):
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_text(s, Inches(1.0), Inches(2.4), Inches(11.3), Inches(1.2),
             [("谢谢 · Q&A", 0, 48, True, WHITE)])
    add_text(s, Inches(1.0), Inches(3.7), Inches(11.3), Inches(1.2),
             [("Agent = 模型 + Harness", 0, 22, True, ORANGE),
              ("上下文 · 记忆 · 工具 · 编排 · 观测评估 · 约束修复 —— 确定性优先", 0, 15, False, LIGHT)])
    add_text(s, Inches(1.0), Inches(6.6), Inches(11.3), Inches(0.5),
             [("db-agent 六维深度探索  ·  junior-to-senior/db-agent", 0, 13, False, MUTED)])
    next_page()


def build():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH

    cover(prs)
    agenda(prs)
    overview(prs)
    single_vs_multi(prs)

    section_slide(prs, "01 · CONTEXT", "上下文管理", "让模型每次只看见该看见的：少塞、准塞、撑不住就压", BLUE)
    ctx_a(prs)
    ctx_b(prs)

    section_slide(prs, "02 · MEMORY", "记忆管理", "Working / Short / Long 三层 + 控制器防污染 + 成功 SQL 回流样例库", TEAL)
    mem_a(prs)
    mem_b(prs)

    section_slide(prs, "03 · TOOLS", "工具系统", "@tool 注入注册 + 原子可组合 + Multi 按 Agent 白名单挂载", ORANGE)
    tool_a(prs)
    tool_b(prs)

    section_slide(prs, "04 · ORCHESTRATION", "系统编排", "Single 是 ReAct Loop，Multi 是 LangGraph；失败有上限，别死循环烧钱", GREEN)
    orch_a(prs)
    orch_b(prs)
    add_diagram_slide(prs, "Query 完整链路", "一条查询从进来到返回：入口 → 护栏 → Router → 多 Agent → 质检 → 返回",
                      IMG("query-flow-ppt.png"))

    section_slide(prs, "05 · EVAL & OBS", "评估观测", "观测回答『这次发生了什么』；评估回答『够不够好、回不回归』", PURPLE)
    obs_a(prs)
    obs_b(prs)

    section_slide(prs, "06 · CONSTRAINTS", "约束与修复", "出事之前六层护栏，出事之后三层自愈；熔断 / 幂等兜底", RED)
    cons_a(prs)
    add_diagram_slide(prs, "安全与权限链路", "输入 → 意图路由 → RBAC → SQL 护栏 → HITL → 输出护栏",
                      IMG("security-chain-ppt.png"))
    cons_b(prs)
    add_diagram_slide(prs, "三层自愈", "重试 → 执行自愈 → 熔断降级（每层有上限，可恢复 ≠ 假装成功）",
                      IMG("self-healing-ppt.png"))

    numbers(prs)
    story(prs)
    boundary(prs)
    closing(prs)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    prs.save(OUT)
    print(f"OK → {OUT}  (共 {len(prs.slides._sldIdLst)} 页)")


if __name__ == "__main__":
    build()
