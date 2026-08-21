# tools/template_matcher.py — SQL 模板优先匹配器
#
# 三层策略：
#   1. 模板命中 → 直接填槽返回 SQL（< 1ms，不调 LLM）
#   2. 模板未命中 → 返回 None → 回退 LLM 自由生成
#   3. 模板命中但填槽失败 → 返回 None → 回退 LLM
#
# 设计决策：
#   - 模板匹配不调 LLM，纯关键词 + 实体提取，零延迟零成本
#   - 只覆盖高频确定性查询（单表聚合、简单多表 JOIN）
#   - 复杂查询不强行模板化，直接回退 LLM

import re
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from harness.tools import tool

DB_DIR = Path(__file__).resolve().parents[2] / "db"
METRIC_DB = DB_DIR / "metric_registry.db"


# ── 日期槽位解析 ──

def _parse_date_range(query: str) -> tuple[str, str]:
    """从 query 中提取日期范围，返回 (start_date, end_date)，ISO 格式。"""
    today = datetime.now().date()

    patterns = [
        (r"(今天|今日)", (today, today)),
        (r"(昨天|昨日)", (today - timedelta(days=1), today - timedelta(days=1))),
        (r"最近\s*(\d+)\s*天", lambda m: (today - timedelta(days=int(m.group(1))), today)),
        (r"最近\s*(\d+)\s*个月", lambda m: (
            today.replace(day=1) - timedelta(days=1),  # 粗略处理
            today)),
        (r"(上个月|上月)", (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1),
            today.replace(day=1) - timedelta(days=1))),
        (r"本周", (today - timedelta(days=today.weekday()), today)),
        (r"上周", (
            today - timedelta(days=today.weekday() + 7),
            today - timedelta(days=today.weekday() + 1))),
        (r"(Q\d|第\d季度)", (today.replace(month=1, day=1), today)),
    ]

    for pattern, handler in patterns:
        m = re.search(pattern, query)
        if m:
            if callable(handler):
                start, end = handler(m)
            else:
                start, end = handler
            return (start.isoformat(), end.isoformat())

    # 默认最近 30 天
    return (
        (today - timedelta(days=30)).isoformat(),
        today.isoformat(),
    )


# ── 实体槽位解析（关键词匹配，不调 LLM）──

_ENTITY_EXTRACTORS: dict[str, callable] = {}


def _extract_dept(query: str) -> str | None:
    """从 query 中提取部门名。"""
    dept_map = {
        "销售": "销售部", "市场": "市场部", "研发": "研发部",
        "人事": "人事部", "财务": "财务部", "运营": "运营部",
    }
    for keyword, full_name in dept_map.items():
        if keyword in query:
            return full_name
    return None


def _extract_status(query: str) -> str | None:
    """从 query 中提取状态值。"""
    status_map = {
        "已付款": "paid", "付款": "paid", "已支付": "paid",
        "已发货": "shipped", "发货": "shipped",
        "已取消": "cancelled", "取消": "cancelled",
        "待处理": "pending", "待付款": "pending",
    }
    for keyword, value in status_map.items():
        if keyword in query:
            return value
    return None


def _extract_limit(query: str) -> int:
    """从 query 中提取 TOP N。"""
    m = re.search(r"(前|top\s*|TOP\s*|排名前)\s*(\d+)", query)
    if m:
        return int(m.group(2))
    m = re.search(r"(\d+)\s*(个|名|条)", query)
    if m:
        return int(m.group(1))
    return 10


def _extract_table_name(query: str) -> str | None:
    """从 query 中提取表名。"""
    known_tables = [
        "orders", "employees", "departments", "products",
        "customers", "order_items", "regions",
    ]
    for t in known_tables:
        if t.lower() in query.lower():
            return t
    # 尝试匹配中文 "XX 表"
    cn_map = {
        "订单": "orders", "员工": "employees", "部门": "departments",
        "产品": "products", "客户": "customers",
    }
    for cn, en in cn_map.items():
        if cn in query:
            return en
    return None


def _extract_product(query: str) -> str | None:
    """从 query 中提取产品名。"""
    products = ["企业版", "标准版", "基础版", "SaaS", "专业版"]
    for p in products:
        if p in query:
            return p
    return None


