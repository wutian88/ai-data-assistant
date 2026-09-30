"""Existing observable LLM token usage; missing values remain None."""
from typing import Any, Literal


def extract_usage(
    usage: dict[str, Any] | None,
    prefix: Literal["router", "service"],
) -> dict[str, int | None]:
    """
    把不同来源的 usage 统一转换成 AppState 字段。

    Router 的 reasoning 位于：
        output_token_details.reasoning

    Day18 MCP Server 已把 reasoning 扁平化成：
        reasoning_tokens
    """

    usage = usage or {}
    output_details = usage.get("output_token_details") or {}

    reasoning_tokens = usage.get("reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = output_details.get("reasoning")

    return {
        f"{prefix}_input_tokens": usage.get("input_tokens"),
        f"{prefix}_output_tokens": usage.get("output_tokens"),
        f"{prefix}_total_tokens": usage.get("total_tokens"),
        f"{prefix}_reasoning_tokens": reasoning_tokens,
    }



def empty_service_usage() -> dict[str, None]:
    """服务未调用或没有拿到 usage 时，明确记录为 None，而不是猜成 0。"""

    return {
        "service_input_tokens": None,
        "service_output_tokens": None,
        "service_total_tokens": None,
        "service_reasoning_tokens": None,
    }



def add_if_complete(
    first: int | None,
    second: int | None,
) -> int | None:
    """只有两部分数据都存在时才相加；缺任何一项都返回 None。"""

    if first is None or second is None:
        return None

    return first + second



def build_observed_request_usage(
    result: dict[str, Any],
) -> dict[str, int | None]:
    """
    计算当前“可观测到的”请求级 LLM Token Usage。

    fallback 正常情况下只调用 Router，因此 Router Usage 就是请求 Usage。
    rag/sql 需要 Router + Service 两部分都存在才能计算完整值。
    """

    route = result.get("route")

    router_input = result.get("router_input_tokens")
    router_output = result.get("router_output_tokens")
    router_total = result.get("router_total_tokens")
    router_reasoning = result.get("router_reasoning_tokens")

    if route == "fallback":
        return {
            "input_tokens": router_input,
            "output_tokens": router_output,
            "total_tokens": router_total,
            "reasoning_tokens": router_reasoning,
        }

    return {
        "input_tokens": add_if_complete(
            router_input,
            result.get("service_input_tokens"),
        ),
        "output_tokens": add_if_complete(
            router_output,
            result.get("service_output_tokens"),
        ),
        "total_tokens": add_if_complete(
            router_total,
            result.get("service_total_tokens"),
        ),
        "reasoning_tokens": add_if_complete(
            router_reasoning,
            result.get("service_reasoning_tokens"),
        ),
    }