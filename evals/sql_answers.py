import ast
import json
import time

from pathlib import Path

from app.services.sql_agent import (
    safe_sql_query,
    sql_answer_with_usage,
)


# ============================================================
# 1. 固定评测问题
# ============================================================

QUESTION = "所有订单一共购买了多少件商品？"

GOLD_SQL = "SELECT SUM(quantity) FROM orders"

REPORT_FILE = (
    Path(__file__).resolve().parent
    / "reports"
    / "sql_answer_report.json"
)


# ============================================================
# 2. 获取标准结果
# ============================================================

print("\n========== SQL Agent 效果评测 ==========")

print("\n问题：", QUESTION)

gold_output = safe_sql_query.invoke({
    "query": GOLD_SQL,
})

# safe_sql_query 当前返回的是列表的字符串表示，
# 例如 "[(5,)]"，使用 literal_eval 解析。
gold_rows = ast.literal_eval(gold_output)

gold_count = gold_rows[0][0]

print("标准 SQL：", GOLD_SQL)
print("标准结果：", gold_count)


# ============================================================
# 3. 调用真实 SQL Agent
# ============================================================

start = time.perf_counter()

result = sql_answer_with_usage(
    QUESTION
)

elapsed = time.perf_counter() - start

print("\nSQL Agent 回答：")
print(result["answer"])

print("\nToken：")
print(result["usage"])

print("\n耗时：")
print(round(elapsed, 2), "秒")


# ============================================================
# 4. 保存人工评测报告
# ============================================================

report = {
    "question": QUESTION,
    "gold_sql": GOLD_SQL,
    "gold_result": gold_count,
    "agent_answer": result["answer"],
    "usage": result["usage"],
    "latency_seconds": round(elapsed, 3),

    # 暂时不通过简单关键词匹配判断答案
    "answer_correct": None,
}

REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

REPORT_FILE.write_text(
    json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)

print("\n报告已保存：", REPORT_FILE)