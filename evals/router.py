import csv
import json
import time

from pathlib import Path

from app.workflow.router import route_question_with_usage


# ============================================================
# 1. 路径配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_FILE = BASE_DIR / "datasets" / "router_eval.json"

REPORT_FILE = BASE_DIR / "reports" / "router_report.csv"


# ============================================================
# 2. 读取评测数据
# ============================================================

def load_cases() -> list[dict]:

    with DATA_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        cases = json.load(file)

    return cases


# ============================================================
# 3. Router 效果评测
# ============================================================

def evaluate_router():

    cases = load_cases()

    results = []

    correct_count = 0

    print("\n========== Router 效果评测 ==========")

    for index, case in enumerate(cases, start=1):

        question = case["question"]

        expected = case["expected_route"]

        start = time.perf_counter()

        actual = None
        error = ""

        try:

            # 调用项目中真实的 Router
            route_result, usage = (
                route_question_with_usage(
                    question
                )
            )

            actual = route_result.route

        except Exception as exc:

            error = type(exc).__name__

        elapsed = time.perf_counter() - start

        # 分类正确才计入正确数量
        is_correct = (
            not error
            and actual == expected
        )

        if is_correct:
            correct_count += 1

        status = (
            "PASS"
            if is_correct
            else "FAIL"
        )

        print(
            f"\n[{status}] 第 {index} 题"
        )

        print("问题：", question)
        print("预期：", expected)
        print("实际：", actual)
        print("耗时：", round(elapsed, 2), "秒")

        if error:
            print("异常：", error)

        results.append({
            "question": question,
            "expected_route": expected,
            "actual_route": actual or "",
            "correct": is_correct,
            "latency_seconds": round(elapsed, 3),
            "error": error,
        })

    # ========================================================
    # 4. 生成 CSV 报告
    # ========================================================

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "question",
                "expected_route",
                "actual_route",
                "correct",
                "latency_seconds",
                "error",
            ],
        )

        writer.writeheader()

        writer.writerows(results)

    # ========================================================
    # 5. 统计正确率
    # ========================================================

    total = len(cases)

    accuracy = (
        correct_count / total
        if total
        else 0
    )

    print("\n========== 评测总结 ==========")

    print("测试题数：", total)

    print("正确数量：", correct_count)

    print(
        "正确率：",
        f"{accuracy:.2%}"
    )

    print("报告位置：", REPORT_FILE)


if __name__ == "__main__":
    evaluate_router()