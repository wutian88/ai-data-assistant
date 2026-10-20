"""Regression：SQL 作用域与别名权限漏洞专项回归。

从项目根目录运行：python -m tests.regression.sql_scope_regression
本脚本只调用 SQL 校验器和模拟 Tool 执行，不访问数据库、不调用 LLM。
"""

import ast
from unittest.mock import patch
from app.services.sql_agent import (
    _scoped_query,
    build_sql_tools,
    execute_with_timeout,
    validate_sql,
    safe_sql_query,
)


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
    must_block("订单归属列不可直接投影", "SELECT user_id FROM orders", "禁止访问字段")
    must_block("users.id 不可直接投影", "SELECT id FROM users", "禁止访问字段")
    must_block("别名伪装不能改变真实表字段权限", "SELECT p.user_id FROM products AS p", "禁止访问字段")
    must_block(
        "LEFT JOIN 也不能投影右表归属列",
        "SELECT o.user_id FROM products p LEFT JOIN orders o ON p.id = o.product_id",
        "禁止访问字段",
    )
    must_block("归属列不能用于用户排序", "SELECT id FROM orders ORDER BY user_id", "禁止访问字段")
    must_block("归属列不能用于聚合分组", "SELECT COUNT(*) FROM orders GROUP BY user_id", "禁止访问字段")
    must_block("users.id 不能通过别名伪装直接投影", "SELECT u.id FROM users AS u", "禁止访问字段")
    print("\n========== 正常 SQL 仍可用 ==========\n")
    must_allow("简单 SELECT", "SELECT id, quantity FROM orders")
    must_allow("表别名", "SELECT o.id, o.quantity FROM orders AS o")
    must_allow("公共商品表允许使用别名", "SELECT p.name FROM products AS p")
    must_allow(
        "普通 JOIN ON",
        "SELECT u.name, o.quantity FROM users AS u "
        "JOIN orders AS o ON u.id = o.user_id",
    )
    must_allow("COUNT(*)", "SELECT COUNT(*) FROM orders")
    must_allow(
        "订单状态聚合与结果别名排序",
        "SELECT status, COUNT(*) AS total FROM orders GROUP BY status ORDER BY total DESC",
    )
    must_allow(
        "订单字段排序",
        "SELECT id, created_at FROM orders ORDER BY created_at DESC LIMIT 5",
    )
    must_allow(
        "限定订单日期的城市统计",
        "SELECT u.city, COUNT(*) AS total FROM users u JOIN orders o ON u.id = o.user_id "
        "WHERE o.created_at >= '2026-09-02' AND o.created_at < '2026-10-02' "
        "GROUP BY u.city ORDER BY total DESC, u.city",
    )
    must_allow(
        "月统计重复分组表达式",
        "SELECT strftime('%Y-%m', o.created_at) AS month, SUM(o.quantity) AS total "
        "FROM orders o GROUP BY strftime('%Y-%m', o.created_at) ORDER BY month",
    )
    must_block(
        "同名注册日期和订单日期必须限定表",
        "SELECT created_at FROM users u JOIN orders o ON u.id = o.user_id",
        "歧义",
    )
    must_block(
        "GROUP BY 不通过投影别名授权",
        "SELECT strftime('%Y-%m', created_at) AS month FROM orders GROUP BY month",
        "禁止访问字段",
    )
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

    print("\n========== 行级权限注入与参数绑定 ==========" )
    user_a_tool = build_sql_tools("user_a")[2]
    owner_count = execute_with_timeout(
        "SELECT COUNT(*) FROM orders WHERE user_id = 1"
    )[0][0]
    user_b_count = ast.literal_eval(build_sql_tools("user_b")[2].invoke({
        "query": "SELECT COUNT(*) FROM orders"
    }))[0][0]
    expected_user_b_count = execute_with_timeout(
        "SELECT COUNT(*) FROM orders WHERE user_id = 2"
    )[0][0]
    assert user_b_count == expected_user_b_count
    assert user_b_count > 0
    all_count = execute_with_timeout("SELECT COUNT(*) FROM orders")[0][0]
    scoped_owner_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM orders"
    }))[0][0]
    assert scoped_owner_count == owner_count > 0
    bypass_attempt = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM orders WHERE 1 = 1 OR 1 = 1"
    }))[0][0]
    assert bypass_attempt == owner_count and bypass_attempt < all_count
    print("[PASS] OR 1=1 仍受括号包裹后的归属条件约束")

    users_a_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM users"
    }))[0][0]
    users_b_count = ast.literal_eval(build_sql_tools("user_b")[2].invoke({
        "query": "SELECT COUNT(*) FROM users"
    }))[0][0]
    assert users_a_count == users_b_count == 1
    print("[PASS] users 表按各自服务端映射的 users.id 限制")

    scoped_sql, params = _scoped_query(
        "SELECT o.id FROM orders o JOIN users u ON o.user_id = u.id "
        "WHERE 1 = 1 OR 1 = 0",
        "user_a",
    )
    assert "__scope_user_0" in scoped_sql and "__scope_user_1" in scoped_sql
    assert params == {"__scope_user_0": 1, "__scope_user_1": 1}
    assert "1 = 1 OR 1 = 0" in scoped_sql
    joined_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id"
    }))[0][0]
    assert joined_count == owner_count
    assert joined_count < all_count
    joined_bypass_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM orders o JOIN users u ON o.user_id = u.id "
                 "WHERE 1 = 1 OR 1 = 1"
    }))[0][0]
    assert joined_bypass_count == owner_count and joined_bypass_count < all_count
    print("[PASS] JOIN 他人订单隔离：orders 与 users 各自别名单独注入归属条件")

    left_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(DISTINCT p.id) FROM products p "
                 "LEFT JOIN orders o ON p.id = o.product_id"
    }))[0][0]
    assert left_count == 200
    left_counts = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(DISTINCT p.id), COUNT(DISTINCT o.id) "
                 "FROM products p LEFT JOIN orders o ON p.id = o.product_id"
    }))[0]
    assert left_counts == (200, owner_count)
    print("[PASS] LEFT JOIN 归属条件留在 ON，商品维表仍保留无订单行")

    left_sql, _ = _scoped_query(
        "SELECT p.id FROM products p LEFT JOIN orders o ON p.id = o.product_id",
        "user_a",
    )
    assert "LEFT JOIN orders AS o ON (p.id = o.product_id) AND o.user_id = :__scope_user_0" in left_sql
    assert " WHERE " not in left_sql

    self_join_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM users u1 JOIN users u2 ON u1.city = u2.city"
    }))[0][0]
    assert self_join_count == 1
    unscoped_self_join_count = execute_with_timeout(
        "SELECT COUNT(*) FROM users u1 JOIN users u2 ON u1.city = u2.city"
    )[0][0]
    assert self_join_count < unscoped_self_join_count
    self_join_bypass_count = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT COUNT(*) FROM users u1 JOIN users u2 ON u1.city = u2.city "
                 "WHERE 1 = 1 OR 1 = 1"
    }))[0][0]
    assert self_join_bypass_count == self_join_count
    print("[PASS] 自连接的两个 users 别名都单独受限")

    assert ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT o.id FROM orders o ORDER BY o.created_at DESC LIMIT 5"
    }))
    aggregate_rows = ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT o.status, COUNT(*) AS total FROM orders o "
                 "GROUP BY o.status ORDER BY total DESC"
    }))
    assert aggregate_rows and sum(row[1] for row in aggregate_rows) == owner_count
    assert ast.literal_eval(user_a_tool.invoke({
        "query": "SELECT p.name FROM products AS p LIMIT 1"
    }))

    anonymous_tool = build_sql_tools(None)[2]
    assert anonymous_tool.invoke({"query": "SELECT COUNT(*) FROM orders"}).startswith(
        "SQL校验失败：SQL_IDENTITY_REQUIRED"
    )
    assert anonymous_tool.invoke({"query": "SELECT COUNT(*) FROM products"}) == "[(200,)]"
    print("[PASS] 无身份拒绝私有表，公共商品表仍可查询")

    print("\nSQL 作用域专项回归全部通过")


if __name__ == "__main__":
    main()
