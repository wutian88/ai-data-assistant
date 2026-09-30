from unittest.mock import patch

import sqlglot

from app.services.sql_agent import (
    MAX_ROWS,
    safe_sql_query,
    tools,
)


def main():
    print("========== SQL Tool 集成测试 ==========\n")

    # 1. 确认原始 SQL 执行工具已被移除
    tool_names = [item.name for item in tools]

    assert "sql_db_query" not in tool_names
    assert "safe_sql_query" in tool_names

    print("[PASS] 只能通过自定义安全工具执行 SQL")

    # 2. 正常数据库查询
    result = safe_sql_query.invoke({
        "query": "SELECT COUNT(*) FROM orders"
    })

    assert not result.startswith("SQL校验失败")
    assert not result.startswith("SQL执行失败")

    print(f"[PASS] 正常查询：{result}")

    # 3. 验证非法 SQL 被 Tool 拦截
    blocked_cases = [
        ("DELETE", "DELETE FROM orders"),
        ("敏感字段", "SELECT password FROM users"),
        ("SELECT *", "SELECT * FROM users"),
        (
            "多语句",
            "SELECT id FROM orders; DROP TABLE orders",
        ),
    ]

    for name, sql in blocked_cases:
        result = safe_sql_query.invoke({
            "query": sql
        })

        assert result.startswith(
            "SQL校验失败"
        ), f"{name} 未被正确拦截：{result}"

        print(f"[PASS] {name} 已拦截")

    # 4. 验证 Tool 最终提交执行的 SQL
    # 使用 Mock，避免真正执行测试查询
    with (
        patch(
            "app.services.sql_agent.execute_with_timeout",
            return_value=[(1,)],
        ) as mock_execute,
        patch("app.services.sql_agent.audit_log"),
    ):
        safe_sql_query.invoke({
            "query": (
                "SELECT id FROM orders "
                "LIMIT 1000000"
            )
        })

        executed_sql = mock_execute.call_args.args[0]

        tree = sqlglot.parse_one(
            executed_sql,
            read="sqlite",
        )

        actual_limit = int(
            tree.args["limit"].expression.this
        )

        assert actual_limit == MAX_ROWS

        print(
            f"[PASS] Tool 强制 LIMIT："
            f"{actual_limit}"
        )

    print("\nSQL Tool 集成测试全部通过")


if __name__ == "__main__":
    main()