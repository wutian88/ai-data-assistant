"""MCP stdio client for the existing business tools."""
import os
import sys
from typing import Any

import anyio
from mcp import Client, StdioServerParameters

MCP_TIMEOUT_SECONDS = 45

async def call_mcp_tool(
    tool_name: str,
    question: str,
) -> dict[str, Any]:
    """通过 stdio 调用 正式可观测 MCP Server。"""

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        # 让子进程继承当前服务环境变量（包括模型 API 配置）。
        env=os.environ.copy(),
    )

    result = None
    timed_out = False

    async with Client(server) as client:
        try:
            with anyio.fail_after(MCP_TIMEOUT_SECONDS):
                result = await client.call_tool(
                    tool_name,
                    {"question": question},
                )
        except TimeoutError:
            # 先让 MCP Client 正常退出上下文，再在外部抛异常。
            # 这样可以避免清理阶段把异常包装成 ExceptionGroup。
            timed_out = True

    if timed_out:
        raise RuntimeError(
            f"MCP Tool 调用超时: {tool_name}"
        )

    if result is None:
        raise RuntimeError(
            "MCP Tool 没有返回结果"
        )

    if result.is_error:
        raise RuntimeError(
            f"MCP Tool 调用失败: {result.content}"
        )

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

    if "MCP Tool 调用失败" in message:
        return "MCP_TOOL_ERROR"

    if "MCP Tool 没有返回结果" in message:
        return "MCP_NO_RESULT"

    if "MCP 没有返回结构化结果" in message:
        return "MCP_INVALID_RESULT"

    return "INTERNAL_ERROR"