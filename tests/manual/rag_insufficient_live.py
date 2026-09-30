from unittest.mock import patch

from langchain_core.documents import Document

from app.services import rag as rag_service


QUESTION = "公司的退款期限是多少天？"


UNRELATED_DOCUMENT = Document(
    page_content=(
        "公司办公时间：\n"
        "周一至周五。\n"
        "上午9点到下午6点。\n"
        "周末不办公。"
    ),
    metadata={
        "source": "day19_unrelated_test"
    },
)


def main():

    # 模拟检索到与问题无关的文档
    # LLM 仍然使用真实模型
    with patch.object(
        rag_service,
        "retriever",
    ) as mock_retriever:

        mock_retriever.invoke.return_value = [
            UNRELATED_DOCUMENT
        ]

        result = (
            rag_service.rag_answer_with_usage(
                QUESTION
            )
        )

    print("========== 证据不足测试 ==========")

    print("\n用户问题：")
    print(QUESTION)

    print("\n检索文档：")
    print(UNRELATED_DOCUMENT.page_content)

    print("\n模型回答：")
    print(result["answer"])

    print("\n来源：")
    print(result["sources"])

    print("\nToken：")
    print(
        result["usage"]["total_tokens"]
    )


if __name__ == "__main__":
    main()