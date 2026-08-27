"""生成 db-agent 工作经历项目讲解 PPT。

用法:
    python scripts/generate_project_deck.py

输出:
    docs/db-agent-项目讲解.pptx

依赖:
    python-pptx（本地离线生成，不需要 2slides API）
"""

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ── 配色（最初版） ───────────────────────────────────────────────────
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


def add_text(slide, x, y, w, h, lines, size=14, color=DARK, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.15):
    """lines: [(text, level, size, bold, color)] 或纯字符串列表。"""
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


def add_wordmark(slide, x, y, color=NAVY, size=28, align=PP_ALIGN.LEFT):
    """Citi 风格 "citi" 字标（斜体粗体）。"""
    box = slide.shapes.add_textbox(x, y, Inches(2.4), Inches(0.65))
    tf = box.text_frame
    tf.word_wrap = False
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    _set_run(run, "citi", size, color, True)
    run.font.italic = True
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


def fill_shape_text(sp, text, size=13, color=WHITE, bold=True,
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
    add_text(slide, Inches(0.55), Inches(0.35), Inches(11.0), Inches(0.65),
             [(title, 0, 30, True, NAVY)])
    if subtitle:
        add_text(slide, Inches(0.57), Inches(1.02), Inches(11.0), Inches(0.4),
                 [(subtitle, 0, 14, False, MUTED)])
    if page is not None:
        add_text(slide, Inches(12.3), Inches(7.05), Inches(0.8), Inches(0.35),
                 [(str(page), 0, 12, False, MUTED)], align=PP_ALIGN.RIGHT)


def add_bullet_card(slide, x, y, w, h, title, bullets, fill=LIGHT, title_color=NAVY,
                    title_size=16, body_size=13):
    add_rect(slide, x, y, w, h, fill=fill)
    add_text(slide, x + Inches(0.25), y + Inches(0.18), w - Inches(0.5), Inches(0.4),
             [(title, 0, title_size, True, title_color)])
    lines = [(("• " + b), 0, body_size, False, DARK) for b in bullets]
    add_text(slide, x + Inches(0.25), y + Inches(0.62), w - Inches(0.5), h - Inches(0.8),
             lines, size=body_size, spacing=1.12)


def png_size(path):
    """读取 PNG 宽高（IHDR），不依赖 PIL。返回 (width, height)。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG 文件: {path}")
    import struct
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def add_diagram_slide(prs, title, subtitle, image_path, max_w=12.2, max_h=5.6):
    """新增一页全幅图解：标题 + 居中缩放图片。"""
    s = new_slide(prs)
    add_header(s, title, subtitle)
    w_px, h_px = png_size(image_path)
    aspect = w_px / h_px
    w_in = max_w
    h_in = w_in / aspect
    if h_in > max_h:
        h_in = max_h
        w_in = h_in * aspect
    x = (SW - Inches(w_in)) / 2
    y = Inches(1.7) + (Inches(5.0) - Inches(h_in)) / 2
    s.shapes.add_picture(image_path, x, y, width=Inches(w_in), height=Inches(h_in))
    add_text(s, Inches(0.7), Inches(6.9), Inches(12.0), Inches(0.4),
             [("图片来源：docs/engineering-mechanisms.md（本对话生成）", 0, 11, False, MUTED)],
             align=PP_ALIGN.CENTER)


def build():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH

    # ── 1 封面 ────────────────────────────────────────────────────────
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.75), SW, Inches(0.06), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(1.6), Inches(11.3), Inches(1.2),
             [("db-agent", 0, 60, True, WHITE)])
    add_text(s, Inches(1.0), Inches(2.75), Inches(11.3), Inches(0.7),
             [("企业级自然语言数据库分析 Agent · 工作经历项目讲解", 0, 26, True, TEAL)])
    add_text(s, Inches(1.0), Inches(3.6), Inches(11.3), Inches(1.0),
             [("自然语言 → SQL / Hive / HBase / 指标口径 / 趋势分析", 0, 18, False, LIGHT),
              ("独立开发 · Python / LangGraph / ChromaDB / FastAPI / React", 0, 15, False, LIGHT)])
    add_text(s, Inches(1.0), Inches(5.15), Inches(11.3), Inches(1.2),
             [("定位：Agent = 模型 + Harness", 0, 18, True, ORANGE),
              ("本项目做 Harness：工具、权限、记忆、编排、护栏、自愈、评测", 0, 14, False, WHITE)])
    add_text(s, Inches(1.0), Inches(6.7), Inches(11.3), Inches(0.5),
             [("2026 · 面试讲解版", 0, 13, False, MUTED)])

    # ── 2 工作经历 → 项目背景 ────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "工作经历 → 项目背景", "从平时数据平台工作，引出 db-agent 项目", 2)
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(4.6),
                    "平时工作 · 花旗 Olympus Core（3 年 Data Analyst）", [
                        "Kafka 实时流 + SFTP 批量双通道，负责全数据平台交付保障",
                        "覆盖 load / validation / dispatcher / monitor / recon 全链路",
                        "执行层 Spark + HDFS，调度层 Autosys",
                        "DGB / BKB / Opportunity / DDA-checking 四条业务线双通道稳定交付",
                        "硬指标：准时性、完整性、口径一致；交付质量事故 0",
                    ], fill=LIGHT, title_color=NAVY)
    add_bullet_card(s, Inches(6.9), Inches(1.5), Inches(5.9), Inches(4.6),
                    "项目背景 · 痛点与动机", [
                        "业务取数依赖专人写 SQL，沟通链路长",
                        "数据源多：关系库 / 数仓 / NoSQL，语法与口径复杂",
                        "Text-to-SQL 只生成不执行，不管权限、不会自愈",
                        "db-agent：把模型 + 工具 + 权限 + 自愈 + 评测做成可交付 Harness",
                    ], fill=LIGHT, title_color=BLUE)
    add_text(s, Inches(0.7), Inches(6.3), Inches(12.0), Inches(0.6),
             [("从「交付保障」到「Agent 应用开发」：同一个目标——让数据准确、安全、及时地到达需要它的人。",
               0, 14, True, NAVY)])

    # ── 3 项目背景与目标 ──────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "项目背景与目标", "为什么需要一个数据 Agent 的 Harness", 3)
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(5.9), Inches(4.5),
                    "业务痛点", [
                        "取数依赖专人写 SQL，沟通链路长",
                        "数据源多：关系库 / 数仓 / NoSQL",
                        "Agent 直接查库：权限、安全、审计难控",
                        "LLM 输出不稳定，容易编造数据或反复报错",
                    ], fill=LIGHT, title_color=RED)
    add_bullet_card(s, Inches(6.9), Inches(1.5), Inches(5.9), Inches(4.5),
                    "项目目标", [
                        "自然语言自助取数，覆盖 SQL / Hive / HBase",
                        "多 Agent 编排：专业分工 + 质量闸门",
                        "RBAC + HITL + 护栏：越权与误操作兜底",
                        "三层自愈 + 全程可观测 + 可评测",
                    ], fill=LIGHT, title_color=GREEN)
    add_text(s, Inches(0.7), Inches(6.2), Inches(12.0), Inches(0.6),
             [("核心观点：Agent = 模型 + Harness；本项目不训练模型，而是给模型搭一套能在数据库领域稳定工作的环境。",
               0, 14, True, NAVY)])

    # ── 4 整体架构 ────────────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "整体架构", "三种入口共用一套 Harness", 4)

    add_rect(s, Inches(0.7), Inches(1.5), Inches(12.0), Inches(1.25), fill=NAVY)
    add_text(s, Inches(0.95), Inches(1.62), Inches(11.6), Inches(0.35),
             [("入口层", 0, 14, True, ORANGE)])
    entries = ["CLI  main.py", "Streamlit  app.py", "Web  FastAPI + SSE + React"]
    x = Inches(0.95)
    for e in entries:
        box = add_rect(s, x, Inches(2.0), Inches(3.7), Inches(0.55), fill=BLUE)
        fill_shape_text(box, e, 13, WHITE)
        x += Inches(3.85)

    add_rect(s, Inches(0.7), Inches(3.05), Inches(12.0), Inches(2.6), fill=LIGHT)
    add_text(s, Inches(0.95), Inches(3.17), Inches(11.6), Inches(0.35),
             [("Harness 六维（运行时代码）", 0, 14, True, NAVY)])
    dims = [
        ("编排", "LangGraph 6 Agent / ReAct"),
        ("工具", "15+ Tools 统一注册"),
        ("上下文", "Schema Linking / few-shot"),
        ("记忆", "短期 + 向量长期"),
        ("约束", "RBAC / 护栏 / HITL"),
        ("观测", "Trace / Opik / 指标"),
    ]
    for i, (t, d) in enumerate(dims):
        col = i % 3
        row = i // 3
        bx = Inches(0.95 + col * 4.05)
        by = Inches(3.58 + row * 0.95)
        box = add_rect(s, bx, by, Inches(3.75), Inches(0.82), fill=WHITE, line=BLUE)
        add_text(s, bx + Inches(0.15), by + Inches(0.1), Inches(3.4), Inches(0.3),
                 [(t, 0, 14, True, NAVY)])
        add_text(s, bx + Inches(0.15), by + Inches(0.42), Inches(3.4), Inches(0.35),
                 [(d, 0, 11, False, MUTED)])

    add_rect(s, Inches(0.7), Inches(5.9), Inches(12.0), Inches(1.0), fill=TEAL)
    add_text(s, Inches(0.95), Inches(6.0), Inches(11.6), Inches(0.35),
             [("数据层", 0, 14, True, WHITE)])
    add_text(s, Inches(0.95), Inches(6.35), Inches(11.6), Inches(0.5),
             [("SQLite 业务库（departments/employees/orders…）  |  Hive 模拟数仓（ods/dwd/dim）  |  HBase 内存模拟 KV",
               0, 13, True, WHITE)])
    add_diagram_slide(prs, "工程机制全景", "对话中生成的机制总览图",
                      "docs/diagrams/ppt/mechanism-overview-ppt.png")

    # ── 5 核心机制 1：多 Agent 编排 ───────────────────────────────────
    s = new_slide(prs)
    add_header(s, "核心机制 1：多 Agent 编排", "LangGraph StateGraph + Router 三层短路", 5)
    agents = [
        ("SQL", "只查数据，不分析"),
        ("Analysis", "只分析，不查库"),
        ("Strategy", "制度 / 指标口径"),
        ("HBase", "KV 方言与命令"),
        ("Hive", "数仓方言与模板"),
        ("DataQuality", "质量预检，只报事实"),
    ]
    for i, (t, d) in enumerate(agents):
        col = i % 3
        row = i // 3
        bx = Inches(0.7 + col * 4.1)
        by = Inches(1.55 + row * 1.05)
        box = add_rect(s, bx, by, Inches(3.85), Inches(0.92), fill=WHITE, line=BLUE)
        add_text(s, bx + Inches(0.2), by + Inches(0.12), Inches(3.4), Inches(0.35),
                 [(t, 0, 16, True, NAVY)])
        add_text(s, bx + Inches(0.2), by + Inches(0.5), Inches(3.4), Inches(0.35),
                 [(d, 0, 11, False, MUTED)])

    add_bullet_card(s, Inches(0.7), Inches(3.9), Inches(6.0), Inches(2.85),
                    "Router 三层短路", [
                        "硬规则：闲聊 / 制度 / 元问题直接拦截",
                        "LRU 缓存：同 query 复用 plan",
                        "LLM：兜底意图分类 + JSON plan",
                    ])
    add_bullet_card(s, Inches(6.95), Inches(3.9), Inches(5.75), Inches(2.85),
                    "质量闸与状态", [
                        "DataQuality 首查预检（1h 时效）",
                        "Confidence Gate：SQL 低分进 HITL",
                        "Reflection 重写 ≤2 次 / 失败重规划 ≤1 次",
                        "Checkpointer：SQLite / Redis，断点续跑",
                    ])

    # ── 6 核心机制 2：安全与权限 ─────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "核心机制 2：安全与权限", "RBAC + 三层护栏 + 双层 HITL", 6)
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(2.7),
                    "5 角色 RBAC（权限存 DB 可热改）", [
                        "dba：全工具全表",
                        "manager：行级 dept_id 隔离",
                        "analyst：业务表 + HBase",
                        "viewer：只读元数据",
                        "support：窄表白名单",
                    ])
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.75), Inches(2.7),
                    "三层护栏", [
                        "输入：注入 / 空 / 超长，零 token",
                        "SQL：仅 SELECT，写操作硬拦",
                        "输出：PII 告警 + prompt 泄露硬拦",
                    ])
    add_bullet_card(s, Inches(0.7), Inches(4.4), Inches(12.0), Inches(2.2),
                    "双层 HITL 人工审批（LangGraph 原生 interrupt）", [
                        "SQL 敏感列：salary / cost / budget",
                        "HBase 破坏性操作：put / delete / drop / truncate",
                        "Command(resume=...) 恢复，Checkpointer 保证状态不丢",
                        "Web API：WEB_API_TOKEN 可选鉴权",
                    ], title_color=RED)
    add_diagram_slide(prs, "安全与权限链路", "三层护栏 + 权限网关 + HITL",
                      "docs/diagrams/ppt/security-chain-ppt.png")

    # ── 7 核心机制 3：记忆与上下文 ───────────────────────────────────
    s = new_slide(prs)
    add_header(s, "核心机制 3：记忆与上下文", "三层记忆 + 检索增强 + 自学习闭环", 7)
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(2.6),
                    "记忆系统", [
                        "短期：滑动窗口 + LLM 摘要压缩",
                        "长期：ChromaDB 向量召回",
                        "Self-Query：先拆意图再过滤元数据",
                        "TokenBudget + HybridWindow 主动压缩",
                    ])
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.75), Inches(2.6),
                    "上下文增强", [
                        "Schema Linking + 值级索引",
                        "few-shot：相似 SQL 样例注入",
                        "高频指标模板优先，命中零 LLM",
                    ])
    add_bullet_card(s, Inches(0.7), Inches(4.35), Inches(12.0), Inches(2.25),
                    "自学习闭环", [
                        "从 run_query 工具层捕获「真正执行成功」的 SQL",
                        "质量门：仅 SELECT / 非超时 / 真实库 dry-run",
                        "回流样例库 → 下次 few-shot 直接命中",
                        "HITL 批准的 SQL 也回流（人工背书）",
                    ], title_color=GREEN)
    add_diagram_slide(prs, "记忆、上下文与观测闭环", "记忆 → 上下文 → 执行 → 观测 → 回流",
                      "docs/diagrams/ppt/memory-obs-loop-ppt.png")

    # ── 8 核心机制 4：三层自愈 ───────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "核心机制 4：三层自愈", "重试 → 执行自愈 → 熔断降级", 8)
    layers = [
        ("第 1 层 · API 重试", ORANGE,
         "429 / 5xx / 连接错误\n指数退避 + 随机抖动\n未输出才重试，避免重复内容"),
        ("第 2 层 · 执行自愈", BLUE,
         "SQL 报错带 retryable + hint → 重写 ≤2 次\nAgent 超时 → 回 Router 重规划 ≤1 次\nReflection 不通过 → 退回重写 ≤2 次"),
        ("第 3 层 · 熔断降级", RED,
         "连续失败 ≥ 阈值 → open 快速失败\n冷却后半开试探，成功恢复\n打开时发告警，不再烧钱"),
    ]
    y = Inches(1.55)
    for title, color, desc in layers:
        add_rect(s, Inches(0.7), y, Inches(12.0), Inches(1.35), fill=LIGHT)
        add_rect(s, Inches(0.7), y, Inches(2.6), Inches(1.35), fill=color)
        add_text(s, Inches(0.7), y + Inches(0.45), Inches(2.6), Inches(0.5),
                 [(title, 0, 14, True, WHITE)], align=PP_ALIGN.CENTER)
        add_text(s, Inches(3.55), y + Inches(0.18), Inches(9.0), Inches(1.0),
                 [(d, 0, 13, False, DARK)])
        y += Inches(1.5)
    add_text(s, Inches(0.7), Inches(6.35), Inches(12.0), Inches(0.6),
             [("另外：写工具幂等守卫（save_to_memory / run_hbase）同参数 TTL 窗口内不重复执行，防止重试造成副作用。",
               0, 14, True, NAVY)])
    add_diagram_slide(prs, "三层自愈", "重试 → 执行自愈 → 熔断降级",
                      "docs/diagrams/ppt/self-healing-ppt.png")

    # ── 9 核心机制 5：可观测与运维 ───────────────────────────────────
    s = new_slide(prs)
    add_header(s, "核心机制 5：可观测与运维", "本地审计 + 平台 Trace + 生产指标", 9)
    cards = [
        ("Trace JSONL", "请求级 Span 追踪\nSQL 参数脱敏\n离线可审计"),
        ("Opik 平台", "Trace / Feedback\nDataset / Experiment\nLLM-as-Judge"),
        ("Prometheus", "/api/metrics\n查询数 / 成功率\ntoken / 耗时 / 错误"),
        ("告警 Webhook", "熔断 / Agent 超时\n日志必发\n可配 Slack/钉钉"),
        ("Task board", "plan 落盘 .tasks/\n✓ / ✗ 状态可见"),
        ("成本估算", "按模型统计 token\n人民币 / 美元"),
    ]
    for i, (t, d) in enumerate(cards):
        col = i % 3
        row = i // 3
        bx = Inches(0.7 + col * 4.1)
        by = Inches(1.55 + row * 1.85)
        box = add_rect(s, bx, by, Inches(3.85), Inches(1.6), fill=LIGHT)
        add_text(s, bx + Inches(0.25), by + Inches(0.15), Inches(3.35), Inches(0.4),
                 [(t, 0, 15, True, NAVY)])
        add_text(s, bx + Inches(0.25), by + Inches(0.62), Inches(3.35), Inches(0.9),
                 [(d, 0, 12, False, DARK)])
    add_text(s, Inches(0.7), Inches(5.55), Inches(12.0), Inches(1.2),
             [("状态外置：Checkpointer 支持 Redis（TTL 自动清理），Redis 不可用自动降级 SQLite；",
               0, 14, True, NAVY),
              ("StreamingRunner 按 session 管理并空闲回收，Web HITL 按 session 精确 resume。",
               0, 13, False, MUTED)])

    # ── 10 Query 完整链路 ────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "Query 完整链路", "一条查询从进来到返回：入口 → 护栏 → Router → 多 Agent → 质检 → 返回", 10)
    steps = [
        ("输入", "CLI/Web/Streamlit"),
        ("护栏", "注入/空/超长"),
        ("Router", "硬规则/缓存/LLM"),
        ("DQ", "质量预检(可选)"),
        ("SQL Agent", "schema+查询+权限"),
        ("置信度门", "6 项评分"),
        ("Analysis", "结论+图表"),
        ("Reflection", "质量审查"),
        ("返回", "答案+Trace"),
    ]
    x = Inches(0.38)
    for i, (t, d) in enumerate(steps):
        w = Inches(1.38)
        box = add_rect(s, x, Inches(2.0), w, Inches(1.15), fill=BLUE if i % 2 == 0 else NAVY)
        fill_shape_text(box, t, 13, WHITE)
        add_text(s, x, Inches(3.2), w, Inches(0.6),
                 [(d, 0, 9, False, MUTED)], align=PP_ALIGN.CENTER)
        if i < len(steps) - 1:
            add_arrow(s, x + w + Inches(0.02), Inches(2.48), Inches(0.12), Inches(0.2))
        x += w + Inches(0.16)

    add_text(s, Inches(0.7), Inches(4.1), Inches(12.0), Inches(0.5),
             [("HITL 中断：敏感 SQL / HBase 写 / 澄清 / 低置信度都会在这里暂停，人工批准后 resume 继续。",
               0, 14, True, RED)])
    add_bullet_card(s, Inches(0.7), Inches(4.75), Inches(12.0), Inches(1.9),
                    "single 模式差异", [
                        "不做 Router / DQ / Confidence / Reflection，直接单 ReAct loop（15 tools）",
                        "HITL 退化为 run_query 返回「需要审批」标记，没有原生 interrupt 暂停/恢复",
                        "multi 是生产主链路；single 是轻量/快速验证入口",
                    ])
    add_diagram_slide(prs, "Query 完整链路", "一条查询从进来到返回（本对话生成的流程图）",
                      "docs/diagrams/ppt/query-flow-ppt.png")

    # ── 11 工程质量与成果 ────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "工程质量与成果", "测试 / 评测 / 可运维", 11)
    metrics = [
        ("423", "测试用例"),
        ("~10s", "全离线零 API"),
        ("35+", "Eval 用例"),
        ("33/35", "LLM-Judge 通过"),
        ("3", "入口形态"),
        ("3", "数据源类型"),
    ]
    x = Inches(0.7)
    for num, label in metrics:
        box = add_rect(s, x, Inches(1.55), Inches(1.95), Inches(1.35), fill=LIGHT)
        add_text(s, x, Inches(1.7), Inches(1.95), Inches(0.5),
                 [(num, 0, 24, True, BLUE)], align=PP_ALIGN.CENTER)
        add_text(s, x, Inches(2.25), Inches(1.95), Inches(0.5),
                 [(label, 0, 12, False, MUTED)], align=PP_ALIGN.CENTER)
        x += Inches(2.05)

    add_bullet_card(s, Inches(0.7), Inches(3.15), Inches(6.0), Inches(3.5),
                    "测试体系", [
                        "冒烟：模块导入 / Tool 配线 / Graph 编译",
                        "单元：编排 / 权限 / 模板 / 自学习 / HITL",
                        "SSE / Web：事件流与 resume 测试",
                        "LLM / Embedding 全 fake 化，离线可跑",
                    ])
    add_bullet_card(s, Inches(6.95), Inches(3.15), Inches(5.75), Inches(3.5),
                    "产品化", [
                        "CLI 安装即用 + 启动配置校验",
                        "Docker Compose + 锁文件",
                        "代码拆分为 nodes/graph/runner/helpers",
                        "类型化 state/config，可测性好",
                    ])

    # ── 12 项目成果与亮点 ────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "项目成果与亮点", "STAR 化提炼", 12)
    rows = [
        ("S · 背景", "业务取数依赖专人，多数据源 + 安全合规要求高"),
        ("T · 任务", "做一个自然语言数据库分析 Agent 的完整 Harness"),
        ("A · 动作", "6 Agent 编排 / RBAC+HITL / 三层自愈 / 记忆闭环 / 双观测 / 评测体系"),
        ("R · 结果", "3 入口覆盖 SQL/Hive/HBase；423 测试离线 10s；Eval 33/35"),
    ]
    y = Inches(1.55)
    colors = [BLUE, TEAL, NAVY, GREEN]
    for i, (t, d) in enumerate(rows):
        add_rect(s, Inches(0.7), y, Inches(12.0), Inches(1.05), fill=LIGHT)
        add_rect(s, Inches(0.7), y, Inches(1.7), Inches(1.05), fill=colors[i])
        add_text(s, Inches(0.7), y + Inches(0.32), Inches(1.7), Inches(0.5),
                 [(t, 0, 14, True, WHITE)], align=PP_ALIGN.CENTER)
        add_text(s, Inches(2.65), y + Inches(0.16), Inches(9.9), Inches(0.8),
                 [(d, 0, 14, False, DARK)])
        y += Inches(1.2)
    add_text(s, Inches(0.7), Inches(6.45), Inches(12.0), Inches(0.6),
             [("一句话：把「用自然语言查数据库」从 Demo 做到可治理、可自愈、可评测的工程系统。",
               0, 14, True, NAVY)])

    # ── 13 面试讲解要点 ──────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "面试讲解要点", "先定位，再展开，留追问钩子", 13)
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(12.0), Inches(2.4),
                    "怎么讲", [
                        "30 秒定位：Agent = 模型 + Harness，我做的后者",
                        "2 分钟架构：入口 → 六维 → 多 Agent → 数据层",
                        "深挖 1-2 个机制：优先安全（HITL/RBAC）或自愈（三层）",
                        "收尾：评测与测试怎么保证不退化",
                    ], fill=LIGHT, title_color=NAVY)
    add_bullet_card(s, Inches(0.7), Inches(4.15), Inches(6.0), Inches(2.6),
                    "高频追问", [
                        "Router 为什么硬规则优先？",
                        "HITL 用 interrupt 而不是审批队列？",
                        "SQL 自愈怎么防止死循环？",
                        "权限为什么存 DB 而不是代码？",
                    ])
    add_bullet_card(s, Inches(6.95), Inches(4.15), Inches(5.75), Inches(2.6),
                    "可接的改进点", [
                        "SQL 解析 AST 化（sqlglot）",
                        "行级权限落数据库原生 RLS",
                        "真实 PostgreSQL / HBase / Hive 接入",
                        "Opik / Trace 打点收敛成统一 wrapper",
                    ])

    # ── 14 其他项目一览 ──────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "其他项目一览", "fin-agent / fraud-agent（材料待补充）", 14)
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(3.4),
                    "fin-agent · AI 研报分析 Agent", [
                        "FastAPI + LangGraph + RAG + MCP",
                        "多源研报检索 / 结构化提取 / 对比分析",
                        "待补充：数据源、Agent 编排、RAG 效果、MCP 能力",
                    ], fill=LIGHT, title_color=NAVY)
    add_bullet_card(s, Inches(6.95), Inches(1.5), Inches(5.75), Inches(3.4),
                    "fraud-agent · 反欺诈分析 Agent", [
                        "LangGraph + RAG + Streamlit + 10+ Tool",
                        "双轨编排 + 知识库 + 规则引擎",
                        "待补充：双轨指什么、Tool 清单、数据与效果",
                    ], fill=LIGHT, title_color=NAVY)
    add_text(s, Inches(0.7), Inches(5.25), Inches(12.0), Inches(1.2),
             [("建议：按 STAR 补全这两个项目，并补充量化指标（准确率 / 召回 / 延迟 / 节省人力）。",
               0, 14, True, ORANGE),
              ("本次 PPT 以 db-agent 为主项目展开，fin/fraud 留了占位页。",
               0, 13, False, MUTED)])

    # ── 15 结尾 ──────────────────────────────────────────────────────
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.6), SW, Inches(0.06), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(2.6), Inches(11.3), Inches(1.0),
             [("谢谢 · Q&A", 0, 48, True, WHITE)])
    add_text(s, Inches(1.0), Inches(4.0), Inches(11.3), Inches(0.6),
             [("db-agent · 企业级自然语言数据库分析 Agent", 0, 18, True, TEAL)])
    add_text(s, Inches(1.0), Inches(6.2), Inches(11.3), Inches(0.6),
             [("联系/演示信息占位", 0, 13, False, MUTED)])

    out = "docs/db-agent-项目讲解.pptx"
    prs.save(out)
    print(f"已生成: {out}")


if __name__ == "__main__":
    build()
