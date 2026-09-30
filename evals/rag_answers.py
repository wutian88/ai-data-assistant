import json
import time

from pathlib import Path
from unittest.mock import patch

from app.services import rag as rag_service
from app.services.vector_store import retriever


# ============================================================
# 1. 配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATA_FILE = BASE_DIR / "datasets" / "rag_answer_eval.json"

REPORT_FILE = BASE_DIR / "reports" / "rag_answer_report.json"


# ============================================================
# 2. 加载评测数据
# ============================================================

def load_cases():

    with DATA_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(file)


# ============================================================
# 3. 执行真实 RAG 评测
# ============================================================

def evaluate():

    cases = load_cases()

    if not cases:
        raise ValueError("评测数据不能为空")

    reports = []

    print("\n========== RAG 答案评测 ==========")

    for index, case in enumerate(cases, start=1):

        question = case["question"]

        print(f"\n========== 第 {index} 题 ==========")
        print("问题：", question)

        start = time.perf_counter()

        try:

            # ① 真实 Milvus 检索
            docs = retriever.invoke(question)

            # ② 使用同一批文档调用真实 RAG
            # 只替换检索器，不替换 LLM
            with patch.object(
                rag_service,
                "retriever",
            ) as mock_retriever:

                mock_retriever.invoke.return_value = docs

                result = (
                    rag_service.rag_answer_with_usage(
                        question
                    )
                )

            elapsed = time.perf_counter() - start

            # ③ 输出实际检索证据
            print("\n检索文档：")

            evidence = []

            for number, doc in enumerate(
                docs,
                start=1,
            ):

                source = doc.metadata.get(
                    "source",
                    "unknown",
                )

                evidence.append({
                    "source": source,
                    "content": doc.page_content,
                })

                print(
                    f"{number}. [{source}] "
                    f"{doc.page_content}"
                )

            # ④ 输出真实模型回答
            print("\n模型回答：")
            print(result["answer"])

            print("\n标准答案要点：")

            for fact in case["expected_facts"]:
                print("-", fact)

            print("\nToken：", result["usage"])
            print("耗时：", round(elapsed, 2), "秒")

            reports.append({
                "question": question,
                "expected_facts": case["expected_facts"],
                "answer": result["answer"],
                "sources": result["sources"],
                "evidence": evidence,
                "usage": result["usage"],
                "latency_seconds": round(elapsed, 3),
                "answer_correct": None,
                "supported_by_evidence": None,
            })

        except Exception as exc:

            print(
                "[ERROR]",
                type(exc).__name__,
                str(exc),
            )

            reports.append({
                "question": question,
                "error": str(exc),
            })

    # ========================================================
    # 4. 保存评测报告
    # ========================================================

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)

    REPORT_FILE.write_text(
        json.dumps(
            reports,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n========== 评测结束 ==========")
    print("报告：", REPORT_FILE)


if __name__ == "__main__":
    evaluate()