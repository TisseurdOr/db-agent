"""生成「工作经历 + 项目讲解」面试 PPT。

叙事顺序：先工作经历（花旗 Olympus），再项目经历（db-agent），中间有能力迁移桥接页。
内容来源：docs/面试/公司面经/平安背稿.md、docs/项目介绍/项目案例.md、docs/项目介绍/功能点评分表.md、此前对话定稿。

用法:
    python scripts/generate_career_project_deck.py

输出:
    docs/简历与经历/工作经历与项目讲解.pptx
"""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ── 配色：金融数据底 + Agent 工程感 ──────────────────────────────────
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
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "工作经历与项目讲解.pptx"


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
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, 0)
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


def fill_shape_text(sp, text, size=13, color=WHITE, bold=True, align=PP_ALIGN.CENTER):
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.06)
    tf.margin_right = Inches(0.06)
    tf.margin_top = Inches(0.02)
    tf.margin_bottom = Inches(0.02)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    _set_run(run, text, size, color, bold)


def new_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def add_header(slide, title, subtitle="", page=None, section=""):
    add_rect(slide, 0, 0, SW, Inches(0.14), fill=BLUE, shape=MSO_SHAPE.RECTANGLE)
    if section:
        add_text(slide, Inches(0.55), Inches(0.28), Inches(4.0), Inches(0.3),
                 [(section, 0, 11, True, TEAL)])
        title_y = Inches(0.52)
        sub_y = Inches(1.12)
    else:
        title_y = Inches(0.35)
        sub_y = Inches(0.98)
    add_text(slide, Inches(0.55), title_y, Inches(11.2), Inches(0.55),
             [(title, 0, 28, True, NAVY)])
    if subtitle:
        add_text(slide, Inches(0.57), sub_y, Inches(11.0), Inches(0.35),
                 [(subtitle, 0, 13, False, MUTED)])
    if page is not None:
        add_text(slide, Inches(12.3), Inches(7.05), Inches(0.8), Inches(0.35),
                 [(str(page), 0, 12, False, MUTED)], align=PP_ALIGN.RIGHT)


def add_bullet_card(slide, x, y, w, h, title, bullets, fill=LIGHT, title_color=NAVY,
                    title_size=15, body_size=12):
    add_rect(slide, x, y, w, h, fill=fill)
    add_text(slide, x + Inches(0.22), y + Inches(0.16), w - Inches(0.44), Inches(0.38),
             [(title, 0, title_size, True, title_color)])
    lines = [(("• " + b), 0, body_size, False, DARK) for b in bullets]
    add_text(slide, x + Inches(0.22), y + Inches(0.58), w - Inches(0.44), h - Inches(0.72),
             lines, size=body_size, spacing=1.1)


def png_size(path):
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG: {path}")
    return struct.unpack(">II", head[16:24])


def add_diagram_slide(prs, title, subtitle, rel_path, page, section="项目经历"):
    path = ROOT / rel_path
    if not path.exists():
        return
    s = new_slide(prs)
    add_header(s, title, subtitle, page, section=section)
    w_px, h_px = png_size(path)
    aspect = w_px / h_px
    max_w, max_h = 12.0, 5.2
    w_in, h_in = max_w, max_w / aspect
    if h_in > max_h:
        h_in, w_in = max_h, max_h * aspect
    x = (SW - Inches(w_in)) / 2
    y = Inches(1.55) + (Inches(5.0) - Inches(h_in)) / 2
    s.shapes.add_picture(str(path), x, y, width=Inches(w_in), height=Inches(h_in))


