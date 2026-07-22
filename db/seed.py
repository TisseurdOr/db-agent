# db/seed.py — 示例数据库初始化
#
# 扩数据的原因：原来 3 个部门 + 5 条订单只能做基本的 select 验证，
# 面试时说不出"趋势分析"、"同比环比"、"多维下钻"这类词。
# 现在 6 部门 + 200+ 订单（跨 14 个月）+ 40 员工 + 15 产品 + 12 客户，
# Agent 能做时间序列对比、部门绩效排名、产品动销分析、地区/行业下钻。

import sqlite3
import os
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "demo.db")


def init_db(reset: bool = False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        -- 权限系统表（Agent 工具层读取，不可被 Agent 查询）
        CREATE TABLE IF NOT EXISTS agent_roles (
            role TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            allowed_tools TEXT NOT NULL,
            db_tables TEXT,
            db_row_filter TEXT,
            docs_filter TEXT,
            sensitive_check INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS agent_users (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL REFERENCES agent_roles(role),
            dept_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            budget REAL,
            headcount INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            dept_id INTEGER REFERENCES departments(id),
            title TEXT NOT NULL,
            salary REAL,
            hire_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit_price REAL NOT NULL,
            cost REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            region TEXT NOT NULL,
            city TEXT NOT NULL,
            industry TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'B'
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            dept_id INTEGER REFERENCES departments(id),
            product_id INTEGER REFERENCES products(id),
            customer_id INTEGER REFERENCES customers(id),
            total REAL NOT NULL,
            quantity INTEGER DEFAULT 1,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

""")

    # 用户记忆表从独立 SQL 文件加载（课程 0017 要求）
    user_memory_sql = os.path.join(os.path.dirname(__file__), "user_memory.sql")
    with open(user_memory_sql) as f:
        conn.executescript(f.read())

    conn.executescript("""

        DELETE FROM orders;
        DELETE FROM employees;
        DELETE FROM products;
        DELETE FROM customers;
        DELETE FROM departments;
        DELETE FROM user_memory;
        DELETE FROM agent_users;
        DELETE FROM agent_roles;

        -- agent_roles: 5 种角色
        INSERT INTO agent_roles VALUES ('dba',     '研发DBA',  '["run_query","list_tables","describe_table","search_knowledge_base","read_document","write_query"]', null, null, null, 0);
        INSERT INTO agent_roles VALUES ('manager', '部门经理',  '["run_query","list_tables","describe_table","search_knowledge_base","read_document"]', null, '{"employees":"dept_id"}', null, 1);
        INSERT INTO agent_roles VALUES ('analyst', '数据分析师','["run_query","list_tables","describe_table","search_knowledge_base","read_document"]', '["departments","employees","products","customers","orders"]', null, null, 1);
        INSERT INTO agent_roles VALUES ('viewer',  '访客',      '["list_tables","describe_table","search_knowledge_base","read_document"]', '["departments","products","customers","orders"]', null, '["产品手册","部门介绍","销售制度"]', 0);
        INSERT INTO agent_roles VALUES ('support', '技术支持',  '["run_query","list_tables","describe_table","search_knowledge_base","read_document"]', '["products","customers","orders"]', null, '["技术文档","产品手册"]', 0);

        -- agent_users: 10 个用户
        INSERT INTO agent_users VALUES ('dba',        '研发DBA',  'dba',     3);
        INSERT INTO agent_users VALUES ('zhoufang',   '周芳',     'manager', 1);
        INSERT INTO agent_users VALUES ('xiaoyiming', '萧一鸣',   'manager', 2);
        INSERT INTO agent_users VALUES ('gaoyong',    '高勇',     'manager', 3);
        INSERT INTO agent_users VALUES ('linyi',      '林怡',     'manager', 4);
        INSERT INTO agent_users VALUES ('liangming',  '梁明',     'manager', 5);
        INSERT INTO agent_users VALUES ('lujie',      '卢杰',     'manager', 6);
        INSERT INTO agent_users VALUES ('analyst',    '数据分析师','analyst', null);
        INSERT INTO agent_users VALUES ('viewer',     '访客',     'viewer',  null);
        INSERT INTO agent_users VALUES ('support',    '技术支持', 'support',  null);

        -- departments: 6 个
        INSERT INTO departments VALUES (1, '销售部', 1000000, 8);
        INSERT INTO departments VALUES (2, '市场部', 800000, 6);
        INSERT INTO departments VALUES (3, '研发部', 1500000, 10);
        INSERT INTO departments VALUES (4, '财务部', 400000, 4);
        INSERT INTO departments VALUES (5, '人事部', 350000, 3);
        INSERT INTO departments VALUES (6, '产品部', 700000, 5);

        -- products: 15 个（3 个品类 × 5 个产品）
        INSERT INTO products VALUES (1, '企业版SaaS订阅', '软件', 50000, 15000);
        INSERT INTO products VALUES (2, '专业版SaaS订阅', '软件', 20000, 6000);
        INSERT INTO products VALUES (3, '基础版SaaS订阅', '软件', 5000, 1500);
        INSERT INTO products VALUES (4, '定制开发服务', '软件', 150000, 90000);
        INSERT INTO products VALUES (5, '技术咨询服务', '软件', 30000, 18000);
        INSERT INTO products VALUES (6, '数据分析平台', '硬件', 80000, 50000);
        INSERT INTO products VALUES (7, '服务器运维服务', '硬件', 40000, 20000);
        INSERT INTO products VALUES (8, '云存储套餐', '硬件', 15000, 5000);
        INSERT INTO products VALUES (9, '网络安全方案', '硬件', 60000, 35000);
        INSERT INTO products VALUES (10, 'IoT设备套件', '硬件', 25000, 12000);
        INSERT INTO products VALUES (11, '企业培训课程', '服务', 10000, 3000);
        INSERT INTO products VALUES (12, '项目管理咨询', '服务', 45000, 25000);
        INSERT INTO products VALUES (13, '品牌设计套餐', '服务', 35000, 18000);
        INSERT INTO products VALUES (14, '市场调研报告', '服务', 20000, 8000);
        INSERT INTO products VALUES (15, '售后技术支持', '服务', 8000, 4000);

        -- customers: 12 个（4 个地区 × 3 个行业，S/A/B 三级）
        INSERT INTO customers VALUES (1,  '字节跳动', '华北', '北京', '互联网', 'S');
        INSERT INTO customers VALUES (2,  '阿里巴巴', '华东', '杭州', '互联网', 'S');
        INSERT INTO customers VALUES (3,  '招商银行', '华南', '深圳', '金融', 'A');
        INSERT INTO customers VALUES (4,  '中国平安', '华南', '深圳', '金融', 'S');
        INSERT INTO customers VALUES (5,  '美团', '华北', '北京', '互联网', 'A');
        INSERT INTO customers VALUES (6,  '比亚迪', '华南', '深圳', '制造业', 'S');
        INSERT INTO customers VALUES (7,  '三一重工', '华中', '长沙', '制造业', 'A');
        INSERT INTO customers VALUES (8,  '蚂蚁集团', '华东', '上海', '金融', 'S');
        INSERT INTO customers VALUES (9,  '格力电器', '华南', '珠海', '制造业', 'A');
        INSERT INTO customers VALUES (10, '小红书', '华东', '上海', '互联网', 'B');
        INSERT INTO customers VALUES (11, '中信证券', '华北', '北京', '金融', 'A');
        INSERT INTO customers VALUES (12, '中联重科', '华中', '武汉', '制造业', 'B');
    """)

    random.seed(42)

    # ── employees: 40 人 ──
    titles_pool = {
        "销售部": ["销售总监", "大客户经理", "大客户经理", "销售代表", "销售代表",
                  "销售代表", "销售助理", "销售助理"],
        "市场部": ["市场总监", "品牌经理", "市场专员", "市场专员", "市场专员",
                  "市场专员"],
        "研发部": ["技术总监", "高级工程师", "高级工程师", "高级工程师",
                  "前端工程师", "前端工程师", "后端工程师", "后端工程师",
                  "后端工程师", "测试工程师", "测试工程师", "运维工程师"],
        "财务部": ["财务总监", "会计", "会计", "出纳"],
        "人事部": ["人事总监", "招聘经理", "HR专员", "HR专员"],
        "产品部": ["产品总监", "产品经理", "产品经理", "UX设计师", "UX设计师"],
    }
    surnames = ["张", "李", "王", "赵", "陈", "刘", "黄", "周", "吴", "杨",
                "朱", "马", "胡", "郭", "何", "高", "林", "郑", "罗", "梁",
                "宋", "唐", "许", "韩", "冯", "邓", "曹", "彭", "曾", "萧",
                "沈", "孙", "徐", "苏", "卢", "蒋", "蔡", "丁", "魏", "程"]
    given = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋",
             "勇", "艳", "杰", "娟", "涛", "明", "超", "秀兰", "霞", "平",
             "刚", "桂英", "文", "华", "建华", "玉兰", "建平", "志强", "宇", "欣",
             "浩", "辰", "怡", "思远", "一鸣", "雨桐", "睿", "梓涵", "博文", "晓峰"]

    emp_id = 1
    for dept_id, dept_name in enumerate(
        ["销售部", "市场部", "研发部", "财务部", "人事部", "产品部"], start=1
    ):
        for title in titles_pool[dept_name]:
            name = random.choice(surnames) + random.choice(given)
            if "总监" in title:
                salary = random.randint(35000, 50000)
            elif "经理" in title or "高级" in title or "资深" in title:
                salary = random.randint(20000, 38000)
            elif "工程师" in title or "设计师" in title:
                salary = random.randint(15000, 32000)
            else:
                salary = random.randint(8000, 18000)

            days_ago = random.randint(30, 2200)
            hire_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
            status = "inactive" if random.random() < 0.08 else "active"

            conn.execute(
                "INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
                (emp_id, name, dept_id, title, salary, hire_date, status),
            )
            emp_id += 1

    # ── orders: 跨 2025-06-01 到 2026-07-15，~220 条 ──
    statuses = ["completed", "pending", "cancelled"]
    status_weights = [0.60, 0.28, 0.12]

    # 不同品类的季节性：年底采购硬件多，Q1 软件续费多，服务类稳定
    product_category = {
        1: "软件", 2: "软件", 3: "软件", 4: "软件", 5: "软件",
        6: "硬件", 7: "硬件", 8: "硬件", 9: "硬件", 10: "硬件",
        11: "服务", 12: "服务", 13: "服务", 14: "服务", 15: "服务",
    }

    order_id = 1
    start_date = datetime(2025, 6, 1)
    end_date = datetime(2026, 7, 15)
    total_days = (end_date - start_date).days

    for day_offset in range(total_days):
        date = start_date + timedelta(days=day_offset)
        month = date.month

        # 每月 15-25 单，Q4 和 Q2 偏多
        base_orders = 0.55
        if month in (6, 12):     # 年中/年末冲业绩
            base_orders = 0.8
        elif month in (1, 2):    # 春节淡季
            base_orders = 0.3

        for _ in range(random.choices([0, 1, 2], weights=[1 - base_orders, base_orders * 0.7, base_orders * 0.3])[0]):
            dept_id = random.randint(1, 6)
            customer_id = random.randint(1, 12)
            product_id = random.randint(1, 15)

            base_prices = [50000, 20000, 5000, 150000, 30000, 80000, 40000,
                          15000, 60000, 25000, 10000, 45000, 35000, 20000, 8000]
            total = round(base_prices[product_id - 1] * random.uniform(0.7, 1.4), -2)
            quantity = random.choices([1, 2, 3, 5], weights=[0.4, 0.3, 0.2, 0.1])[0]
            status = random.choices(statuses, weights=status_weights)[0]

            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, dept_id, product_id, customer_id, total, quantity, status,
                 date.strftime("%Y-%m-%d")),
            )
            order_id += 1

    conn.commit()

    # 统计
    emp_count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    cust_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    conn.close()

    print(f"数据库已初始化: {DB_PATH}")
    print(f"  departments: 6, employees: {emp_count}, products: 15, customers: {cust_count}, orders: {order_count}")
    print(f"  时间范围: 2025-06-01 ~ 2026-07-15")
    print(f"  新特性: customers 表（地区 + 行业维度），orders 含季节性波动")


if __name__ == "__main__":
    init_db(reset=True)
