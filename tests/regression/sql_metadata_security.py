"""Regression：SQL 工具白名单与元数据泄露回归测试。

放入 tests/regression/sql_metadata_security.py 后，从项目根目录执行：
    python -m tests.regression.sql_metadata_security

无需真实 LLM 请求；元数据测试也不会读取 SQLite 数据。
"""

from unittest.mock import patch

from app.services.sql_agent import ALLOWED_COLUMNS, ALLOWED_TABLES, safe_list_tables, safe_schema, tools


def main() -> None:
    print("========== SQL 元数据安全测试 ==========\n")

    tool_names = [item.name for item in tools]
    assert set(tool_names) == {"sql_db_list_tables", "sql_db_schema", "safe_sql_query"}
    assert "sql_db_query" not in tool_names
    assert "sql_db_query_checker" not in tool_names
    print("[PASS] Agent 仅暴露三个受控工具")

    # 元数据 Tool 应只读取配置白名单，无需连接数据库。
    with patch("app.services.sql_agent.sqlite3.connect") as mock_connect:
        table_result = safe_list_tables.invoke({})
        assert table_result == ", ".join(sorted(ALLOWED_TABLES))
        assert "sqlite_master" not in table_result
        print("[PASS] 表列表仅包含授权表")

        schema_result = safe_schema.invoke({"table_names": "users, orders"})
        for table in ("users", "orders"):
            assert f"表：{table}" in schema_result
            for column in ALLOWED_COLUMNS[table]:
                assert column in schema_result
        assert "password" not in schema_result
        assert "products" not in schema_result
        print("[PASS] Schema 仅返回所请求表的授权字段")

        for table_names in (
            "sqlite_master",
            "users, sqlite_master",  # 整批拒绝，不泄露 users 结构。
            "",
            "users,",
        ):
            result = safe_schema.invoke({"table_names": table_names})
            assert result.startswith("SQL权限校验失败"), (table_names, result)
            assert "允许字段" not in result
        print("[PASS] 未授权、混合、空表名均被拒绝")

        mock_connect.assert_not_called()
        print("[PASS] 元数据工具不连接 SQLite")

    print("\nSQL 元数据安全测试全部通过")


if __name__ == "__main__":
    main()
