from typing import Literal, cast
from pydantic import BaseModel

from app.core.llm import model


class RouteResult(BaseModel):
    route: Literal[
        "rag",
        "sql",
        "fallback"
    ]


# ==========================================
# 普通结构化 Router
# ==========================================

router_model = model.with_structured_output(
    RouteResult
)


# ==========================================
# 带原始 AIMessage 的 Router
# Day18 用于读取 usage_metadata
# ==========================================

router_model_with_raw = model.with_structured_output(
    RouteResult,
    include_raw=True
)
def build_router_prompt(
    question: str
) -> str:

    return f"""
你是一个问题路由器。如果用户问题信息不足、含义模糊，
或者既不属于知识库问答也不属于数据库查询，
返回 fallback。

rag：
查询文档、规则、制度、知识库。

sql：
查询用户、订单、商品等结构化数据，
以及统计、排名、聚合问题。

用户问题：
{question}
"""


# ==========================================
# 原来的函数
# 保持不变，避免影响 Day15 / Day17
# ==========================================

def route_question(
    question: str
) -> RouteResult:

    prompt = build_router_prompt(
        question
    )

    result = cast(
        RouteResult,
        router_model.invoke(prompt)
    )

    return result


# ==========================================
# Day18 新增：
# 同时返回 route + token usage
# ==========================================

def route_question_with_usage(
    question: str
) -> tuple[
    RouteResult,
    dict
]:

    prompt = build_router_prompt(
        question
    )

    result = cast(
        dict,
        router_model_with_raw.invoke(prompt)
    )

    parsed = cast(
        RouteResult,
        result["parsed"]
    )

    raw = result["raw"]

    usage = (
        raw.usage_metadata
        or {}
    )

    return parsed, usage