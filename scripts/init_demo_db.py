"""Deterministic portfolio data and an explicit, protected legacy-demo upgrade.

Normal startup never replaces an existing database. --upgrade-demo only accepts
the exact original 4/3/5-row demo, keeps a verified SQLite backup, and upgrades
inside a transaction. Unknown or modified databases are rejected.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.paths import SHOP_DB

DB_PATH = SHOP_DB
SEED = 42
REFERENCE_DATE = date(2026, 10, 1)
RECENT_DAYS = 30
SCHEMA_VERSION = 2
APPLICATION_ID = 0x41494441  # AIDA
COUNTS = {"users": 1000, "products": 200, "orders": 10000}
CITIES = ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安")
CATEGORIES = ("数码", "家居", "服饰", "食品", "运动")
STATUSES = ("paid", "shipped", "completed", "cancelled")

LEGACY_SCHEMA = {
    "users": """CREATE TABLE users (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL
    )""",
    "products": """CREATE TABLE products (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL
    )""",
    "orders": """CREATE TABLE orders (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
}
LEGACY_ROWS = {
    "users": [(1, "Alice", "北京"), (2, "Bob", "上海"),
              (3, "Carol", "广州"), (4, "David", "深圳")],
    "products": [(1, "机械键盘", 299.0), (2, "无线鼠标", 129.0),
                 (3, "显示器", 1299.0)],
    "orders": [
        (1, 1, 1, 1, "2026-09-01 10:00:00"),
        (2, 2, 2, 2, "2026-09-02 11:30:00"),
        (3, 3, 3, 1, "2026-09-03 14:20:00"),
        (4, 1, 2, 3, "2026-09-04 09:15:00"),
        (5, 4, 1, 2, "2026-09-05 16:40:00"),
    ],
}
V2_SCHEMA = {
    "users": """CREATE TABLE users (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "products": """CREATE TABLE products (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
        category TEXT NOT NULL
    )""",
    "orders": """CREATE TABLE orders (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
        created_at TEXT NOT NULL, status TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )""",
}
COLUMNS = {
    "users": ("id", "name", "city", "created_at"),
    "products": ("id", "name", "price", "category"),
    "orders": ("id", "user_id", "product_id", "quantity", "created_at", "status"),
}


class DemoUpgradeError(RuntimeError):
    """The requested upgrade would risk changing non-demo data."""


def generate_dataset() -> dict[str, list[tuple]]:
    """Use a private RNG and fixed calendar; wall-clock time never affects data."""
    rng = random.Random(SEED)
    users = []
    registration_start = REFERENCE_DATE - timedelta(days=364)
    registration_dates = {}
    for user_id in range(1, COUNTS["users"] + 1):
        created = registration_start + timedelta(days=rng.randrange(365))
        registration_dates[user_id] = created
        users.append((user_id, f"User{user_id:04d}", rng.choice(CITIES),
                      created.isoformat() + " 00:00:00"))
    products = []
    for product_id in range(1, COUNTS["products"] + 1):
        category = CATEGORIES[(product_id - 1) % len(CATEGORIES)]
        price_cents = rng.randrange(1000, 200001)
        products.append((product_id, f"{category}商品{product_id:03d}",
                         price_cents / 100, category))
    orders = []
    order_start = REFERENCE_DATE - timedelta(days=179)
    for order_id in range(1, COUNTS["orders"] + 1):
        user_id = rng.randrange(1, COUNTS["users"] + 1)
        product_id = rng.randrange(1, COUNTS["products"] + 1)
        first_day = max(order_start, registration_dates[user_id])
        day = first_day + timedelta(
            days=rng.randrange((REFERENCE_DATE - first_day).days + 1)
        )
        created = datetime.combine(day, time()) + timedelta(
            seconds=rng.randrange(86400)
        )
        orders.append((order_id, user_id, product_id, rng.randrange(1, 6),
                       created.isoformat(sep=" "),
                       rng.choices(STATUSES, weights=(30, 25, 30, 15), k=1)[0]))
    return {"users": users, "products": products, "orders": orders}


def dataset_fingerprint(rows: dict[str, list[tuple]]) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def expected_metadata(rows: dict[str, list[tuple]] | None = None) -> dict[str, Any]:
    rows = generate_dataset() if rows is None else rows
    return {
        "dataset_version": "portfolio-v2",
        "schema_version": SCHEMA_VERSION,
        "seed": SEED,
        "reference_date": REFERENCE_DATE.isoformat(),
        "recent_days_inclusive": RECENT_DAYS,
        "recent_start_date": (REFERENCE_DATE - timedelta(days=RECENT_DAYS - 1)).isoformat(),
        "order_start_date": (REFERENCE_DATE - timedelta(days=179)).isoformat(),
        "counts": COUNTS,
        "dataset_fingerprint": dataset_fingerprint(rows),
        "amount_basis": "quantity * current products.price; exclude cancelled; not historical sale price",
    }


def _read_rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"
        ).fetchall()
        for table, columns in COLUMNS.items()
    }


def _normal_sql(sql: str) -> str:
    return re.sub(r"\s+", "", sql).casefold()


def is_exact_legacy_demo(connection: sqlite3.Connection) -> bool:
    """Reject extra objects/constraints/columns and even one changed demo value."""
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
            return False
        if connection.execute("PRAGMA application_id").fetchone()[0] != 0:
            return False
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE lower(substr(name, 1, 7)) != 'sqlite_' ORDER BY name"
        ).fetchall()
        if len(objects) != 3:
            return False
        for object_type, table, sql in objects:
            if object_type != "table" or table not in LEGACY_SCHEMA:
                return False
            if not sql or _normal_sql(sql) != _normal_sql(LEGACY_SCHEMA[table]):
                return False
            column_names = [row[1] for row in connection.execute(f"PRAGMA table_xinfo({table})")]
            if len(column_names) != len(LEGACY_ROWS[table][0]):
                return False
            if connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() != LEGACY_ROWS[table]:
                return False
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            return False
        return connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    except sqlite3.DatabaseError:
        return False


def _read_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        key: json.loads(value)
        for key, value in connection.execute("SELECT key, value FROM demo_metadata")
    }


def read_demo_metadata(path: Path | str = DB_PATH) -> dict[str, Any]:
    """Read data provenance without importing a model or changing the database."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return _read_metadata(connection)


def verify_demo_database(path: Path | str = DB_PATH) -> dict[str, Any]:
    """Validate provenance and actual rows through a read-only connection."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return _verify_v2(connection)


def _write_metadata(connection: sqlite3.Connection, metadata: dict[str, Any]) -> None:
    connection.execute("CREATE TABLE demo_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO demo_metadata(key, value) VALUES (?, ?)",
        [(key, json.dumps(value, ensure_ascii=False, sort_keys=True))
         for key, value in metadata.items()],
    )
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")


def _insert_dataset(connection: sqlite3.Connection, rows: dict[str, list[tuple]]) -> None:
    for table, columns in COLUMNS.items():
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table}({', '.join(columns)}) VALUES ({placeholders})",
            rows[table],
        )


def _verify_v2(connection: sqlite3.Connection) -> dict[str, Any]:
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE lower(substr(name, 1, 7)) != 'sqlite_'"
    ).fetchall()
    if set(objects) != {("table", name) for name in (*COLUMNS, "demo_metadata")}:
        raise DemoUpgradeError("Demo schema contains unexpected objects")
    for table, columns in COLUMNS.items():
        actual = connection.execute(f"PRAGMA table_xinfo({table})").fetchall()
        if tuple(row[1] for row in actual) != columns or any(row[6] for row in actual):
            raise DemoUpgradeError("Demo schema contains unexpected columns")
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise DemoUpgradeError("Unexpected demo schema version")
    if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise DemoUpgradeError("Unexpected database application id")
    metadata = _read_metadata(connection)
    if metadata != expected_metadata():
        raise DemoUpgradeError("Demo metadata does not match the fixed dataset")
    if dataset_fingerprint(_read_rows(connection)) != metadata["dataset_fingerprint"]:
        raise DemoUpgradeError("Demo rows have changed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise DemoUpgradeError("Demo foreign key check failed")
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise DemoUpgradeError("Demo integrity check failed")
    return metadata


def _create_database(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.init-", suffix=".db", dir=path.parent
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        rows = generate_dataset()
        metadata = expected_metadata(rows)
        connection = sqlite3.connect(temporary, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in V2_SCHEMA.values():
                connection.execute(statement)
            _insert_dataset(connection, rows)
            _write_metadata(connection, metadata)
            _verify_v2(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        # A hard link publishes the complete sibling file atomically and refuses
        # to overwrite a database created by another initializer in the meantime.
        try:
            os.link(temporary, path)
        except FileExistsError:
            return {"status": "skipped", "path": str(path)}
        return {"status": "created", "path": str(path), "metadata": metadata}
    finally:
        temporary.unlink(missing_ok=True)


def _backup_legacy(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.stem}.before-v2-{stamp}-{uuid.uuid4().hex[:8]}.db")
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    # This is a separate read-only source: backup() on the writer connection
    # holding BEGIN IMMEDIATE would keep retrying SQLITE_LOCKED.
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination, pages=128, sleep=0.01)
        if not is_exact_legacy_demo(destination):
            raise DemoUpgradeError(f"Legacy backup failed identity/integrity verification: {backup}")
    finally:
        destination.close()
        source.close()
    return backup


def _upgrade_database(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DemoUpgradeError("Refusing to upgrade a non-regular database path")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=rw",
                                 uri=True, isolation_level=None, timeout=5)
    backup = None
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID:
            metadata = _verify_v2(connection)
            connection.rollback()
            return {"status": "skipped", "path": str(path), "metadata": metadata}
        if not is_exact_legacy_demo(connection):
            raise DemoUpgradeError("Refusing to upgrade: database is not the unchanged legacy demo")
        backup = _backup_legacy(path)
        if not is_exact_legacy_demo(connection):
            raise DemoUpgradeError("Legacy database changed before upgrade")
        connection.execute("ALTER TABLE users ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        for table in ("orders", "products", "users"):
            connection.execute(f"DELETE FROM {table}")
        rows = generate_dataset()
        _insert_dataset(connection, rows)
        _write_metadata(connection, expected_metadata(rows))
        metadata = _verify_v2(connection)
        connection.commit()
        return {"status": "upgraded", "path": str(path),
                "backup_path": str(backup), "metadata": metadata}
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
        if backup is not None:
            print(f"[init-db] legacy backup preserved: {backup}")


def init_database(db_path: Path | str | None = None, *,
                  upgrade_demo: bool = False) -> dict[str, Any]:
    path = Path(DB_PATH if db_path is None else db_path).absolute()
    if path.exists():
        if not upgrade_demo:
            print(f"[init-db] database already exists; leaving it unchanged: {path}")
            return {"status": "skipped", "path": str(path)}
        result = _upgrade_database(path)
    else:
        result = _create_database(path)
    print(f"[init-db] {result['status']}: {path}")
    if "metadata" in result:
        print(f"[init-db] users=1000 products=200 orders=10000 seed=42 "
              f"reference_date={REFERENCE_DATE.isoformat()}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade-demo", action="store_true",
                        help="Back up and upgrade only the exact unchanged legacy demo")
    args = parser.parse_args()
    init_database(upgrade_demo=args.upgrade_demo)


if __name__ == "__main__":
    main()
