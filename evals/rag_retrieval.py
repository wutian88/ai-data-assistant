import csv
import json
import time

from pathlib import Path

from app.services.vector_store import retriever


# ============================================================
# 1. 路径配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_FILE = BASE_DIR / "datasets" / "rag_eval.json"
REPORT_FILE = BASE_DIR / "reports" / "rag_retrieval_report.csv"


# ============================================================
# 2. RAG 检索评测
# ============================================================

def evaluate():

    cases = json.loads(
        DATA_FILE.read_text(encoding="utf-8")
    )

    if not cases:
        raise ValueError("评测数据集不能为空")

    results = []
    hit_count = 0

    print("\n========== RAG 检索评测 ==========")

    for index, case in enumerate(cases, start=1):

        question = case["question"]
        expected = case["expected_source"]

        start = time.perf_counter()

        sources = []
        documents = []
        error = ""

        try:
            # 调用真实 Milvus Retriever
            documents = retriever.invoke(question)

            sources = [
                doc.metadata.get("source", "unknown")
                for doc in documents
            ]

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        elapsed = time.perf_counter() - start

        # 判断正确来源是否出现在检索结果中
        hit = not error and expected in sources

        if hit:
            hit_count += 1

        print(f"\n第 {index} 题：{question}")
        print("预期来源：", expected)
        print("实际来源：", sources)
        print("是否命中：", hit)

        # 显示文档内容，方便人工核查相关性
        for number, doc in enumerate(documents, start=1):

            print(
                f"文档 {number}：",
                doc.page_content[:200]
            )

        if error:
            print("异常：", error)

        results.append({
            "question": question,
            "expected_source": expected,
            "retrieved_sources": json.dumps(
                sources,
                ensure_ascii=False,
            ),
            "hit": hit,
            "latency_seconds": round(elapsed, 3),
            "error": error,
        })

    # ========================================================
    # 3. 保存 CSV 报告
    # ============================================================

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=list(results[0].keys()),
        )

        writer.writeheader()
        writer.writerows(results)

    # ========================================================
    # 4. 计算命中率
    # ============================================================

    hit_rate = hit_count / len(cases)

    print("\n========== 评测总结 ==========")
    print("测试数量：", len(cases))
    print("命中数量：", hit_count)
    print(f"Hit Rate@3：{hit_rate:.2%}")
    print("报告：", REPORT_FILE)


if __name__ == "__main__":
    evaluate()