import os
import sqlite3

from pathlib import Path
from app.core.paths import PROJECT_ROOT, APP_DATA_DIR, SHOP_DB


# ============================================================
# 1. 数据库位置
#
# Docker：
#   APP_DATA_DIR=/data
#   → /data/shop.db
#
# 本机直接运行：
#   没配置 APP_DATA_DIR
#   → 项目 data/shop.db
# ============================================================

DB_PATH = SHOP_DB


# ============================================================
# 2. 初始化数据库
# ============================================================

def init_database() -> None:

    # --------------------------------------------------------
    # 已经存在时不覆盖
    #
    # Docker Volume 中的数据需要持久化，
    # 所以后续启动容器时不能重新写入。
    # --------------------------------------------------------

    if DB_PATH.exists():
        print(f"[init-db] database already exists: {DB_PATH}")
        return

    # 确保目录存在
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(f"[init-db] creating database: {DB_PATH}")

    connection = sqlite3.connect(DB_PATH)

    try:

        # 开启外键约束
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        cursor = connection.cursor()

        # ====================================================
        # 3. 创建 users 表
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT NOT NULL
            )
            """
        )

        # ====================================================
        # 4. 创建 products 表
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL
            )
            """
        )

        # ====================================================
        # 5. 创建 orders 表
        # ====================================================

        cursor.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                created_at TEXT NOT NULL,

                FOREIGN KEY (user_id)
                    REFERENCES users(id),

                FOREIGN KEY (product_id)
                    REFERENCES products(id)
            )
            """
        )

        # ====================================================
        # 6. 插入演示用户
        #
        # 4 位用户
        # ====================================================

        users = [
            (1, "Alice", "北京"),
            (2, "Bob", "上海"),
            (3, "Carol", "广州"),
            (4, "David", "深圳"),
        ]

        cursor.executemany(
            """
            INSERT INTO users (
                id,
                name,
                city
            )
            VALUES (?, ?, ?)
            """,
            users,
        )

        # ====================================================
        # 7. 插入演示商品
        # ====================================================

        products = [
            (1, "机械键盘", 299.0),
            (2, "无线鼠标", 129.0),
            (3, "显示器", 1299.0),
        ]

        cursor.executemany(
            """
            INSERT INTO products (
                id,
                name,
                price
            )
            VALUES (?, ?, ?)
            """,
            products,
        )

        # ====================================================
        # 8. 插入演示订单
        #
        # 共 5 笔订单
        #
        # quantity：
        #
        # 1 + 2 + 1 + 3 + 2 = 9
        #
        # 与 Day20 SQL Agent 评测结果保持一致：
        #
        # COUNT(*)     → 5
        # SUM(quantity) → 9
        # ====================================================

        orders = [
            (
                1,
                1,
                1,
                1,
                "2026-09-01 10:00:00",
            ),
            (
                2,
                2,
                2,
                2,
                "2026-09-02 11:30:00",
            ),
            (
                3,
                3,
                3,
                1,
                "2026-09-03 14:20:00",
            ),
            (
                4,
                1,
                2,
                3,
                "2026-09-04 09:15:00",
            ),
            (
                5,
                4,
                1,
                2,
                "2026-09-05 16:40:00",
            ),
        ]

        cursor.executemany(
            """
            INSERT INTO orders (
                id,
                user_id,
                product_id,
                quantity,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            orders,
        )

        connection.commit()

        print("[init-db] database initialized successfully")
        print("[init-db] users: 4")
        print("[init-db] orders: 5")
        print("[init-db] total quantity: 9")

    except Exception:

        connection.rollback()

        # 初始化失败时删除残缺数据库，
        # 避免下一次因为文件已经存在而跳过初始化。
        connection.close()

        if DB_PATH.exists():
            DB_PATH.unlink()

        raise

    finally:

        # 上面的异常路径可能已经关闭过连接
        try:
            connection.close()
        except Exception:
            pass


# ============================================================
# 9. Script Entry
# ============================================================

if __name__ == "__main__":
    init_database()