import subprocess
import sys
import time

from pathlib import Path

from tests.offline import regression_environment


# ============================================================
# 1. 项目根目录
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ============================================================
# 2. 现有自动化测试
# ============================================================

TEST_MODULES = [
    "tests.regression.sql_security",
    "tests.regression.sql_tool",
    "tests.regression.sql_metadata_security",
    "tests.regression.sql_scope_regression",
    "tests.regression.rag_security",
    "tests.regression.thread_access",
    "tests.regression.session_http",
    "tests.regression.session_checkpoint",
]


# ============================================================
# 3. 逐个执行测试
# ============================================================

def run_tests():
    with regression_environment() as env:
        _run_tests(env)


def _run_tests(env):

    passed = 0
    failed = []

    for module in TEST_MODULES:

        print(f"\n[RUN] {module}", flush=True)

        start = time.perf_counter()

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    module,
                ],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )

        except subprocess.TimeoutExpired:
            print("[FAIL] 测试执行超时")
            failed.append(module)
            continue

        elapsed = time.perf_counter() - start

        if result.returncode == 0:
            passed += 1

            print(
                f"[PASS] {module} "
                f"耗时：{elapsed:.2f}s"
            )

        else:
            failed.append(module)

            print(f"[FAIL] {module}")

            # 失败时输出日志，方便定位问题
            print(result.stdout[-4000:])
            print(result.stderr[-4000:])

    # ========================================================
    # 4. 汇总测试结果
    # ========================================================

    print("\n========== 回归测试总结 ==========")

    print(f"测试模块：{len(TEST_MODULES)}")
    print(f"通过模块：{passed}")
    print(f"失败模块：{len(failed)}")

    if failed:
        print("\n失败列表：")

        for module in failed:
            print(f"- {module}")

        sys.exit(1)

    print("\n全部回归测试通过")


if __name__ == "__main__":
    run_tests()
