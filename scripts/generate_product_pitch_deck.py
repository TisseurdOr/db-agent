"""生成《db-agent 产品介绍 · 推销版》PPT。

讲稿放在 docs/项目介绍/产品介绍讲稿.md（避免嵌入备注导致部分导入器报“格式不支持”）。
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


def set_notes(slide, text):
    # 兼容旧调用：备注不再写入 pptx，统一放 docs/项目介绍/产品介绍讲稿.md
    return None


def build():
    prs = Presentation()
    prs.slide_width = SW
    prs.slide_height = SH

    # 1 封面
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.75), SW, Inches(0.06), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(1.6), Inches(11.3), Inches(1.2),
             [("db-agent", 0, 60, True, WHITE)])
    add_text(s, Inches(1.0), Inches(2.75), Inches(11.3), Inches(0.7),
             [("让业务用一句话查数，安全、准确、可审计", 0, 26, True, TEAL)])
    add_text(s, Inches(1.0), Inches(3.6), Inches(11.3), Inches(1.0),
             [("自然语言 → SQL / Hive / HBase / 指标口径 / 趋势分析", 0, 18, False, LIGHT),
              ("不是聊天机器人，而是让模型写 SQL 不出事的工程系统", 0, 15, False, LIGHT)])
    add_text(s, Inches(1.0), Inches(6.7), Inches(11.3), Inches(0.5),
             [("产品介绍 · 演示版", 0, 13, False, MUTED)])
    set_notes(s, "大家好，今天用几分钟介绍我做的产品 db-agent。它解决一件事：让不懂 SQL 的业务同学，用一句话就能安全、准确地查数据。")

    # 2 问题
    s = new_slide(prs)
    add_header(s, "先看业务现状", "取数为什么这么难", 2)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(4.6),
             "业务侧的痛点", [
                 "查数要等数据组排期，改一个字段等几周",
                 "SQL / Hive / HBase 三套语法，业务学不会",
                 "同一个指标，各部门口径还不一样",
             ], fill=LIGHT, title_color=RED)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(4.6),
             "市面工具的短板", [
                 "Text-to-SQL 只生成 SQL，不负责执行",
                 "不鉴权：谁能查 salary 没人管",
                 "报错不会自愈，结果对错没评测",
             ], fill=LIGHT, title_color=RED)
    add_text(s, Inches(0.7), Inches(6.3), Inches(12.0), Inches(0.6),
             [("结论：缺的不是“能写 SQL 的模型”，而是“让 SQL 不出事的工程系统”。", 0, 14, True, NAVY)])
    set_notes(s, "先讲业务现状：取数要排队、口径要对齐、市面工具只生成 SQL 不管执行和安全。所以缺的不是模型，而是工程系统。")

    # 3 答案
    s = new_slide(prs)
    add_header(s, "我们的答案", "说人话 → 查数 → 分析", 3)
    steps = [
        ("说人话", "业务用中文提问，不需要懂 SQL"),
        ("安全查数", "权限硬拦截 + 敏感操作人工审批"),
        ("自动分析", "多 Agent 分工：查数、分析、口径、质量"),
        ("可评测", "失败自愈 + 全程可观测 + 评测兜底"),
    ]
    y = Inches(1.55)
    for i, (t, d) in enumerate(steps, 1):
        add_rect(s, Inches(0.8), y, Inches(11.7), Inches(1.05), fill=LIGHT)
        add_rect(s, Inches(0.8), y, Inches(0.18), Inches(1.05), fill=BLUE)
        add_text(s, Inches(1.2), y + Inches(0.15), Inches(3.0), Inches(0.7),
                 [(t, 0, 18, True, NAVY)])
        add_text(s, Inches(4.4), y + Inches(0.15), Inches(7.8), Inches(0.8),
                 [(d, 0, 14, False, MUTED)])
        y += Inches(1.2)
    set_notes(s, "db-agent 的定位：说人话、安全查数、自动分析、可评测。它把模型、工具、权限、自愈、评测串成一个完整闭环。")

    # 4 核心能力
    s = new_slide(prs)
    add_header(s, "五个关键词", "多引擎 · 多 Agent · 安全 · 自愈 · 评测", 4)
    cards = [
        ("多引擎问数", "SQL / Hive / HBase", GREEN),
        ("6 Agent 编排", "查数 / 分析 / 口径 / 质量", BLUE),
        ("权限与 HITL", "RBAC + 人工审批", ORANGE),
        ("三层自愈", "重试 / 重写 / 熔断", TEAL),
        ("评测与自学习", "Eval + 成功 SQL 回流", NAVY),
    ]
    for i, (t, d, c) in enumerate(cards):
        col = i % 3
        row = i // 3
        bx = Inches(0.7 + col * 4.1)
        by = Inches(1.55 + row * 1.9)
        add_rect(s, bx, by, Inches(3.85), Inches(1.65), fill=LIGHT)
        add_rect(s, bx, by, Inches(3.85), Inches(0.14), fill=c)
        add_text(s, bx + Inches(0.25), by + Inches(0.35), Inches(3.35), Inches(0.5),
                 [(t, 0, 16, True, NAVY)])
        add_text(s, bx + Inches(0.25), by + Inches(0.9), Inches(3.35), Inches(0.6),
                 [(d, 0, 13, False, MUTED)])
    add_text(s, Inches(0.7), Inches(5.75), Inches(12.0), Inches(0.6),
             [("一句话：把数据库领域的高频能力，做成了可拼装的 Agent 模块。", 0, 14, True, NAVY)])
    set_notes(s, "五个关键词介绍核心能力：多引擎、多 Agent、安全、自愈、评测。每个都可以展开讲。")

    # 5 安全（重点）
    s = new_slide(prs)
    add_header(s, "安全，是金融场景的第一诉求", "权限硬拦截 + 人工审批 + 审计", 5)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(2.7),
             "5 角色 RBAC", [
                 "工具级白名单",
                 "表级过滤",
                 "行级 WHERE dept_id 隔离",
                 "敏感列 salary / cost / budget",
             ], fill=LIGHT, title_color=RED)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(2.7),
             "三层护栏 + HITL", [
                 "输入：注入 / 空 / 超长",
                 "SQL：仅 SELECT",
                 "输出：PII 告警 + prompt 泄露硬拦",
                 "敏感操作人工审批，可暂停可恢复",
             ], fill=LIGHT, title_color=RED)
    add_card(s, Inches(0.7), Inches(4.45), Inches(12.0), Inches(2.1),
             "审计", [
                 "Trace JSONL：SQL 参数脱敏后落盘",
                 "Opik / Prometheus / 告警：每一步可追溯",
                 "Web API 可选 Bearer Token 鉴权",
             ], fill=LIGHT, title_color=RED)
    set_notes(s, "金融场景最关心安全。我们有五角色权限、三层护栏、人工审批和审计。敏感数据不是靠模型自觉，而是靠工具层硬拦截。")

    # 6 可靠
    s = new_slide(prs)
    add_header(s, "不怕模型出错", "三层自愈 + 熔断 + 幂等 + 观测", 6)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(2.7),
             "错误接得住", [
                 "API 重试：429/5xx 指数退避",
                 "SQL 报错回喂模型重写（≤2 次）",
                 "Agent 超时回 Router 重规划（≤1 次）",
                 "Reflection 不通过退回重写（≤2 次）",
             ], fill=LIGHT, title_color=TEAL)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(2.7),
             "成本守得住", [
                 "熔断：连续失败快速失败，不烧钱",
                 "幂等：写工具不重复执行",
                 "模板 > 缓存 > LLM：高频问题零 LLM",
             ], fill=LIGHT, title_color=TEAL)
    add_card(s, Inches(0.7), Inches(4.45), Inches(12.0), Inches(2.1),
             "全程可观测", [
                 "Trace JSONL（SQL 脱敏）+ Opik 双写",
                 "Prometheus /api/metrics + Webhook 告警",
                 "Task board：每一步执行状态可见",
             ], fill=LIGHT, title_color=TEAL)
    set_notes(s, "我们不怕模型出错，因为错误会被系统接住：重试、重写、重规划、熔断、幂等，而且每一步都有观测和告警。")

    # 7 记忆
    s = new_slide(prs)
    add_header(s, "越用越聪明", "记忆 + 检索 + 自学习", 7)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(2.7),
             "记忆", [
                 "短期：最近几轮 + 摘要压缩",
                 "长期：向量库跨会话召回",
                 "Self-Query：先拆意图再过滤",
             ], fill=LIGHT, title_color=BLUE)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(2.7),
             "检索", [
                 "HyDE：短查询先扩写再检索",
                 "LLM Rerank：候选精排",
                 "Schema Linking：直接命中相关表和字段",
             ], fill=LIGHT, title_color=BLUE)
    add_card(s, Inches(0.7), Inches(4.45), Inches(12.0), Inches(2.1),
             "自学习", [
                 "成功 SQL 从工具层捕获，不从模型文本猜",
                 "质量门 + 真实库 dry-run 通过才回流",
                 "人工批准的 SQL 也回流，可信度更高",
             ], fill=LIGHT, title_color=BLUE)
    set_notes(s, "它不是一次性问答，而是越用越聪明：短期加长期记忆，HyDE 加 Rerank 精排，成功 SQL 自动回流样例库。")

    # 8 产品形态
    s = new_slide(prs)
    add_header(s, "怎么用", "CLI / Web / Streamlit / API", 8)
    add_card(s, Inches(0.7), Inches(1.55), Inches(3.9), Inches(2.7),
             "CLI", [
                 "安装即用",
                 "适合个人和快速验证",
             ], fill=LIGHT, title_color=NAVY)
    add_card(s, Inches(4.8), Inches(1.55), Inches(3.9), Inches(2.7),
             "Web", [
                 "SSE 流式思考过程",
                 "HITL 审批弹窗",
                 "图表 + 点赞回流",
             ], fill=LIGHT, title_color=NAVY)
    add_card(s, Inches(8.9), Inches(1.55), Inches(3.9), Inches(2.7),
             "API / 部署", [
                 "FastAPI 接口",
                 "Docker Compose",
                 "状态可外置 Redis",
             ], fill=LIGHT, title_color=NAVY)
    add_text(s, Inches(0.7), Inches(4.5), Inches(12.0), Inches(1.2),
             [("可接入方式灵活：既能做交互产品，也能作为企业数据问答中间件嵌入现有系统。", 0, 14, True, NAVY)])
    set_notes(s, "产品有三种形态：CLI 快速验证、Web 完整演示、API 可嵌入现有系统。部署用 Docker，状态可外置 Redis。")

    # 9 适用场景
    s = new_slide(prs)
    add_header(s, "能用在哪", "四个典型场景", 9)
    scenarios = [
        ("业务自助取数", "销售、财务、HR 一句话查数"),
        ("数据质量预检", "查数前先扫 NULL / 日期 / 异常值"),
        ("指标口径查询", "GMV 怎么算、退款扣不扣"),
        ("趋势分析", "跨地区、跨时间对比 + 图表"),
    ]
    for i, (t, d) in enumerate(scenarios):
        col = i % 2
        row = i // 2
        bx = Inches(0.7 + col * 6.1)
        by = Inches(1.55 + row * 2.1)
        add_rect(s, bx, by, Inches(5.85), Inches(1.85), fill=LIGHT)
        add_text(s, bx + Inches(0.3), by + Inches(0.25), Inches(5.2), Inches(0.5),
                 [(t, 0, 18, True, NAVY)])
        add_text(s, bx + Inches(0.3), by + Inches(0.85), Inches(5.2), Inches(0.8),
                 [(d, 0, 14, False, MUTED)])
    set_notes(s, "典型场景四个：业务自助取数、数据质量预检、指标口径查询、趋势分析。")

    # 10 数据说话
    s = new_slide(prs)
    add_header(s, "用数字说话", "不是 Demo，是可验证的系统", 10)
    metrics = [
        ("435", "离线测试用例"),
        ("47 + 30", "Eval + Golden Set"),
        ("6", "专职 Agent"),
        ("3", "入口形态"),
        ("3", "数据源类型"),
        ("100%", "离线零 API 可跑"),
    ]
    for i, (num, label) in enumerate(metrics):
        col = i % 3
        row = i // 3
        bx = Inches(0.7 + col * 4.1)
        by = Inches(1.6 + row * 1.9)
        add_rect(s, bx, by, Inches(3.85), Inches(1.6), fill=LIGHT)
        add_text(s, bx, by + Inches(0.25), Inches(3.85), Inches(0.6),
                 [(num, 0, 26, True, BLUE)], align=PP_ALIGN.CENTER)
        add_text(s, bx, by + Inches(0.95), Inches(3.85), Inches(0.5),
                 [(label, 0, 13, False, MUTED)], align=PP_ALIGN.CENTER)
    set_notes(s, "用数字说明它不是 Demo：435 条离线测试、47 条 Eval 加 30 条 Golden Set、6 个专职 Agent、三种入口、三类数据源。")

    # 11 对比
    s = new_slide(prs)
    add_header(s, "和市面 Text-to-SQL 比", "差在“执行之后”", 11)
    add_card(s, Inches(0.7), Inches(1.55), Inches(5.9), Inches(4.6),
             "市面工具", [
                 "生成 SQL",
                 "不管执行",
                 "不鉴权",
                 "不会自愈",
                 "对错无评测",
             ], fill=LIGHT, title_color=RED)
    add_card(s, Inches(6.8), Inches(1.55), Inches(5.9), Inches(4.6),
             "db-agent", [
                 "生成 + 真实执行",
                 "RBAC 权限硬拦截",
                 "HITL 人工审批",
                 "三层自愈 + 熔断",
                 "评测 + 自学习闭环",
             ], fill=LIGHT, title_color=GREEN)
    set_notes(s, "和市面 Text-to-SQL 比，我们差在“执行之后”：执行、权限、审批、自愈、评测，这些才是生产真正需要的。")

    # 12 下一步
    s = new_slide(prs)
    add_header(s, "下一步", "怎么合作", 12)
    add_card(s, Inches(0.7), Inches(1.55), Inches(3.9), Inches(2.7),
             "接真实数据源", [
                 "PostgreSQL / HBase / Hive",
                 "真实连接器替换模拟器",
             ], fill=LIGHT, title_color=BLUE)
    add_card(s, Inches(4.8), Inches(1.55), Inches(3.9), Inches(2.7),
             "私有化部署", [
                 "数据不出域",
                 "本地 Embedding / Rerank",
             ], fill=LIGHT, title_color=BLUE)
    add_card(s, Inches(8.9), Inches(1.55), Inches(3.9), Inches(2.7),
             "业务定制", [
                 "指标口径注册",
                 "权限模型定制",
                 "评测集共建",
             ], fill=LIGHT, title_color=BLUE)
    add_text(s, Inches(0.7), Inches(4.5), Inches(12.0), Inches(1.2),
             [("可以从一个 POC 开始：选一个业务场景，一周内看到“一句话查数 + 安全审批 + 可评测”。", 0, 14, True, NAVY)])
    set_notes(s, "合作方式：可以先做一个 POC，选一个业务场景，一周内看到一句话查数、安全审批、可评测的效果。")

    # 13 结尾
    s = new_slide(prs)
    add_rect(s, 0, 0, SW, SH, fill=NAVY)
    add_rect(s, 0, Inches(4.6), SW, Inches(0.06), fill=ORANGE)
    add_text(s, Inches(1.0), Inches(2.6), Inches(11.3), Inches(1.0),
             [("谢谢 · Q&A", 0, 48, True, WHITE)])
    add_text(s, Inches(1.0), Inches(4.0), Inches(11.3), Inches(0.6),
             [("db-agent：让业务用一句话查数，安全、准确、可审计", 0, 18, True, TEAL)])
    add_text(s, Inches(1.0), Inches(6.2), Inches(11.3), Inches(0.5),
             [("联系 / 演示信息占位", 0, 13, False, MUTED)])
    set_notes(s, "谢谢大家，欢迎提问。如果感兴趣，我们可以约一个十分钟的现场演示。")

    out = "docs/项目介绍/db-agent-产品介绍.pptx"
    prs.save(out)
    print(f"已生成: {out}")


if __name__ == "__main__":
    build()