@dataclass
class MetricTemplate:
    """一个业务指标模板。"""
    metric_name: str
    sql_template: str
    aliases: list[str] = field(default_factory=list)
    placeholder_defs: dict = field(default_factory=dict)
    related_tables: list[str] = field(default_factory=list)
    domain: str = ""
    caveats: str = ""
    usage_count: int = 0

    def match_score(self, query: str) -> float:
        """计算 query 与模板的匹配度 (0.0 ~ 1.0)。"""
        query_lower = query.lower()
        score = 0.0

        # 精确名称匹配: 模板名或别名作为整体子串出现在 query 中
        exact_patterns = [self.metric_name] + self.aliases
        for pattern in exact_patterns:
            if pattern in query:
                score += 0.8
                return min(score, 1.0)

        # n-gram 关键词匹配: 单字 + 双字 + 三字词
        all_text = self.metric_name + "".join(self.aliases)
        keywords = set()
        # 单字（筛掉标点和虚词）
        skip_chars = set("的了吗呢啊吧是哪有什么这不都就很也")
        for ch in all_text:
            if ch not in skip_chars:
                keywords.add(ch)
        # 双字词
        for i in range(len(all_text) - 1):
            keywords.add(all_text[i:i+2])
        # 三字词
        for i in range(len(all_text) - 2):
            keywords.add(all_text[i:i+3])

        if keywords:
            hits = sum(1 for kw in keywords if kw in query)
            hit_rate = hits / len(keywords)
            score += hit_rate * 0.5

        # 表名命中: +0.2
        for table in self.related_tables:
            if table.lower() in query_lower:
                score += 0.2
                break

        # 领域词命中: +0.15
        domain_terms = {
            "sales": ["销售", "订单", "产品", "客户", "营收", "销量", "卖"],
            "hr": ["员工", "部门", "工资", "薪资", "人事", "人"],
            "meta": ["表", "结构", "有哪些", "概览"],
        }
        domain_hits = domain_terms.get(self.domain, [])
        matched_domain_terms = [t for t in domain_hits if t in query]
        if matched_domain_terms:
            score += 0.15 * min(len(matched_domain_terms) / 3, 1.0)

        return min(score, 1.0)

    def fill(self, query: str) -> str | None:
        """尝试用 query 填充模板槽位，返回完整 SQL；填不了返回 None。"""
        sql = self.sql_template
        placeholders = set(re.findall(r"\{(\w+)\}", sql))

        for ph in placeholders:
            value = self._resolve_placeholder(ph, query)
            if value is None:
                return None
            sql = sql.replace(f"{{{ph}}}", str(value))

        return sql

    def _resolve_placeholder(self, name: str, query: str) -> str | None:
        """解析单个槽位。"""
        # 日期类
        if name in ("start_date", "date_from"):
            return _parse_date_range(query)[0]
        if name in ("end_date", "date_to"):
            return _parse_date_range(query)[1]
        if name == "date_range_start":
            return _parse_date_range(query)[0]
        if name == "date_range_end":
            return _parse_date_range(query)[1]

        # 实体类
        if name == "dept_name":
            return _extract_dept(query)
        if name == "status_value":
            return _extract_status(query)
        if name == "product_name":
            return _extract_product(query)

        # 数值类
        if name == "limit":
            return str(_extract_limit(query))
        if name == "table_name":
            return _extract_table_name(query)

        # 从 placeholder_defs 取默认值
        if name in self.placeholder_defs:
            return self.placeholder_defs[name]

        return None


# ── 数据库操作 ──

