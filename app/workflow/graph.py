"""Existing question routing workflow and graph builder."""
import logging
import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.workflow.router import route_question_with_usage
from app.mcp.client import call_mcp_tool, get_error_code
from app.core.observability import extract_usage, empty_service_usage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)

# SQL Agent 本身可能包含多轮模型 / Tool 调用，30 秒容易误伤。
MCP_TIMEOUT_SECONDS = 45
RECURSION_LIMIT = 20

RouteName = Literal["rag", "sql", "fallback"]


class AppState(TypedDict):
    request_id: str
    question: str

    route: RouteName | None
    answer: str
    sources: list[str]

    error: str | None
    error_code: str | None

    router_input_tokens: int | None
    router_output_tokens: int | None
    router_total_tokens: int | None
    router_reasoning_tokens: int | None

    service_input_tokens: int | None
    service_output_tokens: int | None
    service_total_tokens: int | None
    service_reasoning_tokens: int | None



def classify_node(state: AppState) -> dict[str, Any]:
    request_id = state["request_id"]
    start = time.perf_counter()

    logger.info(
        "[%s] classify_node start",
        request_id,
    )

    try:
        route_result, usage = route_question_with_usage(
            state["question"]
        )

        route = route_result.route
        token_data = extract_usage(
            usage,
            prefix="router",
        )
        cost = time.perf_counter() - start

        if route not in {"rag", "sql", "fallback"}:
            logger.error(
                "[%s] classify_node end | status=error | "
                "error_code=INVALID_ROUTE | cost=%.3fs | total_tokens=%s",
                request_id,
                cost,
                token_data["router_total_tokens"],
            )

            return {
                "route": "fallback",
                "error": f"非法路由结果: {route}",
                "error_code": "INVALID_ROUTE",
                **token_data,
            }

        logger.info(
            "[%s] classify_node end | route=%s | status=success | cost=%.3fs | "
            "input_tokens=%s | output_tokens=%s | total_tokens=%s | reasoning_tokens=%s",
            request_id,
            route,
            cost,
            token_data["router_input_tokens"],
            token_data["router_output_tokens"],
            token_data["router_total_tokens"],
            token_data["router_reasoning_tokens"],
        )

        return {
            "route": route,
            "error": None,
            "error_code": None,
            **token_data,
        }

    except Exception as e:
        cost = time.perf_counter() - start

        logger.exception(
            "[%s] classify_node end | status=error | "
            "error_code=ROUTER_ERROR | cost=%.3fs",
            request_id,
            cost,
        )

        return {
            "route": "fallback",
            "error": str(e),
            "error_code": "ROUTER_ERROR",
            "router_input_tokens": None,
            "router_output_tokens": None,
            "router_total_tokens": None,
            "router_reasoning_tokens": None,
        }



async def rag_node(state: AppState) -> dict[str, Any]:
    request_id = state["request_id"]
    start = time.perf_counter()

    logger.info(
        "[%s] rag_node start",
        request_id,
    )

    try:
        result = await call_mcp_tool(
            "knowledge_query",
            state["question"],
        )

        token_data = extract_usage(
            result.get("usage"),
            prefix="service",
        )
        cost = time.perf_counter() - start

        logger.info(
            "[%s] rag_node end | status=success | cost=%.3fs | "
            "input_tokens=%s | output_tokens=%s | total_tokens=%s | reasoning_tokens=%s",
            request_id,
            cost,
            token_data["service_input_tokens"],
            token_data["service_output_tokens"],
            token_data["service_total_tokens"],
            token_data["service_reasoning_tokens"],
        )

        return {
            "answer": result["answer"],
            "sources": result["sources"],
            "error": None,
            "error_code": None,
            **token_data,
        }

    except Exception as e:
        cost = time.perf_counter() - start
        error_code = get_error_code(e)

        logger.exception(
            "[%s] rag_node end | status=error | error_code=%s | cost=%.3fs",
            request_id,
            error_code,
            cost,
        )

        return {
            "answer": "知识库查询暂时无法完成。",
            "sources": [],
            "error": str(e),
            "error_code": error_code,
            **empty_service_usage(),
        }



async def sql_node(state: AppState) -> dict[str, Any]:
    request_id = state["request_id"]
    start = time.perf_counter()

    logger.info(
        "[%s] sql_node start",
        request_id,
    )

    try:
        result = await call_mcp_tool(
            "business_query",
            state["question"],
        )

        token_data = extract_usage(
            result.get("usage"),
            prefix="service",
        )
        cost = time.perf_counter() - start

        logger.info(
            "[%s] sql_node end | status=success | cost=%.3fs | "
            "input_tokens=%s | output_tokens=%s | total_tokens=%s | reasoning_tokens=%s",
            request_id,
            cost,
            token_data["service_input_tokens"],
            token_data["service_output_tokens"],
            token_data["service_total_tokens"],
            token_data["service_reasoning_tokens"],
        )

        return {
            "answer": result["answer"],
            "sources": [],
            "error": None,
            "error_code": None,
            **token_data,
        }

    except Exception as e:
        cost = time.perf_counter() - start
        error_code = get_error_code(e)

        logger.exception(
            "[%s] sql_node end | status=error | error_code=%s | cost=%.3fs",
            request_id,
            error_code,
            cost,
        )

        return {
            "answer": "数据库查询暂时无法完成。",
            "sources": [],
            "error": str(e),
            "error_code": error_code,
            **empty_service_usage(),
        }



def fallback_node(state: AppState) -> dict[str, str]:
    request_id = state["request_id"]
    start = time.perf_counter()

    logger.info(
        "[%s] fallback_node start",
        request_id,
    )

    if state["error"]:
        answer = "当前请求无法正常处理，请稍后重试。"
        status = "error"
    else:
        answer = "当前信息不足，请补充你想查询的内容。"
        status = "success"

    cost = time.perf_counter() - start

    logger.info(
        "[%s] fallback_node end | status=%s | error_code=%s | cost=%.3fs",
        request_id,
        status,
        state["error_code"],
        cost,
    )

    return {
        "answer": answer,
    }



def route_next(state: AppState) -> RouteName:
    route = state["route"]

    if route not in {"rag", "sql", "fallback"}:
        return "fallback"

    return route



graph = StateGraph(AppState)

graph.add_node("classify", classify_node)
graph.add_node("rag", rag_node)
graph.add_node("sql", sql_node)
graph.add_node("fallback", fallback_node)

graph.add_edge(START, "classify")

graph.add_conditional_edges(
    "classify",
    route_next,
    {
        "rag": "rag",
        "sql": "sql",
        "fallback": "fallback",
    },
)

graph.add_edge("rag", END)
graph.add_edge("sql", END)
graph.add_edge("fallback", END)

app = graph.compile()