def section_divider(prs, label, title, subtitle, page):
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    add_rect(s, 0, Inches(4.55), SW, Inches(0.06), fill=ORANGE, shape=MSO_SHAPE.RECTANGLE)
    add_text(s, Inches(1.0), Inches(2.0), Inches(11.0), Inches(0.4),
             [(label, 0, 16, True, TEAL)])
    add_text(s, Inches(1.0), Inches(2.55), Inches(11.0), Inches(1.0),
             [(title, 0, 40, True, WHITE)])
    add_text(s, Inches(1.0), Inches(3.7), Inches(11.0), Inches(0.6),
             [(subtitle, 0, 18, False, LIGHT)])
    add_text(s, Inches(12.3), Inches(7.05), Inches(0.8), Inches(0.35),
             [(str(page), 0, 12, False, MUTED)], align=PP_ALIGN.RIGHT)
    return s


def build():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH
    page = 0

    def next_page():
        nonlocal page
        page += 1
        return page

    # ── 1 封面 ───────────────────────────────────────────────────────
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    add_rect(s, 0, Inches(4.85), SW, Inches(0.06), fill=ORANGE, shape=MSO_SHAPE.RECTANGLE)
    add_text(s, Inches(1.0), Inches(1.5), Inches(11.3), Inches(0.45),
             [("面试讲解 · Career + Project", 0, 16, True, TEAL)])
    add_text(s, Inches(1.0), Inches(2.1), Inches(11.3), Inches(1.0),
             [("工作经历与项目讲解", 0, 48, True, WHITE)])
    add_text(s, Inches(1.0), Inches(3.25), Inches(11.3), Inches(0.8),
             [("伍彩琳  ·  重庆大学大数据与软件硕士", 0, 22, True, LIGHT),
              ("花旗 Olympus Core Data Analyst（3 年）→ Agent 应用开发", 0, 16, False, LIGHT)])
    add_text(s, Inches(1.0), Inches(5.2), Inches(11.3), Inches(1.0),
             [("Part 1  花旗工作经历（2 页）：公司介绍 + 四段日常经历", 0, 15, False, WHITE),
              ("Part 2  项目时序：单Agent→多Agent→安全→可靠性→工程化", 0, 15, False, WHITE)])
    next_page()

    # ── 2 目录 ───────────────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "目录", "工作经历两页；项目按五阶段时序：问题 → 改进", next_page())
    agenda = [
        ("01", "一分钟自我介绍", "数据工程底子 + Agent 落地能力"),
        ("02", "工作公司介绍", "Olympus Core · 岗位边界 · 交付怎么验"),
        ("03", "日常四个工作经历", "交付保障 / Metadata / Entitlement / Spark"),
        ("04", "项目时序路线图", "五阶段：问题 → 改进 → 叠成复杂系统"),
    ]
    for i, (num, title, sub) in enumerate(agenda):
        col, row = i % 2, i // 2
        x = Inches(0.7 + col * 6.2)
        y = Inches(1.5 + row * 1.65)
        add_rect(s, x, y, Inches(5.9), Inches(1.4), fill=LIGHT)
        add_text(s, x + Inches(0.25), y + Inches(0.28), Inches(1.0), Inches(0.6),
                 [(num, 0, 28, True, BLUE)])
        add_text(s, x + Inches(1.4), y + Inches(0.3), Inches(4.2), Inches(0.45),
                 [(title, 0, 18, True, NAVY)])
        add_text(s, x + Inches(1.4), y + Inches(0.8), Inches(4.2), Inches(0.4),
                 [(sub, 0, 13, False, MUTED)])

    # ── 3 自我介绍 ───────────────────────────────────────────────────
    s = new_slide(prs)
    add_header(s, "一分钟自我介绍", "面试开场定稿（30~40 秒）", next_page())
    add_rect(s, Inches(0.7), Inches(1.45), Inches(12.0), Inches(2.7), fill=LIGHT)
    add_text(s, Inches(0.95), Inches(1.65), Inches(11.5), Inches(2.3), [
        ("我叫伍彩琳，重庆大学大数据与软件硕士。", 0, 16, False, DARK),
        ("之前在花旗 Olympus Core 做了三年 Data Analyst，负责全数据平台交付保障——", 0, 16, False, DARK),
        ("Kafka、Spark、Hive 这一整套数据链路我都实际跑过，三年交付质量事故为零。", 0, 16, False, DARK),
        ("今年系统做 Agent 应用开发：从 NL2SQL 到数据治理多 Agent 系统——", 0, 16, False, DARK),
        ("LangGraph 10 节点、Router、HITL、RBAC、LLM-as-Judge，可靠性工程都在代码里做过。", 0, 16, False, DARK),
    ], spacing=1.25)
    add_bullet_card(s, Inches(0.7), Inches(4.4), Inches(5.9), Inches(2.3),
                    "匹配点", [
                        "数据工程硬底子：准时、完整、可追溯",
                        "大模型落地最头疼的「可控、可评测」解决过",
                        "不是只会调 API，而是能把 Agent 做成工程系统",
                    ], title_color=GREEN)
    add_bullet_card(s, Inches(6.85), Inches(4.4), Inches(5.85), Inches(2.3),
                    "关键数字（开场可点）", [
                        "花旗：3 年 / 0 事故 / 4 条业务线",
                        "Metadata：2h → 10min，提效 92%",
                        "db-agent：6 Agent / 10 节点 / 423 测试 / Eval 33/35",
                    ], title_color=BLUE)



    # PART 1 — 工作经历（严格 2 页）
    # 页1：公司 / 平台介绍
    s = new_slide(prs)
    add_header(s, "工作公司 · Olympus Core", "花旗 Markets 战略级大数据底座 · 批发数据再分发", next_page(),
               section="工作经历")
    add_bullet_card(s, Inches(0.55), Inches(1.4), Inches(6.05), Inches(3.35),
                    "平台是什么", [
                        "上游：多系统交易 / 持仓 / 事件 → 清洗校验路由",
                        "形成 Markets 权威数据副本（single source of truth）",
                        "下游：风控 / 合规 / 总账 / 流动性 / 监管报送",
                        "业务线：DGB / BKB / Opportunity / DDA-checking",
                        "硬指标：准时性 + 完整性（漏数/延迟/口径漂移会卡下游）",
                    ], title_color=NAVY, body_size=12)
    add_bullet_card(s, Inches(6.8), Inches(1.4), Inches(6.0), Inches(3.35),
                    "我的岗位与技术边界", [
                        "角色：Data Analyst · 全数据平台交付保障 · 3 年 0 事故",
                        "通道：Kafka 实时流 + SFTP 批量",
                        "链路：load→validation→dispatcher→monitor→recon",
                        "调度层 Autosys · 执行层 Spark + HDFS",
                        "口头禅：调度成功 ≠ 交付成功",
                        "⛔ Cancel & Correct 未覆盖，不硬接",
                    ], title_color=BLUE, body_size=12)
    add_bullet_card(s, Inches(0.55), Inches(4.95), Inches(12.25), Inches(1.7),
                    "日常交付怎么验（0 事故底气）", [
                        "双通道对账不同：SFTP 对「内容对不对」（行数+checksum+大小）；Kafka 对「有没有漏」（offset+序列号，唯一键幂等）",
                        "三层 recon：①行数完整性 ②checksum 内容一致 ③control total 金额准确 → 对不上先重拉，再 break report",
                        "SOX：每次 load 按 batch_id / 源文件 / offset 可追溯；Monitor 盯 SLA，巡检覆盖四条业务线准时与完整",
                    ], title_color=TEAL, body_size=12)

    # 页2：四个工作经历
    s = new_slide(prs)
    add_header(s, "日常四个工作经历", "内容与背稿一致，一页收束", next_page(),
               section="工作经历")
    cards = [
        ("01 交付保障", BLUE, [
            "Kafka + SFTP 双通道质量保障",
            "五环节全链路盯梢 + Autosys 调度",
            "三层对账：行数 / checksum / 金额",
            "漏数重拉；错数定位 batch 回退",
            "四条业务线稳定交付 · 0 事故",
        ]),
        ("02 Metadata 引擎", TEAL, [
            "Spring Boot + React 全栈工具",
            "7 字段描述一条 feed（勿报第 8 个）",
            "feed-name / 源文件 / 源·目标路径",
            "start·end / SLA → 生成 7 表 ~210 字段",
            "2h→10min 提效 92%；人工 review 后落",
        ]),
        ("03 Entitlement", ORANGE, [
            "登录认证 + 三层细粒度授权",
            "个人 / 组织 / 用户组叠加",
            "共享平台四条业务线访问可控",
            "通过内部合规审计",
            "对比 Agent：表级→行级 rewrite_sql",
        ]),
        ("04 Spark 倾斜治理", GREEN, [
            "Opportunity 作业 Stage 长尾",
            "根因：timestamp join key 扎堆",
            "大表+小表广播 → Broadcast Hash Join",
            "去掉按热 key 的 shuffle",
            "作业稳住；小表过大/双大表才另法",
        ]),
    ]
    for i, (title, color, bullets) in enumerate(cards):
        x = Inches(0.45 + i * 3.2)
        add_rect(s, x, Inches(1.4), Inches(3.05), Inches(5.3), fill=LIGHT)
        fill_shape_text(
            add_rect(s, x, Inches(1.4), Inches(3.05), Inches(0.55), fill=color),
            title, 13, WHITE, True)
        lines = [(("• " + b), 0, 11, False, DARK) for b in bullets]
        add_text(s, x + Inches(0.12), Inches(2.15), Inches(2.8), Inches(4.3),
                 lines, spacing=1.18)


    # PART 2 — 项目经历：时序路线图（问题 → 改进）
    section_divider(prs, "PART 2", "项目经历",
                    "时序路线：遇到什么问题 → 做了什么改进 → 长成今天的系统", next_page())

    # 起点
    s = new_slide(prs)
    add_header(s, "起点：业务痛点与目标", "不是调一个 LLM，而是把「说人话 → 查数 → 分析」做成工程系统", next_page(),
               section="项目经历")
    add_bullet_card(s, Inches(0.55), Inches(1.45), Inches(6.05), Inches(4.7),
                    "一开始遇到的问题", [
                        "业务要背三套语法：SQL / Hive / HBase",
                        "改字段要等数据组排期，链路长",
                        "市面 Text-to-SQL 多只生成、不执行",
                        "不管权限、不会改错、不可评测",
                        "银行数据治理：feed 对账 / 血缘 / load 监控",
                        "核心矛盾：job 成功 ≠ 数据真正落地",
                    ], title_color=RED, body_size=12)
    add_bullet_card(s, Inches(6.8), Inches(1.45), Inches(6.0), Inches(4.7),
                    "目标怎么定", [
                        "自然语言自助取数 + 分析",
                        "覆盖多引擎，职责可定位",
                        "权限 / 可靠性 / 评测一起做",
                        "定位：Agent = 模型 + Harness",
                        "本项目做 Harness，不训练模型",
                        "演进原则：每层都被真实问题逼出来",
                    ], title_color=GREEN, body_size=12)

    # 总路线图
    s = new_slide(prs)
    add_header(s, "演进总路线图", "五段时序：问题逼出机制，机制叠成复杂系统", next_page(),
               section="项目经历")
    roadmap = [
        ("一", "单 Agent", "模型不能稳定干活", "手写 ReAct · 真执行 · Caching", BLUE),
        ("二", "多 Agent", "一个大脑扛不住多引擎", "LangGraph · Router · 重规划", TEAL),
        ("三", "权限安全", "Prompt 拦不住越权", "RBAC · 护栏 · HITL", ORANGE),
        ("四", "可靠性", "会挂、会重、会烧钱", "重试→自愈→熔断→幂等→告警", RED),
        ("五", "工程化", "demo 上不了线", "测试 · Eval · CLI · 外置存储", GREEN),
    ]
    for i, (num, title, problem, fix, color) in enumerate(roadmap):
        y = Inches(1.4 + i * 1.0)
        fill_shape_text(
            add_rect(s, Inches(0.55), y, Inches(0.85), Inches(0.82), fill=color),
            num, 22, WHITE)
        add_rect(s, Inches(1.55), y, Inches(2.2), Inches(0.82), fill=LIGHT)
        add_text(s, Inches(1.7), y + Inches(0.22), Inches(1.95), Inches(0.45),
                 [(title, 0, 16, True, NAVY)])
        add_rect(s, Inches(3.9), y, Inches(4.2), Inches(0.82), fill=LIGHT)
        add_text(s, Inches(4.05), y + Inches(0.1), Inches(3.9), Inches(0.3),
                 [("遇到的问题", 0, 11, True, RED)])
        add_text(s, Inches(4.05), y + Inches(0.4), Inches(3.9), Inches(0.35),
                 [(problem, 0, 13, False, DARK)])
        add_rect(s, Inches(8.25), y, Inches(4.55), Inches(0.82), fill=LIGHT)
        add_text(s, Inches(8.4), y + Inches(0.1), Inches(4.25), Inches(0.3),
                 [("改进动作", 0, 11, True, GREEN)])
        add_text(s, Inches(8.4), y + Inches(0.4), Inches(4.25), Inches(0.35),
                 [(fix, 0, 13, False, DARK)])
    add_diagram_slide(prs, "五阶段演进图解", "从调 API → 能上线的 CLI 产品",
                      "docs/diagrams/five_stage_evolution.png", next_page())

    # 阶段一
    s = new_slide(prs)
    add_header(s, "阶段一 · 单 Agent", "先把「模型能干活」跑通", next_page(), section="项目经历")
    add_bullet_card(s, Inches(0.55), Inches(1.45), Inches(6.05), Inches(5.1),
                    "当时的问题", [
                        "直接调 LLM：会编造执行结果",
                        "AgentExecutor 是黑盒，温度/轮次/截断难控",
                        "SQL 一错就整段 crash",
                        "整库 schema 塞提示词又贵又难约束",
                        "成本不可控，高频问法每次从零生成",
                    ], title_color=RED, body_size=13)
    add_bullet_card(s, Inches(6.8), Inches(1.45), Inches(6.0), Inches(5.1),
                    "改进 → 产出", [
                        "手写 ReAct ~250 行，不用 AgentExecutor",
                        "工具真执行：模型只决策，结果回喂",
                        "结构化错误 {error, hint, retryable} 自纠",
                        "Prompt Caching：前缀一致，成本降 ~99.5%",
                        "模板优先：高频问法命中零 LLM",
                        "产出：能稳定执行工具的 CLI Agent",
                    ], title_color=GREEN, body_size=13)

    # 阶段二
    s = new_slide(prs)
    add_header(s, "阶段二 · 多 Agent 编排", "从「一个大脑」到「一组分工」", next_page(), section="项目经历")
    add_bullet_card(s, Inches(0.55), Inches(1.45), Inches(6.05), Inches(5.1),
                    "当时的问题", [
                        "业务不止 SQL：还有 Hive / HBase / 制度",
                        "一个 Agent 全包：上下文互相污染",
                        "权限与责任说不清是谁的错",
                        "每次路由都调 LLM，贵且慢",
                        "子任务失败没有可控重来路径",
                    ], title_color=RED, body_size=13)
    add_bullet_card(s, Inches(6.8), Inches(1.45), Inches(6.0), Inches(5.1),
                    "改进 → 产出", [
                        "选 LangGraph：图节点 + Checkpointer + interrupt",
                        "6 Agent / 10 节点，职责严格隔离",
                        "Router 四层：硬规则→继承→LRU→LLM",
                        "~80% 高频请求零 LLM 直接路由",
                        "失败重规划 ≤1，跳过脏缓存防死循环",
                        "产出：多引擎协同的编排系统",
                    ], title_color=GREEN, body_size=13)

    # 阶段三
    s = new_slide(prs)
    add_header(s, "阶段三 · 权限与安全", "Prompt 拦不住越权，必须硬拦截", next_page(), section="项目经历")
    add_bullet_card(s, Inches(0.55), Inches(1.45), Inches(6.05), Inches(5.1),
                    "当时的问题", [
                        "模型可被诱导越权查敏感列",
                        "只靠 system prompt：软约束失效",
                        "写操作 / 破坏性 HBase 命令风险高",
                        "同一张表不同部门不该看全量",
                        "注入、危险 SQL、输出泄密无人拦",
                    ], title_color=RED, body_size=13)
    add_bullet_card(s, Inches(6.8), Inches(1.45), Inches(6.0), Inches(5.1),
                    "改进 → 产出", [
                        "5 角色 RBAC：工具/表/行级三层",
                        "rewrite_sql 自动注入 WHERE dept_id=X",
                        "三层护栏：输入 / SQL / 输出（零 token）",
                        "HITL：敏感列与 HBase 写走 interrupt",
                        "工具白名单：Analysis 根本挂不上查库工具",
                        "产出：权限下沉工具层的安全闭环",
                    ], title_color=GREEN, body_size=13)
    add_diagram_slide(prs, "安全链路图解", "护栏 + RBAC + HITL（阶段三落地形态）",
                      "docs/diagrams/ppt/security-chain-ppt.png", next_page())

    # 阶段四
    s = new_slide(prs)
    add_header(s, "阶段四 · 可靠性闭环", "从「能跑」到「挂了也不崩、不烧钱」", next_page(), section="项目经历")
    add_bullet_card(s, Inches(0.55), Inches(1.45), Inches(6.05), Inches(5.1),
                    "当时的问题", [
                        "LLM 429/5xx/超时偶发",
                        "SQL 写错表名/字段就卡死",
                        "Agent 超轮数没有退路",
                        "连续失败还在重试 = 烧 token",
                        "重试/重规划让写工具执行两次",
                        "会话中断、前端断流无法续跑",
                    ], title_color=RED, body_size=12)
    add_bullet_card(s, Inches(6.8), Inches(1.45), Inches(6.0), Inches(5.1),
                    "改进 → 产出", [
                        "API：指数退避 + 抖动重试",
                        "执行：结构化 hint 重写 ≤2；Replan ≤1",
                        "熔断：达阈值 open，冷却半开试探",
                        "幂等：同参数 TTL 窗口不重复写",
                        "告警：熔断/超时/评测退化通知",
                        "Checkpointer 续跑 + SSE 断流治理",
                        "产出：重试→自愈→熔断→幂等→告警",
                    ], title_color=GREEN, body_size=12)
    add_diagram_slide(prs, "三层自愈图解", "可靠性闭环（阶段四）",
                      "docs/diagrams/ppt/self-healing-ppt.png", next_page())

    # 阶段五
    s = new_slide(prs)
    add_header(s, "阶段五 · 工程化落地", "从 demo 到能上线的 CLI 产品", next_page(), section="项目经历")
    add_bullet_card(s, Inches(0.55), Inches(1.45), Inches(6.05), Inches(5.1),
                    "当时的问题", [
                        "本地绿、CI 红：LLM 测试不可复现",
                        "状态堆在进程里，挂了就丢",
                        "向量库绑死单一后端，难切换生产",
                        "改代码可能静默把系统改坏",
                        "缺安装即用的产品入口与配置校验",
                    ], title_color=RED, body_size=13)
    add_bullet_card(s, Inches(6.8), Inches(1.45), Inches(6.0), Inches(5.1),
                    "改进 → 产出", [
                        "LLM/Embedding 全 fake → 423 测试离线 ~10s",
                        "会话/checkpoint 外置 Redis，挂了回落 SQLite",
                        "Chroma / Milvus 一行切换（VECTOR_DB）",
                        "Eval 47 条 + Judge 独立模型；降超 5pp 告警",
                        "CLI `db-agent` + Docker + 启动校验",
                        "产出：能上线、可回归、可运维的产品",
                    ], title_color=GREEN, body_size=13)

    # 今天长成什么样
    s = new_slide(prs)
    add_header(s, "今天的系统长什么样", "五段改进叠出来的 Harness 六维（下一页看大字号全景图）", next_page(),
               section="项目经历")
    add_rect(s, Inches(0.55), Inches(1.4), Inches(12.2), Inches(1.0), fill=NAVY)
    add_text(s, Inches(0.8), Inches(1.55), Inches(11.7), Inches(0.7),
             [("入口：CLI / Streamlit / Web(FastAPI+SSE+React)  →  共用同一套 Harness",
               0, 16, True, WHITE)], align=PP_ALIGN.LEFT)
    dims = [
        ("编排", "10 节点 / 6 Agent / Router"),
        ("工具", "白名单隔离 · 真执行"),
        ("上下文", "Schema Linking / few-shot"),
        ("记忆", "工作 / 短期 / 长期向量"),
        ("约束", "RBAC / 护栏 / HITL"),
        ("观测", "Trace / Opik / Eval"),
    ]
    for i, (t, d) in enumerate(dims):
        col, row = i % 3, i // 3
        bx = Inches(0.55 + col * 4.15)
        by = Inches(2.65 + row * 1.7)
        add_rect(s, bx, by, Inches(3.95), Inches(1.45), fill=LIGHT)
        add_text(s, bx + Inches(0.2), by + Inches(0.25), Inches(3.5), Inches(0.4),
                 [(t, 0, 16, True, NAVY)])
        add_text(s, bx + Inches(0.2), by + Inches(0.75), Inches(3.5), Inches(0.45),
                 [(d, 0, 13, False, MUTED)])
    add_diagram_slide(prs, "工程机制全景", "入口 → 编排 → Harness 六维（大字号重绘）",
                      "docs/diagrams/ppt/mechanism-overview-ppt.png", next_page())

    # Query 时序一条线
    s = new_slide(prs)
    add_header(s, "一条 Query 的现在时序", "把演进成果串成一次请求生命周期（下一页看大字号全链路）", next_page(),
               section="项目经历")
    steps = [
        ("输入", "三入口"),
        ("护栏", "阶段三"),
        ("Router", "阶段二"),
        ("DQ", "阶段二"),
        ("SQL", "阶段一/二"),
        ("置信度", "阶段二"),
        ("Analysis", "阶段二"),
        ("自愈", "阶段四"),
        ("Trace", "阶段五"),
    ]
    x = Inches(0.35)
    for i, (t, d) in enumerate(steps):
        w = Inches(1.35)
        box = add_rect(s, x, Inches(1.7), w, Inches(1.15), fill=BLUE if i % 2 == 0 else NAVY)
        fill_shape_text(box, t, 13, WHITE)
        add_text(s, x, Inches(3.0), w, Inches(0.5),
                 [(d, 0, 11, False, MUTED)], align=PP_ALIGN.CENTER)
        x += w + Inches(0.1)
    add_bullet_card(s, Inches(0.55), Inches(3.7), Inches(12.2), Inches(2.9),
                    "时序怎么讲（30 秒）", [
                        "先护栏（阶段三）再路由（阶段二），查数走 SQL，对比走 Analysis",
                        "低置信度 / 敏感操作 interrupt 等人；失败走自愈与熔断（阶段四）",
                        "成功 SQL 可回流样例；全程 Trace + Eval 防退化（阶段五）",
                        "一句话：复杂不是堆功能，是五次「出事 → 补一层」叠出来的",
                    ], title_color=NAVY, body_size=13)
    add_diagram_slide(prs, "全链路生命周期", "入口→护栏→Router→Agent→闸门→分析→Done（大字号重绘）",
                      "docs/diagrams/ppt/query-flow-ppt.png", next_page())

    # 成果
    s = new_slide(prs)
    add_header(s, "演进结果 · 数字收束", "每阶段都留下可验证的产出", next_page(), section="项目经历")
    metrics = [
        ("6", "专业 Agent"),
        ("10", "图节点"),
        ("423", "离线测试"),
        ("33/35", "Eval 通过"),
        ("99.5%", "缓存降本"),
        ("5", "演进阶段"),
    ]
    for i, (num, label) in enumerate(metrics):
        x = Inches(0.55 + i * 2.1)
        add_rect(s, x, Inches(1.5), Inches(1.95), Inches(1.35), fill=LIGHT)
        add_text(s, x, Inches(1.65), Inches(1.95), Inches(0.55),
                 [(num, 0, 22, True, BLUE)], align=PP_ALIGN.CENTER)
        add_text(s, x, Inches(2.3), Inches(1.95), Inches(0.4),
                 [(label, 0, 12, False, MUTED)], align=PP_ALIGN.CENTER)
    rows = [
        ("阶段一", "能稳定执行工具的 CLI Agent"),
        ("阶段二", "多引擎编排 + Router 短路 + 重规划"),
        ("阶段三", "权限下沉工具层（RBAC + HITL + 护栏）"),
        ("阶段四", "可靠性闭环（挂了也不崩、不烧钱）"),
        ("阶段五", "可回归、可运维、能上线的产品形态"),
    ]
    y = Inches(3.15)
    for title, desc in rows:
        add_rect(s, Inches(0.55), y, Inches(2.4), Inches(0.62), fill=NAVY)
        add_text(s, Inches(0.65), y + Inches(0.15), Inches(2.2), Inches(0.4),
                 [(title, 0, 13, True, WHITE)], align=PP_ALIGN.CENTER)
        add_rect(s, Inches(3.1), y, Inches(9.65), Inches(0.62), fill=LIGHT)
        add_text(s, Inches(3.3), y + Inches(0.15), Inches(9.3), Inches(0.4),
                 [(desc, 0, 14, False, DARK)])
        y += Inches(0.7)

    s = new_slide(prs)
    add_header(s, "讲解路线建议", "工作两页带过；项目按时序讲「出事→补一层」", next_page())
    add_bullet_card(s, Inches(0.7), Inches(1.5), Inches(6.0), Inches(5.1),
                    "推荐口述顺序", [
                        "30 秒自我介绍",
                        "工作：公司页 + 四经历页（各点一句）",
                        "项目起点：三痛 + job成功≠数据落地",
                        "总路线图：五阶段问题→改进扫一眼",
                        "深挖 1~2 段：安全或可靠性最加分",
                        "收尾：今天六维 + 数字 + 不是堆功能",
                    ], title_color=NAVY)
    add_bullet_card(s, Inches(6.9), Inches(1.5), Inches(5.9), Inches(5.1),
                    "时序追问钩子", [
                        "为什么先手写 ReAct 不用 AgentExecutor？",
                        "一个 Agent 不够时为什么选 LangGraph？",
                        "Prompt 为什么拦不住越权？",
                        "熔断和幂等分别防什么问题？",
                        "工程化阶段解决的是哪类「假绿」？",
                        "复杂系统是怎么一步步长出来的？",
                    ], title_color=BLUE)

    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY, shape=MSO_SHAPE.RECTANGLE)
    add_rect(s, 0, Inches(4.55), SW, Inches(0.06), fill=ORANGE, shape=MSO_SHAPE.RECTANGLE)
    add_text(s, Inches(1.0), Inches(2.3), Inches(11.3), Inches(1.0),
             [("谢谢 · Q&A", 0, 48, True, WHITE)])
    add_text(s, Inches(1.0), Inches(3.5), Inches(11.3), Inches(0.8),
             [("数据工程底子 × Agent 可靠性工程", 0, 20, True, TEAL),
              ("让数据准确、安全、及时地到达需要它的人", 0, 16, False, LIGHT)])
    next_page()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    print(f"已生成: {OUT}  （约 {len(prs.slides)} 页）")


if __name__ == "__main__":
    build()
