"""Authenticated identity crosses MCP stdio through trusted child env only."""

import asyncio
import logging
import secrets
from types import SimpleNamespace

from app.mcp import client as mcp_client


def test_concurrent_session_identities_are_isolated_and_not_logged(monkeypatch, caplog):
    captured = []
    test_key_a = secrets.token_urlsafe(24)
    test_key_b = secrets.token_urlsafe(24)

    class FakeServerParameters:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeClient:
        def __init__(self, server):
            self.server = server

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def call_tool(self, name, arguments):
            await asyncio.sleep(0)
            captured.append((self.server.env.copy(), name, arguments.copy()))
            if arguments["question"] == "client failure":
                raise RuntimeError(f"private child failure user_b {test_key_b}")
            if arguments["question"] == "failing query":
                return SimpleNamespace(
                    is_error=True,
                    content=[SimpleNamespace(
                        text=f"private failure user_b {test_key_b}"
                    )],
                    structured_content=None,
                )
            return SimpleNamespace(
                is_error=False,
                structured_content={
                    "answer": "ok", "sources": [], "usage": {},
                    "child_identity": self.server.env.get("APP_AUTHENTICATED_USER_ID"),
                },
            )

    monkeypatch.setenv("APP_AUTHENTICATED_USER_ID", "attacker-controlled-parent-env")
    monkeypatch.setenv("SESSION_USER_A_KEY", test_key_a)
    monkeypatch.setenv("SESSION_USER_B_KEY", test_key_b)
    monkeypatch.setenv("DAY19_USER_A_KEY", "test-only-legacy-key-A")
    monkeypatch.setenv("DAY19_USER_B_KEY", "test-only-legacy-key-B")
    monkeypatch.setattr(mcp_client, "StdioServerParameters", FakeServerParameters)
    monkeypatch.setattr(mcp_client, "Client", FakeClient)

    async def run():
        return await asyncio.gather(
            mcp_client.call_mcp_tool("business_query", "orders for A", "user_a"),
            mcp_client.call_mcp_tool("business_query", "orders for B", "user_b"),
        )

    with caplog.at_level(logging.DEBUG):
        result_a, result_b = asyncio.run(run())

    assert result_a["child_identity"] == "user_a"
    assert result_b["child_identity"] == "user_b"
    assert len(captured) == 2
    forbidden_credentials = {
        "SESSION_USER_A_KEY", "SESSION_USER_B_KEY",
        "DAY19_USER_A_KEY", "DAY19_USER_B_KEY",
    }
    for env, tool, args in captured:
        expected = "user_a" if args["question"].endswith("A") else "user_b"
        assert env["APP_AUTHENTICATED_USER_ID"] == expected
        assert tool == "business_query"
        assert args == {"question": f"orders for {expected[-1].upper()}"}
        assert forbidden_credentials.isdisjoint(env)
        assert "attacker-controlled-parent-env" not in env.values()
        assert test_key_a not in env.values() and test_key_b not in env.values()
    assert "user_a" not in caplog.text and "user_b" not in caplog.text
    assert test_key_a not in caplog.text and test_key_b not in caplog.text

    async def run_rag_and_error():
        await mcp_client.call_mcp_tool("knowledge_query", "refund policy", "user_a")
        try:
            await mcp_client.call_mcp_tool("business_query", "failing query", "user_b")
        except RuntimeError as error:
            assert "MCP Tool 调用失败: business_query" == str(error)
            assert "user_b" not in str(error)
            assert test_key_b not in str(error)
            assert "private failure" not in str(error)
        else:
            raise AssertionError("expected sanitized synthetic MCP error")
        try:
            await mcp_client.call_mcp_tool("business_query", "client failure", "user_b")
        except RuntimeError as error:
            assert str(error) == "MCP 客户端错误: RuntimeError"
            assert "user_b" not in str(error)
            assert test_key_b not in str(error)
            assert "private child failure" not in str(error)
        else:
            raise AssertionError("expected sanitized synthetic MCP client failure")

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run_rag_and_error())
    rag_env, rag_tool, rag_args = captured[2]
    assert "APP_AUTHENTICATED_USER_ID" not in rag_env
    assert rag_tool == "knowledge_query"
    assert rag_args == {"question": "refund policy"}
    assert forbidden_credentials.isdisjoint(rag_env)
    assert "user_a" not in caplog.text and "user_b" not in caplog.text
    assert test_key_a not in caplog.text and test_key_b not in caplog.text
