"""Temporary-database checks for deterministic demo data and protected upgrades."""
import hashlib
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import init_demo_db as demo


def create_legacy(path: Path, *, journal_mode: str = "delete") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA journal_mode = {journal_mode}")
        for statement in demo.LEGACY_SCHEMA.values():
            connection.execute(statement)
        for table, rows in demo.LEGACY_ROWS.items():
            marks = ",".join("?" for _ in rows[0])
            connection.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)


def assert_legacy(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        assert demo.is_exact_legacy_demo(connection)
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone() == (4,)
        assert connection.execute("SELECT COUNT(*) FROM products").fetchone() == (3,)
        assert connection.execute("SELECT SUM(quantity) FROM orders").fetchone() == (9,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def generated(tmp_path):
    path = tmp_path / "shop.db"
    result = demo.init_database(path)
    assert result["status"] == "created"
    return path


def test_scale_schema_foreign_keys_and_calendar(generated):
    metadata = demo.read_demo_metadata(generated)
    assert metadata["schema_version"] == 2
    assert metadata["seed"] == 42
    assert metadata["reference_date"] == "2026-10-01"
    assert metadata["recent_start_date"] == "2026-09-02"
    assert metadata["counts"] == {"users": 1000, "products": 200, "orders": 10000}
    assert metadata["dataset_fingerprint"] == "ee7c2fdb3efa3476c44e0066f0995b5bf2a00c8e05b1bf7b665a7c95aa7f22a3"
    with sqlite3.connect(generated) as connection:
        for table, count in metadata["counts"].items():
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (count,)
            assert connection.execute(f"SELECT MIN(id), MAX(id) FROM {table}").fetchone() == (1, count)
        for table, columns in demo.COLUMNS.items():
            assert [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] == list(columns)
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert connection.execute("PRAGMA application_id").fetchone() == (demo.APPLICATION_ID,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id "
            "WHERE o.created_at < u.created_at OR o.created_at >= '2026-10-02'"
        ).fetchone() == (0,)
        assert {row[0] for row in connection.execute("SELECT DISTINCT status FROM orders")} == set(demo.STATUSES)
        assert {row[0] for row in connection.execute("SELECT DISTINCT category FROM products")} == set(demo.CATEGORIES)
        assert demo.dataset_fingerprint(demo._read_rows(connection)) == metadata["dataset_fingerprint"]


def test_deterministic_content_and_private_rng(tmp_path):
    before = random.getstate()
    first = tmp_path / "a.db"
    second = tmp_path / "b.db"
    demo.init_database(first)
    demo.init_database(second)
    assert random.getstate() == before
    assert demo.read_demo_metadata(first) == demo.read_demo_metadata(second)
    with sqlite3.connect(first) as a, sqlite3.connect(second) as b:
        assert demo._read_rows(a) == demo._read_rows(b)


@pytest.mark.parametrize("kind", ["legacy", "v2", "unknown", "not-sqlite"])
def test_default_never_overwrites_existing_database(tmp_path, kind):
    path = tmp_path / "shop.db"
    if kind == "legacy":
        create_legacy(path)
    elif kind == "v2":
        demo.init_database(path)
    elif kind == "unknown":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE important(value TEXT)")
            connection.execute("INSERT INTO important VALUES ('preserve me')")
    else:
        path.write_bytes(b"not a SQLite database")
    before = file_hash(path)
    assert demo.init_database(path)["status"] == "skipped"
    assert file_hash(path) == before
    assert list(tmp_path.glob("*.before-v2-*.db")) == []


@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_exact_legacy_upgrade_preserves_verified_backup(tmp_path, journal_mode):
    path = tmp_path / "shop.db"
    create_legacy(path, journal_mode=journal_mode)
    result = demo.init_database(path, upgrade_demo=True)
    assert result["status"] == "upgraded"
    backup = Path(result["backup_path"])
    assert backup.parent == path.parent and backup != path
    assert_legacy(backup)
    assert demo.read_demo_metadata(path) == demo.expected_metadata()
    with sqlite3.connect(path) as connection:
        assert demo._verify_v2(connection) == demo.expected_metadata()
    assert demo.init_database(path, upgrade_demo=True)["status"] == "skipped"
    assert list(tmp_path.glob("*.before-v2-*.db")) == [backup]


@pytest.mark.parametrize("alteration", [
    "UPDATE users SET name = 'Custom' WHERE id = 1",
    "UPDATE products SET price = 1.23 WHERE id = 1",
    "UPDATE orders SET quantity = 2 WHERE id = 1",
    "ALTER TABLE users ADD COLUMN email TEXT",
    "CREATE TABLE custom(value TEXT)",
    "CREATE INDEX custom_index ON users(name)",
    "CREATE VIEW custom_view AS SELECT name FROM users",
    "CREATE TRIGGER custom_trigger AFTER INSERT ON users BEGIN SELECT 1; END",
    "CREATE TABLE sqliteXimportant(value TEXT)",
    "CREATE INDEX sqliteXindex ON users(name)",
    "CREATE TRIGGER sqliteXtrigger AFTER DELETE ON orders BEGIN UPDATE users SET name='changed'; END",
    "PRAGMA user_version = 7",
    "PRAGMA application_id = 123",
])
def test_upgrade_refuses_modified_or_extended_legacy(tmp_path, alteration):
    path = tmp_path / "shop.db"
    create_legacy(path)
    with sqlite3.connect(path) as connection:
        connection.execute(alteration)
    before = file_hash(path)
    with pytest.raises(demo.DemoUpgradeError, match="not the unchanged legacy demo"):
        demo.init_database(path, upgrade_demo=True)
    assert file_hash(path) == before
    assert list(tmp_path.glob("*.before-v2-*.db")) == []


def test_upgrade_refuses_schema_with_extra_constraint(tmp_path):
    path = tmp_path / "shop.db"
    with sqlite3.connect(path) as connection:
        for table, statement in demo.LEGACY_SCHEMA.items():
            if table == "users":
                statement = statement.replace("city TEXT NOT NULL", "city TEXT NOT NULL CHECK(city <> '')")
            connection.execute(statement)
        for table, rows in demo.LEGACY_ROWS.items():
            marks = ",".join("?" for _ in rows[0])
            connection.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
    with pytest.raises(demo.DemoUpgradeError):
        demo.init_database(path, upgrade_demo=True)


@pytest.mark.parametrize("failure_point", ["insert", "verification"])
def test_upgrade_failure_rolls_back_schema_rows_and_metadata(tmp_path, monkeypatch, failure_point):
    path = tmp_path / "shop.db"
    create_legacy(path)
    if failure_point == "insert":
        def broken_insert(connection, rows):
            connection.execute(
                "INSERT INTO users(id,name,city,created_at) VALUES (1,'partial','test','2026-01-01')"
            )
            raise RuntimeError("simulated insertion failure")
        monkeypatch.setattr(demo, "_insert_dataset", broken_insert)
    else:
        def broken_verify(connection):
            raise RuntimeError("simulated verification failure")
        monkeypatch.setattr(demo, "_verify_v2", broken_verify)
    with pytest.raises(RuntimeError, match="simulated"):
        demo.init_database(path, upgrade_demo=True)
    assert_legacy(path)
    backups = list(tmp_path.glob("*.before-v2-*.db"))
    assert len(backups) == 1
    assert_legacy(backups[0])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='demo_metadata'").fetchall() == []


def test_failed_fresh_creation_leaves_no_partial_database(tmp_path, monkeypatch):
    path = tmp_path / "shop.db"
    def broken_insert(connection, rows):
        raise RuntimeError("simulated creation failure")
    monkeypatch.setattr(demo, "_insert_dataset", broken_insert)
    with pytest.raises(RuntimeError, match="simulated"):
        demo.init_database(path)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_publish_does_not_overwrite_database_created_by_another_initializer(tmp_path, monkeypatch):
    path = tmp_path / "shop.db"
    original_link = demo.os.link
    def competing_link(source, destination):
        with sqlite3.connect(destination) as connection:
            connection.execute("CREATE TABLE important(value TEXT)")
            connection.execute("INSERT INTO important VALUES ('competing initializer')")
        original_link(source, destination)
    monkeypatch.setattr(demo.os, "link", competing_link)
    assert demo.init_database(path)["status"] == "skipped"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM important").fetchone() == ("competing initializer",)
    assert list(tmp_path.glob(".shop.db.init-*")) == []


def test_upgrade_refuses_modified_v2_data(generated):
    with sqlite3.connect(generated) as connection:
        connection.execute("UPDATE users SET name='custom' WHERE id=1")
    with pytest.raises(demo.DemoUpgradeError, match="rows have changed"):
        demo.init_database(generated, upgrade_demo=True)
    assert list(generated.parent.glob("*.before-v2-*.db")) == []


def test_business_gold_queries_use_actual_data_and_fixed_time(generated, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-test-placeholder")
    from app.services import sql_agent
    monkeypatch.setattr(sql_agent, "DB_FILE", str(generated))
    monkeypatch.setattr(sql_agent, "audit_log", lambda *args, **kwargs: None)
    data_file = Path(__file__).resolve().parents[1] / "evals/datasets/sql_eval.json"
    cases = json.loads(data_file.read_text(encoding="utf-8"))["cases"]
    rows = demo.generate_dataset()
    users = {row[0]: row for row in rows["users"]}
    products = {row[0]: row for row in rows["products"]}
    orders = rows["orders"]
    recent_start = demo.REFERENCE_DATE - timedelta(days=demo.RECENT_DAYS - 1)
    expected_recent = sum(recent_start <= date.fromisoformat(row[4][:10]) <= demo.REFERENCE_DATE for row in orders)
    expected_users = sum(recent_start <= date.fromisoformat(row[3][:10]) <= demo.REFERENCE_DATE for row in users.values())
    estimated_amount = round(sum(row[3] * products[row[2]][2] for row in orders if row[5] != "cancelled"), 2)
    with sqlite3.connect(generated) as connection:
        results = {case["id"]: connection.execute(case["gold_sql"]).fetchall() for case in cases}
    import ast
    for case in cases:
        tool_rows = ast.literal_eval(sql_agent.safe_sql_query.invoke({"query": case["gold_sql"]}))
        assert tool_rows == results[case["id"]]
    assert results["user_count"] == [(1000,)]
    assert results["product_count"] == [(200,)]
    assert results["order_count"] == [(10000,)]
    assert results["recent_order_count"] == [(expected_recent,)]
    assert results["recent_new_users"] == [(expected_users,)]
    assert results["quantity_total"] == [(29932,)]
    assert results["recent_order_count"] == [(3371,)]
    assert results["recent_new_users"] == [(103,)]
    assert results["estimated_non_cancelled_amount"] == [(27866788.46,)]
    assert results["estimated_non_cancelled_amount"][0][0] == pytest.approx(estimated_amount)
    assert sum(row[1] for row in results["users_by_city"]) == 1000
    assert sum(row[1] for row in results["orders_by_status"]) == 10000
    assert all(len(result) <= 100 for result in results.values())


def test_sql_evaluation_import_is_inert_and_mocked_run_uses_real_gold(generated, monkeypatch, tmp_path):
    import sys
    monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-test-placeholder")
    from app.services import sql_agent
    monkeypatch.setattr(sql_agent, "DB_FILE", str(generated))
    monkeypatch.setattr(sql_agent, "audit_log", lambda *args, **kwargs: None)
    def forbid_real_model(*args, **kwargs):
        raise AssertionError("Evaluation import must not call the model")
    monkeypatch.setattr(sql_agent, "sql_answer_with_usage", forbid_real_model)
    sys.modules.pop("evals.sql_answers", None)
    from evals import sql_answers
    calls = []
    def fake_answer(question):
        calls.append(question)
        return {"answer": "Offline test output, not a model quality result", "usage": {"total_tokens": None}}
    report_path = tmp_path / "reports" / "sql.json"
    report = sql_answers.evaluate(
        db_path=generated, report_file=report_path,
        safe_tool=sql_agent.safe_sql_query, answer_function=fake_answer,
    )
    assert len(calls) == len(report["cases"]) == 16
    assert report["dataset"]["seed"] == 42
    assert report_path.exists()
    assert all(case["answer_correct"] is None and case["error"] is None for case in report["cases"])
    assert {case["id"]: case["gold_result"] for case in report["cases"]}["order_count"] == 10000


def test_sql_evaluation_rejects_wrong_time_window_before_running_gold_or_model(generated, tmp_path):
    from evals import sql_answers

    payload = json.loads(sql_answers.DATA_FILE.read_text(encoding="utf-8"))
    payload["recent_days_inclusive"] = 7
    data_file = tmp_path / "sql_eval.json"
    data_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    safe_tool = Mock()
    answer_function = Mock()
    report_file = tmp_path / "report.json"

    with pytest.raises(ValueError, match="recent_days_inclusive"):
        sql_answers.evaluate(
            data_file=data_file, report_file=report_file, db_path=generated,
            safe_tool=safe_tool, answer_function=answer_function,
        )

    safe_tool.invoke.assert_not_called()
    answer_function.assert_not_called()
    assert not report_file.exists()


def test_recent_order_count_and_explicit_status_filter_have_distinct_real_results(generated, monkeypatch):
    import ast
    monkeypatch.setenv("DASHSCOPE_API_KEY", "offline-test-placeholder")
    from app.services import sql_agent
    monkeypatch.setattr(sql_agent, "DB_FILE", str(generated))
    monkeypatch.setattr(sql_agent, "audit_log", lambda *args, **kwargs: None)
    period = "created_at >= '2026-09-02' AND created_at < '2026-10-02'"
    def count(condition):
        output = sql_agent.safe_sql_query.invoke({"query": f"SELECT COUNT(*) FROM orders WHERE {condition}"})
        return ast.literal_eval(output)[0][0]
    all_statuses = count(period)
    non_cancelled = count(period + " AND status != 'cancelled'")
    cancelled = count(period + " AND status = 'cancelled'")
    assert all_statuses == 3371
    assert non_cancelled == 2869
    assert cancelled == 502
    assert all_statuses == non_cancelled + cancelled
