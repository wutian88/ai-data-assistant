"""Regression：复杂 SQL 权限边界探索（只解析 SQL，不执行数据库查询）。

运行：python -m tests.regression.sql_complex_security
重点：嵌套查询中的别名不应该为外层查询提供字段权限。
"""

from app.services.sql_agent import validate_sql


def is_allowed(sql: str) -> tuple[bool, str]:
    """仅调用 AST 校验；不会执行 SQL，也不会调用大模型。"""
    try:
        validate_sql(sql)
        return True, "校验放行"
    except ValueError as exc:
        return False, str(exc)


def main() -> None:
    print("========== 复杂 SQL 权限边界测试 ==========\n")

    # 严格要求拒绝的情况：如果放行，就说明校验器可能存在权限漏洞。
    blocked_cases = [
        (
            "嵌套查询别名不能授权外层敏感字段",
            "SELECT password FROM users "
            "WHERE EXISTS (SELECT id AS password FROM orders)",
        ),
        (
            "嵌套查询禁止访问非白名单表",
            "SELECT name FROM users "
            "WHERE EXISTS (SELECT 1 FROM sqlite_master)",
        ),
        (
            "UNION 不得绕过语句类型限制",
            "SELECT name FROM users UNION SELECT name FROM users",
        ),
    ]

    failures = 0
    for title, sql in blocked_cases:
        allowed, detail = is_allowed(sql)
        if allowed:
            failures += 1
            print(f"[FAIL] {title}：校验器错误放行")
            print(f"       SQL: {sql}")
        else:
            print(f"[PASS] {title}：{detail}")

    # 探索性兼容检查：拒绝不会导致信息泄露，但说明现阶段不支持该语法。
    optional_cases = [
        (
            "普通 JOIN",
            "SELECT u.name, o.quantity FROM users AS u "
            "JOIN orders AS o ON u.id = o.user_id",
        ),
        (
            "关联子查询",
            "SELECT u.name FROM users AS u "
            "WHERE EXISTS (SELECT 1 FROM orders AS o WHERE o.user_id = u.id)",
        ),
        (
            "CTE 公共表表达式",
            "WITH t AS (SELECT id FROM users) SELECT id FROM t",
        ),
    ]
    print("\n========== 可用语法探索（拒绝不代表安全失败） ==========\n")
    for title, sql in optional_cases:
        allowed, detail = is_allowed(sql)
        print(f"[{'SUPPORTED' if allowed else 'NOT SUPPORTED'}] {title}：{detail}")

    print("\n========== 测试总结 ==========")
    if failures:
        print(f"发现 {failures} 项权限边界问题。暂勿把当前校验器用作生产级 SQL 沙箱。")
        raise SystemExit(1)
    print("本轮权限拒绝用例通过。仍不代表已覆盖所有 SQL 语法。")


if __name__ == "__main__":
    main()
