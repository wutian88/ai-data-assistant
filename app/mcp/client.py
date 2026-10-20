"""MCP stdio client for the existing business tools."""
import os
import sys
from typing import Any

import anyio
from mcp import Client, StdioServerParameters
from app.core.config import MCP_TIMEOUT_SECONDS

async def call_mcp_tool(
    tool_name: str,
    question: str,
    authenticated_user_id: str | None = None,
) -> dict[str, Any]:
    """通过 stdio 调用 正式可观测 MCP Server。"""

    child_env = os.environ.copy()
    # Never inherit a stale ambient identity. The value comes from an already
    # authenticated server-side request state and is not part of tool arguments.
    child_env.pop("APP_AUTHENTICATED_USER_ID", None)
    # The child receives only the resolved identity, never the caller's secrets.
    for credential_name in (
        "SESSION_USER_A_KEY", "SESSION_USER_B_KEY",
        "DAY19_USER_A_KEY", "DAY19_USER_B_KEY",
    ):
        child_env.pop(credential_name, None)
    if tool_name == "business_query" and authenticated_user_id in {"user_a", "user_b"}:
        child_env["APP_AUTHENTICATED_USER_ID"] = authenticated_user_id

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        # 让子进程继承当前服务环境变量（包括模型 API 配置）。
        env=child_env,
    )

    result = None
    try:
        async with Client(server) as client:
            with anyio.fail_after(MCP_TIMEOUT_SECONDS):
                result = await client.call_tool(
                    tool_name,
                    {"question": question},
                )
    except TimeoutError:
        raise RuntimeError(f"MCP Tool 调用超时: {tool_name}") from None
    except Exception as error:
        # Do not propagate subprocess/client exception text: it may contain
        # environment details supplied to the child process.
        raise RuntimeError(
            f"MCP 客户端错误: {type(error).__name__}"
        ) from None

    if result is None:
        raise RuntimeError(
            "MCP Tool 没有返回结果"
        )

    if result.is_error:
        # Tool error content can echo query context or process data; keep the
        # public exception stable and never forward child details or identity.
        raise RuntimeError(f"MCP Tool 调用失败: {tool_name}")

    if result.structured_content is None:
        raise RuntimeError(
            "MCP 没有返回结构化结果"
        )

    return result.structured_content



def get_error_code(error: Exception) -> str:
    """把具体异常转换成稳定、便于统计的错误码。"""

    message = str(error)

    if "MCP Tool 调用超时" in message:
        return "MCP_TIMEOUT"

    if "SQL_TIMEOUT" in message:
        return "SQL_TIMEOUT"

    if "MCP Tool 调用失败" in message:
        return "MCP_TOOL_ERROR"

    if "MCP Tool 没有返回结果" in message:
        return "MCP_NO_RESULT"

    if "MCP 没有返回结构化结果" in message:
        return "MCP_INVALID_RESULT"

    return "INTERNAL_ERROR"
