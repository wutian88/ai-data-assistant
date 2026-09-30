import logging

from pydantic import BaseModel
from mcp.server import MCPServer

from app.services.rag import rag_answer_with_usage
from app.services.sql_agent import sql_answer_with_usage


# ============================================================
# 1. 日志
#
# logging 默认写 stderr。
# MCP stdio 模式下 stdout 是协议通道，所以不要随便 print。
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# 2. MCP 返回的数据模型
# ============================================================

class TokenUsage(BaseModel):
    """
    单次业务链路中可观测到的 LLM Token Usage。

    使用 None 而不是 0 表示：
    当前调用没有拿到该项 usage 数据。
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None


class RAGResult(BaseModel):
    """
    RAG Tool 的结构化返回。
    """

    answer: str
    sources: list[str]
    usage: TokenUsage


class SQLResult(BaseModel):
    """
    SQL Tool 的结构化返回。
    """

    answer: str
    usage: TokenUsage


# ============================================================
# 3. 创建 MCP Server
# ============================================================

mcp = MCPServer(
    "Day18 Observable Business Server"
)


# ============================================================
# 4. RAG Tool
#
# LangGraph
#   ↓
# MCP Client
#   ↓
# knowledge_query
#   ↓
# rag_answer_with_usage()
#   ↓
# answer + sources + usage
# ============================================================

@mcp.tool(structured_output=True)
def knowledge_query(
    question: str
) -> RAGResult:

    try:

        result = rag_answer_with_usage(
            question
        )

        return RAGResult(
            answer=result["answer"],
            sources=result["sources"],
            usage=TokenUsage(
                **result["usage"]
            )
        )

    except Exception:

        # 记录完整 traceback，但继续把异常抛给 MCP。
        # 这样 Client 才能收到 is_error=True。
        logger.exception(
            "knowledge_query 执行失败"
        )

        raise


# ============================================================
# 5. SQL Tool
#
# SQL Agent 可能发生多次模型调用。
# sql_answer_with_usage() 会把当前能够观测到的
# AIMessage usage_metadata 汇总后返回。
# ============================================================

@mcp.tool(structured_output=True)
def business_query(
    question: str
) -> SQLResult:

    try:

        result = sql_answer_with_usage(
            question
        )

        return SQLResult(
            answer=result["answer"],
            usage=TokenUsage(
                **result["usage"]
            )
        )

    except Exception:

        logger.exception(
            "business_query 执行失败"
        )

        raise


# ============================================================
# 6. 启动 MCP Server
# ============================================================

if __name__ == "__main__":
    mcp.run()