"""Regression：SQL 作用域与别名权限漏洞专项回归。

从项目根目录运行：python -m tests.regression.sql_scope_regression
本脚本只调用 SQL 校验器和模拟 Tool 执行，不访问数据库、不调用 LLM。
"""

from unittest.mock import patch
from app.services.sql_agent import validate_sql, safe_sql_query


def must_block(title: str, query: str, keyword: str | None = None) -> None:
    try:
        validate_sql(query)
    except ValueError as exc:
        reason = str(exc)
        if keyword is not None:
            assert keyword in reason, f"{title}：拒绝原因不符：{reason}"
        print(f"[PASS] {title}：{reason}")
        return
    raise AssertionError(f"[FAIL] 未阻止 {title}: {query}")


def must_allow(title: str, query: str) -> None:
    assert validate_sql(query) == query, f"{title} 被错误修改或拒绝"
    print(f"[PASS] {title}")


def main() -> None:
    print("========== SQL 作用域专项回归 ==========\n")
    must_block(
        "复现：内层别名不可授权外层敏感字段",
        "SELECT password FROM users WHERE EXISTS "
        "(SELECT 1 AS password FROM orders)",
        "暂不支持",
    )
    must_block(
        "自别名绕过：SELECT password AS password",
        "SELECT password AS password FROM users",
        "禁止访问字段",
    )
    must_block(
        "WHERE 不得通过 SELECT 结果别名授权",
        "SELECT name AS password FROM users WHERE password = 'x'",
        "禁止访问字段",
    )
    must_block(
        "CTE 默认拒绝",
        "WITH t AS (SELECT id FROM users) SELECT id FROM t",
        "暂不支持",
    )
    must_block(
        "关联子查询默认拒绝",
        "SELECT u.name FROM users AS u WHERE EXISTS "
        "(SELECT 1 FROM orders AS o WHERE o.user_id = u.id)",
        "暂不支持",
    )
    must_block(
        "UNION 默认拒绝",
        "SELECT name FROM users UNION SELECT name FROM users",
        "只允许 SELECT",
    )
    must_block(
        "JOIN USING 暂不支持",
        "SELECT users.name FROM users JOIN orders USING(id)",
        "暂不支持",
    )
    must_block(
        "表别名不得与其他真实表名冲突",
        "SELECT orders.user_id FROM users AS orders "
        "JOIN orders AS o ON orders.id = o.user_id",
        "冲突",
    )
    must_block(
        "非白名单字段仍拒绝",
        "SELECT password FROM users",
        "禁止访问字段",
    )
    print("\n========== 正常 SQL 仍可用 ==========\n")
    must_allow("简单 SELECT", "SELECT id, quantity FROM orders")
    must_allow("表别名", "SELECT o.id, o.quantity FROM orders AS o")
    must_allow(
        "普通 JOIN ON",
        "SELECT u.name, o.quantity FROM users AS u "
        "JOIN orders AS o ON u.id = o.user_id",
    )
    must_allow("COUNT(*)", "SELECT COUNT(*) FROM orders")
    must_allow(
        "聚合结果别名仅用于 ORDER BY",
        "SELECT SUM(quantity) AS total FROM orders ORDER BY total",
    )

    # 验证真实 Tool 入口，不仅检查 validate_sql 函数本身。
    with (
        patch("app.services.sql_agent.execute_with_timeout") as mock_execute,
        patch("app.services.sql_agent.audit_log"),
    ):
        result = safe_sql_query.invoke({
            "query": "SELECT password FROM users WHERE EXISTS "
                     "(SELECT 1 AS password FROM orders)"
        })
        assert result.startswith("SQL校验失败"), result
        mock_execute.assert_not_called()
        print("[PASS] safe_sql_query 没有执行被拒绝的 SQL")

    print("\nSQL 作用域专项回归全部通过")


if __name__ == "__main__":
    main()
