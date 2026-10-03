# db/seed.py — 示例数据库初始化
#
# 扩 SQL 数据：客户 12→60（5 地区 × 6 行业）、产品 15→36（4 品类）、
# 订单 400→~8000，并补 channels 维度（orders 加 channel_id）。
# departments/employees 保持不变，避免牵连依赖它们的评测用例与权限演示。
# Hive/HBase 保持原样，本次不动。

import json
import os
import random
import sqlite3
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

        CREATE TABLE IF NOT EXISTS user_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            query TEXT NOT NULL,
            answer TEXT NOT NULL,
            rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
            comment TEXT DEFAULT '',
            sql TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            owner TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            dept_id INTEGER REFERENCES departments(id),
            product_id INTEGER REFERENCES products(id),
            customer_id INTEGER REFERENCES customers(id),
            channel_id INTEGER REFERENCES channels(id),
            total REAL NOT NULL,
            quantity INTEGER DEFAULT 1,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id),
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'paid',
            paid_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id),
            carrier TEXT NOT NULL,
            tracking_no TEXT,
            status TEXT NOT NULL DEFAULT 'shipped',
            shipped_at TEXT NOT NULL,
            delivered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY,
            product_id INTEGER REFERENCES products(id),
            warehouse TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            reorder_level INTEGER DEFAULT 10
        );

        -- Hive 风格数仓表（模拟 Hive/Impala 查询环境）
        -- 分区列 dt/region 作为普通列存储，复杂类型用 JSON 文本列
        CREATE TABLE IF NOT EXISTS ods_orders_hive (
            dt TEXT NOT NULL,
            region TEXT NOT NULL,
            order_id TEXT NOT NULL,
            customer_id INTEGER,
            product_id INTEGER,
            total REAL,
            quantity INTEGER DEFAULT 1,
            status TEXT,
            created_at TEXT,
            store_format TEXT DEFAULT 'PARQUET'
        );

        CREATE TABLE IF NOT EXISTS dwd_user_events (
            dt TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_props TEXT,
            event_time TEXT NOT NULL,
            store_format TEXT DEFAULT 'ORC'
        );

        CREATE TABLE IF NOT EXISTS dim_products_hive (
            product_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit_price REAL,
            supplier TEXT,
            tags TEXT,
            store_format TEXT DEFAULT 'PARQUET'
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
        DELETE FROM channels;
        DELETE FROM departments;
        DELETE FROM user_memory;
        DELETE FROM user_feedback;
        DELETE FROM agent_users;
        DELETE FROM agent_roles;
        DELETE FROM ods_orders_hive;
        DELETE FROM dwd_user_events;
        DELETE FROM dim_products_hive;
        DELETE FROM payments;
        DELETE FROM shipments;
        DELETE FROM inventory;

        -- agent_roles / agent_users 由 Python 侧按 _DEFAULT_ROLES 写入（见本函数末）

        -- departments: 6 个（保持不变）
        INSERT INTO departments VALUES (1, '销售部', 1000000, 8);
        INSERT INTO departments VALUES (2, '市场部', 800000, 6);
        INSERT INTO departments VALUES (3, '研发部', 1500000, 10);
        INSERT INTO departments VALUES (4, '财务部', 400000, 4);
        INSERT INTO departments VALUES (5, '人事部', 350000, 3);
        INSERT INTO departments VALUES (6, '产品部', 700000, 5);

        -- channels: 4 个渠道
        INSERT INTO channels VALUES (1, '直销', '销售部');
        INSERT INTO channels VALUES (2, '渠道伙伴', '销售部');
        INSERT INTO channels VALUES (3, '线上', '市场部');
        INSERT INTO channels VALUES (4, '会销', '市场部');
    """)

    random.seed(42)

    # ── products: 36 个（4 品类 × 9）──
    products = [
        # 软件
        (1, "企业版SaaS订阅", "软件", 50000, 15000),
        (2, "专业版SaaS订阅", "软件", 20000, 6000),
        (3, "基础版SaaS订阅", "软件", 5000, 1500),
        (4, "定制开发服务", "软件", 150000, 90000),
        (5, "技术咨询服务", "软件", 30000, 18000),
        (6, "数据中台", "软件", 120000, 70000),
        (7, "低代码平台", "软件", 80000, 45000),
        (8, "移动办公套件", "软件", 15000, 5000),
        (9, "数据治理平台", "软件", 90000, 50000),
        # 硬件
        (10, "服务器X1", "硬件", 35000, 20000),
        (11, "交换机S500", "硬件", 8000, 4000),
        (12, "路由器R200", "硬件", 2500, 1200),
        (13, "存储阵列", "硬件", 60000, 38000),
        (14, "边缘计算节点", "硬件", 20000, 11000),
        (15, "网络安全网关", "硬件", 45000, 26000),
        (16, "IoT网关", "硬件", 12000, 6000),
        (17, "工控机", "硬件", 18000, 10000),
        (18, "一体机", "硬件", 90000, 60000),
        # 服务
        (19, "企业培训课程", "服务", 10000, 3000),
        (20, "项目管理咨询", "服务", 45000, 25000),
        (21, "品牌设计套餐", "服务", 35000, 18000),
        (22, "市场调研报告", "服务", 20000, 8000),
        (23, "售后技术支持", "服务", 8000, 4000),
        (24, "人力外包服务", "服务", 50000, 35000),
        (25, "财务咨询服务", "服务", 25000, 12000),
        (26, "法务咨询服务", "服务", 30000, 15000),
        (27, "数据治理服务", "服务", 40000, 20000),
        # 云服务
        (28, "云主机套餐", "云服务", 12000, 5000),
        (29, "对象存储", "云服务", 8000, 3000),
        (30, "CDN加速", "云服务", 15000, 6000),
        (31, "云数据库", "云服务", 20000, 8000),
        (32, "容器服务", "云服务", 18000, 7000),
        (33, "大数据计算", "云服务", 35000, 15000),
        (34, "AI推理服务", "云服务", 25000, 10000),
        (35, "短信服务", "云服务", 5000, 2000),
        (36, "域名服务", "云服务", 3000, 1000),
    ]
    for pid, name, cat, price, cost in products:
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?)",
            (pid, name, cat, price, cost),
        )

    # ── customers: 60 个（5 地区 × 多行业，S/A/B 三级）──
    customer_rows = [
        # 互联网（12）
        (1, "字节跳动", "华北", "北京", "互联网", "S"),
        (2, "阿里巴巴", "华东", "杭州", "互联网", "S"),
        (5, "美团", "华北", "北京", "互联网", "A"),
        (10, "小红书", "华东", "上海", "互联网", "B"),
        (13, "拼多多", "华东", "上海", "互联网", "A"),
        (14, "快手", "华北", "北京", "互联网", "A"),
        (15, "网易", "华东", "杭州", "互联网", "B"),
        (16, "百度", "华北", "北京", "互联网", "S"),
        (17, "京东", "华北", "北京", "互联网", "S"),
        (18, "哔哩哔哩", "华东", "上海", "互联网", "B"),
        (19, "知乎", "华北", "北京", "互联网", "B"),
        (20, "得物", "华东", "上海", "互联网", "B"),
        # 金融（12）
        (3, "招商银行", "华南", "深圳", "金融", "A"),
        (4, "中国平安", "华南", "深圳", "金融", "S"),
        (8, "蚂蚁集团", "华东", "上海", "金融", "S"),
        (11, "中信证券", "华北", "北京", "金融", "A"),
        (21, "工商银行", "华北", "北京", "金融", "S"),
        (22, "建设银行", "华北", "北京", "金融", "S"),
        (23, "中国人寿", "华北", "北京", "金融", "A"),
        (24, "泰康保险", "华北", "北京", "金融", "B"),
        (25, "广发银行", "华南", "广州", "金融", "A"),
        (26, "民生银行", "华北", "北京", "金融", "B"),
        (27, "平安银行", "华南", "深圳", "金融", "A"),
        (28, "兴业银行", "华东", "上海", "金融", "B"),
        # 制造业（12）
        (6, "比亚迪", "华南", "深圳", "制造业", "S"),
        (7, "三一重工", "华中", "长沙", "制造业", "A"),
        (9, "格力电器", "华南", "珠海", "制造业", "A"),
        (12, "中联重科", "华中", "武汉", "制造业", "B"),
        (29, "宁德时代", "华东", "宁德", "制造业", "S"),
        (30, "海尔集团", "华东", "青岛", "制造业", "A"),
        (31, "美的集团", "华南", "佛山", "制造业", "A"),
        (32, "徐工机械", "华中", "徐州", "制造业", "B"),
        (33, "潍柴动力", "华北", "潍坊", "制造业", "B"),
        (34, "长城汽车", "华北", "保定", "制造业", "A"),
        (35, "三花智控", "华东", "杭州", "制造业", "B"),
        (36, "中芯国际", "华东", "上海", "制造业", "S"),
        # 零售（12）
        (37, "永辉超市", "华南", "福州", "零售", "A"),
        (38, "华润万家", "华南", "深圳", "零售", "A"),
        (39, "苏宁易购", "华东", "南京", "零售", "B"),
        (40, "名创优品", "华南", "广州", "零售", "B"),
        (41, "百果园", "华南", "深圳", "零售", "B"),
        (42, "良品铺子", "华中", "武汉", "零售", "B"),
        (43, "三只松鼠", "华东", "芜湖", "零售", "B"),
        (44, "蜜雪冰城", "华中", "郑州", "零售", "A"),
        (45, "瑞幸咖啡", "华北", "北京", "零售", "A"),
        (46, "盒马鲜生", "华东", "上海", "零售", "A"),
        (47, "海底捞", "西南", "成都", "零售", "A"),
        (48, "老凤祥", "华东", "上海", "零售", "B"),
        # 医疗（6）
        (49, "恒瑞医药", "华东", "连云港", "医疗", "S"),
        (50, "迈瑞医疗", "华南", "深圳", "医疗", "S"),
        (51, "药明康德", "华东", "上海", "医疗", "A"),
        (52, "爱尔眼科", "华中", "长沙", "医疗", "A"),
        (53, "云南白药", "西南", "昆明", "医疗", "B"),
        (54, "华大基因", "华南", "深圳", "医疗", "B"),
        # 物流（6）
        (55, "顺丰速运", "华南", "深圳", "物流", "S"),
        (56, "中通快递", "华东", "上海", "物流", "A"),
        (57, "圆通速递", "华东", "上海", "物流", "B"),
        (58, "德邦物流", "华东", "上海", "物流", "B"),
        (59, "京东物流", "华北", "北京", "物流", "A"),
        (60, "满帮集团", "西南", "贵阳", "物流", "B"),
    ]
    for cid, name, region, city, industry, tier in customer_rows:
        conn.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
            (cid, name, region, city, industry, tier),
        )

    # ── employees: 39 人（保持不变，避免牵连评测）──
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

    # ── orders: 跨 2025-06-01 ~ 2026-09-01，~8000 条 ──
    statuses = ["completed", "pending", "cancelled"]
    status_weights = [0.60, 0.28, 0.12]
    channel_weights = [0.40, 0.30, 0.20, 0.10]  # 直销/渠道/线上/会销
    price_by_id = {pid: price for pid, _, _, price, _ in products}
    # 产品热度权重：热门产品更容易被下单
    product_weights = [
        3, 3, 4, 2, 2, 2, 2, 3, 2,   # 软件
        3, 3, 3, 2, 2, 2, 2, 2, 2,   # 硬件
        5, 2, 2, 3, 4, 2, 2, 2, 2,   # 服务（培训/售后热）
        4, 3, 3, 4, 3, 3, 3, 3, 2,   # 云服务
    ]
    # 客户下单活跃度：S 级大客户更多单
    tier_weight = {"S": 3, "A": 2, "B": 1}

    order_id = 1
    start_date = datetime(2025, 6, 1)
    end_date = datetime(2026, 9, 1)
    total_days = (end_date - start_date).days + 1

    for day_offset in range(total_days):
        date = start_date + timedelta(days=day_offset)
        month = date.month
        base = 17.5
        if month in (6, 11, 12):   # 年中、年末冲量
            base = 22.0
        elif month in (1, 2):      # 春节淡季
            base = 8.0
        n = max(0, int(random.gauss(base, 4)))
        for _ in range(n):
            dept_id = random.randint(1, 6)
            product_id = random.choices(range(1, 37), weights=product_weights)[0]
            # 客户按 tier 活跃度加权
            customer_id = random.choices(
                [c[0] for c in customer_rows],
                weights=[tier_weight[c[5]] for c in customer_rows],
            )[0]
            channel_id = random.choices([1, 2, 3, 4], weights=channel_weights)[0]

            price = price_by_id[product_id]
            quantity = random.choices([1, 2, 3, 5], weights=[0.4, 0.3, 0.2, 0.1])[0]
            total = round(price * quantity * random.uniform(0.7, 1.4), -2)
            status = random.choices(statuses, weights=status_weights)[0]

            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, dept_id, product_id, customer_id, channel_id,
                 total, quantity, status, date.strftime("%Y-%m-%d")),
            )
            order_id += 1

    # ── payments / shipments: 由 completed 订单派生（对账 / 物流场景）──
    pay_methods = ["线上支付", "银行转账", "分期付款", "其他"]
    ship_carriers = ["顺丰速运", "京东物流", "中通快递", "圆通速递", "德邦物流"]
    completed_orders = conn.execute(
        "SELECT id, total, created_at FROM orders WHERE status='completed'"
    ).fetchall()

    pay_id = 1
    ship_id = 1
    for oid, total, created_at in completed_orders:
        n_pay = random.choices([1, 2, 3], weights=[0.78, 0.18, 0.04])[0]
        for _ in range(n_pay):
            amt = round(total * random.uniform(0.3, 0.7), 2)
            method = random.choices(pay_methods, weights=[0.5, 0.3, 0.15, 0.05])[0]
            pstatus = random.choices(["paid", "refunded", "pending"], weights=[0.86, 0.08, 0.06])[0]
            paid_at = (datetime.strptime(created_at, "%Y-%m-%d") + timedelta(days=random.randint(0, 6))).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
                (pay_id, oid, amt, method, pstatus, paid_at),
            )
            pay_id += 1

        carrier = random.choice(ship_carriers)
        shipped_at = (datetime.strptime(created_at, "%Y-%m-%d") + timedelta(days=random.randint(1, 3))).strftime("%Y-%m-%d")
        sstatus = random.choices(["delivered", "in_transit", "shipped"], weights=[0.7, 0.2, 0.1])[0]
        delivered_at = (
            (datetime.strptime(shipped_at, "%Y-%m-%d") + timedelta(days=random.randint(1, 6))).strftime("%Y-%m-%d")
            if sstatus == "delivered" else None
        )
        conn.execute(
            "INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ship_id, oid, carrier, f"TRK{ship_id:08d}", sstatus, shipped_at, delivered_at),
        )
        ship_id += 1

    # ── inventory: 36 产品 × 3 仓 ──
    warehouses = ["华东仓", "华南仓", "华北仓"]
    inv_id = 1
    for pid in range(1, 37):
        for wh in warehouses:
            conn.execute(
                "INSERT INTO inventory VALUES (?, ?, ?, ?, ?)",
                (inv_id, pid, wh, random.randint(0, 500), random.choice([10, 20, 30, 50])),
            )
            inv_id += 1

    # ── ods_orders_hive: Hive 风格订单表（每 3 天一条，旺季可多 region）──
    # 分区: dt (日期), region (地区)
    regions = ["华东", "华南", "华北", "西南", "华中"]
    base_prices = [50000, 20000, 5000, 150000, 30000, 80000, 40000,
                   15000, 60000, 25000, 10000, 45000, 35000, 20000, 8000]
    hive_order_id = 1
    for day_offset in range(0, total_days, 3):
        date = start_date + timedelta(days=day_offset)
        dt = date.strftime("%Y-%m-%d")
        n_hive = 2 if date.month in (6, 8, 9, 12) else 1
        for _ in range(n_hive):
            region = random.choice(regions)
            product_id = random.randint(1, 15)
            customer_id = random.randint(1, 12)
            total = round(base_prices[product_id - 1] * random.uniform(0.7, 1.4), -2)
            quantity = random.choices([1, 2, 3, 5], weights=[0.4, 0.3, 0.2, 0.1])[0]
            status = random.choices(statuses, weights=status_weights)[0]

            conn.execute(
                "INSERT INTO ods_orders_hive VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (dt, region, f"HIV_{hive_order_id:04d}", customer_id, product_id,
                 total, quantity, status, date.strftime("%Y-%m-%d"), "PARQUET"),
            )
            hive_order_id += 1

    # ── dwd_user_events: Hive 风格埋点事件表 ~120 行（覆盖全时间窗）──
    event_types = ["page_view", "click", "add_cart", "purchase", "login", "logout", "search"]
    event_pages = ["/home", "/products", "/cart", "/checkout", "/account", "/search", "/detail"]
    for i in range(120):
        event_date = (start_date + timedelta(days=random.randint(0, total_days - 1))).strftime("%Y-%m-%d")
        user_id = random.randint(1, 20)
        etype = random.choice(event_types)
        page = random.choice(event_pages)
        props = f'{{"page":"{page}","duration":{random.randint(1, 300)},"device":"{random.choice(["iOS", "Android", "Web"])}"}}'
        event_time = f"{event_date} {random.randint(0,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}"
        conn.execute(
            "INSERT INTO dwd_user_events VALUES (?, ?, ?, ?, ?, ?)",
            (event_date, user_id, etype, props, event_time, "ORC"),
        )

    # ── dim_products_hive: Hive 风格产品维度表 ──
    hive_products = [
        (1, "企业版SaaS订阅", "软件", 50000, "腾讯云", '["SaaS","企业级","订阅制"]'),
        (2, "专业版SaaS订阅", "软件", 20000, "阿里云", '["SaaS","专业版","订阅制"]'),
        (3, "基础版SaaS订阅", "软件", 5000, "华为云", '["SaaS","入门","订阅制"]'),
        (4, "定制开发服务", "软件", 150000, "自研", '["定制","外包","项目制"]'),
        (5, "技术咨询服务", "软件", 30000, "自研", '["咨询","专家","按次"]'),
        (6, "数据分析平台", "硬件", 80000, "浪潮", '["硬件","服务器","一体机"]'),
        (7, "服务器运维服务", "硬件", 40000, "戴尔", '["硬件","运维","年度"]'),
        (8, "云存储套餐", "硬件", 15000, "华为云", '["硬件","存储","按量"]'),
        (9, "网络安全方案", "硬件", 60000, "奇安信", '["安全","方案","年度"]'),
        (10, "IoT设备套件", "硬件", 25000, "小米", '["硬件","IoT","套件"]'),
    ]
    for pid, name, cat, price, supplier, tags in hive_products:
        conn.execute(
            "INSERT INTO dim_products_hive VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, name, cat, price, supplier, tags, "PARQUET"),
        )

    # 角色 / 用户：唯一真值来源是 harness.constraints.entitlement._DEFAULT_ROLES，
    # 这里据此写库（不再手抄一份，杜绝 DB 与代码漂移）。
    from harness.constraints.entitlement import _DEFAULT_ROLES, _DEFAULT_USERS

    def _json_or_null(value):
        return None if value is None else json.dumps(value, ensure_ascii=False)

    for role, meta in _DEFAULT_ROLES.items():
        conn.execute(
            "INSERT INTO agent_roles VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                role,
                meta["name"],
                json.dumps(meta["allowed_tools"], ensure_ascii=False),
                _json_or_null(meta["db_tables"]),
                _json_or_null(meta["db_row_filter"]),
                _json_or_null(meta["docs_filter"]),
                1 if meta["sensitive_check"] else 0,
            ),
        )
    for uid, user in _DEFAULT_USERS.items():
        conn.execute(
            "INSERT INTO agent_users VALUES (?, ?, ?, ?)",
            (uid, user["name"], user["role"], user["dept_id"]),
        )

    conn.commit()

    # 统计
    emp_count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    cust_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    prod_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    chan_count = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    pay_count = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    ship_count = conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
    inv_count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
    hive_order_count = conn.execute("SELECT COUNT(*) FROM ods_orders_hive").fetchone()[0]
    hive_event_count = conn.execute("SELECT COUNT(*) FROM dwd_user_events").fetchone()[0]
    hive_prod_count = conn.execute("SELECT COUNT(*) FROM dim_products_hive").fetchone()[0]
    conn.close()

    print(f"数据库已初始化: {DB_PATH}")
    print(f"  [SQL]  departments: 6, employees: {emp_count}, products: {prod_count}, "
          f"customers: {cust_count}, channels: {chan_count}, orders: {order_count}, "
          f"payments: {pay_count}, shipments: {ship_count}, inventory: {inv_count}")
    print(f"  [Hive] ods_orders_hive: {hive_order_count}, dwd_user_events: {hive_event_count}, "
          f"dim_products_hive: {hive_prod_count}")
    print("  [HBase] 内存模拟表: orders / user_profile / product_catalog（启动时 seed）")
    print(f"  时间范围: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    print("  能力: 多 Agent 编排 · SQL/Hive/HBase · 权限 HITL · 记忆 · Task board")


if __name__ == "__main__":
    init_db(reset=True)
