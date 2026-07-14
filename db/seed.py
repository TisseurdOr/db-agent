# db/seed.py
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "demo.db")


def init_db(reset: bool = False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            budget REAL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            dept_id INTEGER REFERENCES departments(id),
            total REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DELETE FROM orders;
        DELETE FROM departments;

        INSERT INTO departments VALUES (1, '销售部', 1000000);
        INSERT INTO departments VALUES (2, '市场部', 800000);
        INSERT INTO departments VALUES (3, '研发部', 1500000);

        INSERT INTO orders VALUES (1, 1, 150000, 'completed', '2026-07-10');
        INSERT INTO orders VALUES (2, 1, 230000, 'completed', '2026-07-11');
        INSERT INTO orders VALUES (3, 2, 180000, 'completed', '2026-07-09');
        INSERT INTO orders VALUES (4, 2, 234000, 'pending',  '2026-07-12');
        INSERT INTO orders VALUES (5, 3, 98000,  'completed', '2026-07-08');
    """)
    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db(reset=True)
