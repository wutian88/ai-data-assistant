from app.services.sql_agent import (
    MAX_ROWS,
    add_limit,
    validate_sql,
)


def test_validate(
    name: str,
    sql: str,
    should_pass: bool,
) -> bool:
    """验证 SQL 安全校验是否符合预期。"""

    try:
        result = validate_sql(sql)

    except ValueError as e:
        if should_pass:
            print(f"[FAIL] {name}：意外拒绝：{e}")
            return False

        print(f"[PASS] {name}：已拒绝：{e}")
        return True

    if not should_pass:
        print(f"[FAIL] {name}：本应拒绝，却通过了")
        return False

    if not isinstance(result, str):
        print(f"[FAIL] {name}：返回值异常：{result!r}")
        return False

    print(f"[PASS] {name}：{result}")
    return True


def test_limit() -> bool:
    """验证 LIMIT 是否被强制封顶。"""

    print("\n========== LIMIT 测试 ==========")

    cases = [
        (
            "无 LIMIT",
            "SELECT id FROM orders",
            f"SELECT id FROM orders LIMIT {MAX_ROWS}",
        ),
        (
            "小 LIMIT",
            "SELECT id FROM orders LIMIT 10",
            "SELECT id FROM orders LIMIT 10",
        ),
        (
            "超大 LIMIT",
            "SELECT id FROM orders LIMIT 1000000",
            f"SELECT id FROM orders LIMIT {MAX_ROWS}",
        ),
    ]

    all_passed = True

    for name, sql, expected in cases:
        actual = add_limit(sql, max_rows=MAX_ROWS)

        if actual == expected:
            print(f"[PASS] {name}：{actual}")
        else:
            print(f"[FAIL] {name}")
            print(f"  实际：{actual}")
            print(f"  预期：{expected}")
            all_passed = False

    return all_passed


def main():
    print("========== SQL 安全回归 ==========\n")

    cases = [
        (
            "正常 SELECT",
            "SELECT id, quantity FROM orders",
            True,
        ),
        (
            "DELETE",
            "DELETE FROM orders",
            False,
        ),
        (
            "UPDATE",
            "UPDATE orders SET quantity = 1",
            False,
        ),
        (
            "DROP",
            "DROP TABLE orders",
            False,
        ),
        (
            "禁止访问非白名单表",
            "SELECT name FROM sqlite_master",
            False,
        ),
        (
            "多语句攻击",
            "SELECT id FROM orders; DROP TABLE orders",
            False,
        ),
        (
            "两个 SELECT",
            "SELECT id FROM orders; SELECT id FROM users",
            False,
        ),
        (
            "允许字段",
            "SELECT id, quantity FROM orders",
            True,
        ),
        (
            "表别名字段",
            "SELECT o.id, o.quantity FROM orders AS o",
            True,
        ),
        (
            "禁止未授权字段",
            "SELECT password FROM users",
            False,
        ),
        (
            "禁止 SELECT *",
            "SELECT * FROM users",
            False,
        ),
        (
            "COUNT(*) 允许",
            "SELECT COUNT(*) FROM orders",
            True,
        ),
        (
            "允许用户注册时间",
            "SELECT COUNT(*) FROM users WHERE created_at >= '2026-09-02' AND created_at < '2026-10-02'",
            True,
        ),
        (
            "允许订单状态分组",
            "SELECT status, COUNT(*) FROM orders GROUP BY status",
            True,
        ),
        (
            "允许商品类别金额估算",
            "SELECT p.category, ROUND(SUM(o.quantity * p.price), 2) AS amount FROM orders o JOIN products p ON o.product_id = p.id WHERE o.status != 'cancelled' GROUP BY p.category ORDER BY amount DESC",
            True,
        ),
        (
            "元数据仍禁止访问",
            "SELECT value FROM demo_metadata",
            False,
        ),
        (
            "新增字段不能授权其他表字段",
            "SELECT status FROM users",
            False,
        ),
    ]

    results = []

    for name, sql, should_pass in cases:
        passed = test_validate(
            name,
            sql,
            should_pass,
        )
        results.append(passed)

    results.append(test_limit())

    print("\n========== 测试总结 ==========")

    passed_count = sum(results)
    total_count = len(results)

    print(f"通过：{passed_count}/{total_count}")

    if not all(results):
        raise SystemExit(1)

    print("SQL 安全回归测试全部通过")


if __name__ == "__main__":
    main()