def _get_conn() -> sqlite3.Connection:
    """获取 metric_registry 数据库连接。"""
    conn = sqlite3.connect(str(METRIC_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_metric_registry():
    """初始化 metric_registry 表和种子数据。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metric_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL UNIQUE,
            aliases TEXT DEFAULT '',
            sql_template TEXT NOT NULL,
            placeholder_defs TEXT DEFAULT '{}',
            related_tables TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            caveats TEXT DEFAULT '',
            usage_count INTEGER DEFAULT 0
        )
    """)

    # 种子模板
    existing = conn.execute("SELECT COUNT(*) FROM metric_registry").fetchone()[0]
    if existing == 0:
        _seed_templates(conn)

    conn.commit()
    return conn


def _seed_templates(conn: sqlite3.Connection):
    """预置高频业务指标模板。"""
    templates = [
        ("部门销售额", "销售部门总销售额",
         "SELECT d.name, SUM(o.total) AS sales FROM orders o JOIN departments d ON o.dept_id = d.id WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY d.name ORDER BY sales DESC",
         "sales",
         "不含退款订单"),
        ("月销售额", "月度销售额,月营收",
         "SELECT SUM(o.total) AS monthly_sales FROM orders o WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}'",
         "sales",
         "不含已取消订单"),
        ("部门平均工资", "部门薪资,各部门工资",
         "SELECT d.name, AVG(e.salary) AS avg_salary FROM employees e JOIN departments d ON e.dept_id = d.id GROUP BY d.name",
         "hr",
         ""),
        ("订单状态分布", "各状态订单数,订单统计",
         "SELECT o.status, COUNT(*) AS count, SUM(o.total) AS amount FROM orders o WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY o.status ORDER BY amount DESC",
         "sales",
         ""),
        ("员工列表", "所有员工,员工清单",
         "SELECT e.name, d.name AS department, e.salary FROM employees e JOIN departments d ON e.dept_id = d.id ORDER BY e.name",
         "hr",
         ""),
        ("部门员工数", "各部门人数,部门规模",
         "SELECT d.name, COUNT(e.id) AS employee_count FROM departments d LEFT JOIN employees e ON e.dept_id = d.id GROUP BY d.name ORDER BY employee_count DESC",
         "hr",
         ""),
        ("某部门员工", "部门员工列表,某部门有哪些人",
         "SELECT e.name, e.position, e.salary FROM employees e JOIN departments d ON e.dept_id = d.id WHERE d.name = '{dept_name}' ORDER BY e.name",
         "hr",
         ""),
        ("产品销售排名", "产品销量,哪个产品卖得好",
         "SELECT p.name, SUM(oi.quantity) AS sold, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi JOIN products p ON oi.product_id = p.id JOIN orders o ON oi.order_id = o.id WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY p.name ORDER BY sold DESC LIMIT {limit}",
         "sales",
         ""),
        ("客户订单排行", "客户排名,哪个客户买得多",
         "SELECT c.name, COUNT(o.id) AS order_count, SUM(o.total) AS total_spent FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY c.name ORDER BY total_spent DESC LIMIT {limit}",
         "sales",
         ""),
        ("数据概览", "数据库概览,有哪些表,整体结构",
         "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_%' ORDER BY name",
         "meta",
         "返回所有非系统表名"),
        ("表结构查询", "表有哪些字段,表结构,describe,字段列表",
         "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table_name}' ORDER BY ordinal_position",
         "meta",
         "注意：SQLite 用 PRAGMA table_info({table_name}) 代替"),
        ("订单量对比", "对比订单量,订单量变化,本月和上月订单",
         "SELECT CASE WHEN o.created_at >= '{date_range_start}' THEN '本期' ELSE '上期' END AS period, COUNT(*) AS order_count, SUM(o.total) AS total_amount FROM orders o WHERE o.created_at BETWEEN '{date_range_start}' AND '{end_date}' GROUP BY period ORDER BY period DESC",
         "sales",
         "对比两个时期的订单量变化"),
        ("单部门销售额", "某部门销售额,华东销售,某个地区销售",
         "SELECT d.name, SUM(o.total) AS sales FROM orders o JOIN departments d ON o.dept_id = d.id WHERE d.name = '{dept_name}' AND o.created_at BETWEEN '{start_date}' AND '{end_date}' GROUP BY d.name",
         "sales",
         ""),
    ]

    for name, aliases, sql, domain, caveats in templates:
        conn.execute(
            "INSERT INTO metric_registry (metric_name, aliases, sql_template, domain, caveats) VALUES (?, ?, ?, ?, ?)",
            (name, aliases, sql, domain, caveats),
        )


# ── 匹配器 ──

@dataclass
class MatchResult:
    """模板匹配结果。"""
    matched: bool
    sql: str | None = None
    metric_name: str | None = None
    caveats: str | None = None
    from_template: bool = False


class TemplateMatcher:
    """SQL 模板优先匹配器。

    用法:
        matcher = TemplateMatcher()
        result = matcher.match("上个月各部门的销售额")
        if result.matched:
            sql = result.sql  # 直接执行
        else:
            sql = llm_generate(...)  # 回退 LLM
    """

    # 匹配阈值：偏宽松，宁可多匹配让 Agent 判断，也不漏掉模板
    MATCH_THRESHOLD = 0.15

    def __init__(self):
        self._templates: list[MetricTemplate] = []
        self._loaded = False

    def _load(self):
        """延迟加载模板（首次匹配时从 DB 加载）。"""
        if self._loaded:
            return
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM metric_registry ORDER BY usage_count DESC"
        ).fetchall()
        for row in rows:
            self._templates.append(MetricTemplate(
                metric_name=row["metric_name"],
                sql_template=row["sql_template"],
                aliases=[a.strip() for a in row["aliases"].split(",") if a.strip()],
                placeholder_defs=json.loads(row["placeholder_defs"] or "{}"),
                related_tables=[t.strip() for t in row["related_tables"].split(",") if t.strip()],
                domain=row["domain"] or "",
                caveats=row["caveats"] or "",
                usage_count=row["usage_count"] or 0,
            ))
        self._loaded = True

    def match(self, query: str) -> MatchResult:
        """匹配 query 到模板，命中则返回填槽后的 SQL。"""
        self._load()
        if not self._templates:
            return MatchResult(matched=False)

        # 计算所有模板得分，取最高
        scores = [(t, t.match_score(query)) for t in self._templates]
        scores.sort(key=lambda x: x[1], reverse=True)
        best_template, best_score = scores[0]

        if best_score < self.MATCH_THRESHOLD:
            return MatchResult(matched=False)

        # 尝试填槽
        sql = best_template.fill(query)
        if sql is None:
            # 模板命中但填槽失败 → 回退 LLM
            return MatchResult(matched=False)

        # 更新使用计数
        self._increment_usage(best_template.metric_name)

        return MatchResult(
            matched=True,
            sql=sql,
            metric_name=best_template.metric_name,
            caveats=best_template.caveats or None,
            from_template=True,
        )

    def _increment_usage(self, metric_name: str):
        """更新模板使用计数。"""
        try:
            conn = _get_conn()
            conn.execute(
                "UPDATE metric_registry SET usage_count = usage_count + 1 WHERE metric_name = ?",
                (metric_name,),
            )
            conn.commit()
        except Exception:
            pass  # 计数更新失败不影响主流程

    def list_templates(self) -> list[dict]:
        """列出所有模板（供调试和运维）。"""
        self._load()
        return [
            {
                "name": t.metric_name,
                "aliases": t.aliases,
                "domain": t.domain,
                "usage_count": t.usage_count,
            }
            for t in self._templates
        ]

    def add_template(self, t: MetricTemplate):
        """动态添加模板。"""
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO metric_registry (metric_name, aliases, sql_template, placeholder_defs, related_tables, domain, caveats) VALUES (?,?,?,?,?,?,?)",
            (
                t.metric_name,
                ",".join(t.aliases),
                t.sql_template,
                json.dumps(t.placeholder_defs, ensure_ascii=False),
                ",".join(t.related_tables),
                t.domain,
                t.caveats,
            ),
        )
        conn.commit()
        self._templates.append(t)
        self._templates.sort(key=lambda x: x.usage_count, reverse=True)


# ── 单例 ──
_matcher: TemplateMatcher | None = None


def get_template_matcher() -> TemplateMatcher:
    global _matcher
    if _matcher is None:
        _matcher = TemplateMatcher()
    return _matcher


# ── Agent Tool: match_sql_template ──
# 注册为 @tool，Agent 可以主动调用来尝试模板匹配。
# 用法：Agent 在生成 SQL 之前先调用此 Tool，如果命中则直接用，
#       未命中则自行生成。


@tool(description="尝试用预置 SQL 模板匹配用户查询。命中会返回预填 SQL（可直接执行），未命中返回提示让 Agent 自行生成。建议在生成 SQL 之前优先调用。")
def match_sql_template(query: str) -> dict:
    """query: 用户的自然语言查询"""
    matcher = get_template_matcher()
    result = matcher.match(query)
    if result.matched:
        return {
            "matched": True,
            "sql": result.sql,
            "metric": result.metric_name,
            "caveats": result.caveats or "",
            "hint": "可直接执行此 SQL，或根据具体情况调整后执行",
        }
    return {
        "matched": False,
        "hint": "模板未命中，请基于 schema 自行生成 SQL",
    }
