# db/seed.py — 示例数据库初始化
#
# 扩数据的原因：原来 3 个部门 + 5 条订单只能做基本的 select 验证，
# 面试时说不出"趋势分析"、"同比环比"、"多维下钻"这类词。
# 现在 6 个部门 + 85 条订单（跨 4 个月）+ 30 个员工 + 15 个产品，
# Agent 能做时间序列对比、部门绩效排名、产品动销分析。

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

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            dept_id INTEGER REFERENCES departments(id),
            product_id INTEGER REFERENCES products(id),
            total REAL NOT NULL,
            quantity INTEGER DEFAULT 1,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- 用户记忆表（Phase 3 结构化记忆）
        CREATE TABLE IF NOT EXISTS user_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            access_count INTEGER DEFAULT 0
        );

        DELETE FROM orders;
        DELETE FROM employees;
        DELETE FROM products;
        DELETE FROM departments;
        DELETE FROM user_memory;

        -- departments: 6 个（原来 3 个，加财务/人事/产品）
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
    """)

    # employees: 30 个，分布到 6 个部门
    titles = {
        "销售部": ["销售总监", "大客户经理", "销售代表", "销售代表", "销售助理"],
        "市场部": ["市场总监", "品牌经理", "市场专员", "市场专员"],
        "研发部": ["技术总监", "高级工程师", "高级工程师", "前端工程师", "后端工程师",
                  "后端工程师", "测试工程师", "测试工程师"],
        "财务部": ["财务总监", "会计", "出纳"],
        "人事部": ["人事总监", "招聘经理", "HR专员"],
        "产品部": ["产品总监", "产品经理", "产品经理", "UX设计师"],
    }
    surnames = ["张", "李", "王", "赵", "陈", "刘", "黄", "周", "吴", "杨",
                "朱", "马", "胡", "郭", "何", "高", "林", "郑", "罗", "梁",
                "宋", "唐", "许", "韩", "冯", "邓", "曹", "彭", "曾", "萧"]
    given = ["伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋",
             "勇", "艳", "杰", "娟", "涛", "明", "超", "秀兰", "霞", "平",
             "刚", "桂英", "文", "华", "建华", "玉兰", "建平", "志强", "宇", "欣"]

    random.seed(42)  # 固定种子，数据可复现
    emp_id = 1
    for dept_id, dept_name in enumerate(
        ["销售部", "市场部", "研发部", "财务部", "人事部", "产品部"], start=1
    ):
        for title in titles[dept_name]:
            name = random.choice(surnames) + random.choice(given)
            # 薪资：管理层 25-45K，专员 12-22K，技术 20-40K
            if "总监" in title:
                salary = random.randint(35000, 45000)
            elif "经理" in title or "高级" in title or "资深" in title:
                salary = random.randint(20000, 35000)
            elif "工程师" in title or "设计师" in title:
                salary = random.randint(15000, 30000)
            else:
                salary = random.randint(8000, 18000)

            # 入职日期：2021 到 2026
            days_ago = random.randint(30, 2000)
            hire_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

            # ~10% 的人已离职
            status = "inactive" if random.random() < 0.1 else "active"

            conn.execute(
                "INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
                (emp_id, name, dept_id, title, salary, hire_date, status),
            )
            emp_id += 1

    # orders: 85 条，跨 2026-03-01 到 2026-07-15
    statuses = ["completed", "pending", "cancelled"]
    status_weights = [0.65, 0.25, 0.10]  # 65% 完成, 25% 待处理, 10% 取消

    order_id = 1
    start_date = datetime(2026, 3, 1)
    for day_offset in range(136):  # ~4.5 个月
        date = start_date + timedelta(days=day_offset)
        # 每天 0-2 单
        for _ in range(random.choices([0, 1, 2], weights=[0.4, 0.4, 0.2])[0]):
            dept_id = random.randint(1, 6)
            product_id = random.randint(1, 15)
            base_price = [50000, 20000, 5000, 150000, 30000, 80000, 40000,
                         15000, 60000, 25000, 10000, 45000, 35000, 20000, 8000][product_id - 1]
            # 金额在单价 ± 30% 内浮动
            total = round(base_price * random.uniform(0.7, 1.3), -2)
            quantity = random.randint(1, 5)
            status = random.choices(statuses, weights=status_weights)[0]

            conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order_id, dept_id, product_id, total, quantity, status,
                 date.strftime("%Y-%m-%d")),
            )
            order_id += 1

    conn.commit()
    conn.close()
    print(f"数据库已初始化: {DB_PATH}")
    print(f"  departments: 6 行, employees: {emp_id - 1} 行, products: 15 行, orders: {order_id - 1} 行")


if __name__ == "__main__":
    init_db(reset=True)
