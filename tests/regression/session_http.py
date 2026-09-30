"""Regression：受保护会话的 HTTP 权限测试（零 LLM / 零 MCP）。

运行：python -m tests.regression.session_http
独立构造最小 FastAPI 测试容器，验证真实的 session_api 路由与身份校验。
实际 main.py 的 Checkpointer 连通性另由 test_session_checkpoint 测试。
"""

import asyncio
import os
import secrets
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from fastapi.testclient import TestClient

from app.api.sessions import create_session_router
from app.core.thread_access import ThreadAccessStore


class FakeGraph:
    """仅替换耗费 Token 的业务 Graph；会话路由、SQLite 归属和 HTTP 全是真实代码。"""

    def __init__(self):
        self.get_calls = []
        self.invoke_calls = []
        self.saved = {}

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        self.get_calls.append(tid)
        return SimpleNamespace(values=self.saved.get(tid, {}))

    async def ainvoke(self, state, config):
        tid = config["configurable"]["thread_id"]
        self.invoke_calls.append(tid)
        output = {
            **state,
            "route": "fallback",
            "answer": "测试回答",
        }
        self.saved[tid] = output
        return output


class SessionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "DAY19_USER_A_KEY": "test-A-very-long-unique-session-secret",
                "DAY19_USER_B_KEY": "test-B-very-long-unique-session-secret",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.fake_graph = FakeGraph()
        app = FastAPI()
        app.state.thread_store = ThreadAccessStore(
            Path(self.tmpdir.name) / "owners.db"
        )
        app.state.session_graph = self.fake_graph
        app.state.session_recursion_limit = 20
        app.state.thread_locks = {}
        app.state.thread_locks_guard = asyncio.Lock()

        app_header = APIKeyHeader(name="X-API-Key", auto_error=False)

        def verify_app_key(key: str | None = Security(app_header)) -> str:
            if not key or not secrets.compare_digest(key, "test-app-secret"):
                raise HTTPException(status_code=401, detail="Invalid API Key")
            return key

        async def no_rate_limit():
            return None

        async def no_concurrency_limit():
            yield

        app.middleware("http")(
            self._create_request_id_middleware
        )
        app.include_router(create_session_router(
            verify_app_key, no_rate_limit, no_concurrency_limit,
        ))
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    @staticmethod
    async def _create_request_id_middleware(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        return await call_next(request)

    def headers(self, user="a", app_key="test-app-secret"):
        return {
            "X-API-Key": app_key,
            "X-Session-Key": (
                "test-A-very-long-unique-session-secret"
                if user == "a" else "test-B-very-long-unique-session-secret"
            ),
        }

    def new_thread(self):
        response = self.client.post("/threads", headers=self.headers("a"))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["thread_id"]

    def test_owner_can_create_read_and_ask(self):
        tid = self.new_thread()
        first = self.client.get(f"/threads/{tid}", headers=self.headers("a"))
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["started"])
        asked = self.client.post(
            f"/threads/{tid}/ask",
            headers=self.headers("a"),
            json={"question": "测试问题"},
        )
        self.assertEqual(asked.status_code, 200, asked.text)
        self.assertEqual(self.fake_graph.invoke_calls, [tid])
        state = self.client.get(f"/threads/{tid}", headers=self.headers("a"))
        self.assertEqual(state.json()["question"], "测试问题")

    def test_foreign_user_blocked_before_touching_graph(self):
        tid = self.new_thread()
        read = self.client.get(f"/threads/{tid}", headers=self.headers("b"))
        ask = self.client.post(
            f"/threads/{tid}/ask", headers=self.headers("b"),
            json={"question": "不应执行"},
        )
        self.assertEqual(read.status_code, 404)
        self.assertEqual(ask.status_code, 404)
        self.assertEqual(self.fake_graph.get_calls, [])
        self.assertEqual(self.fake_graph.invoke_calls, [])

    def test_missing_credentials_rejected(self):
        self.assertEqual(self.client.post("/threads").status_code, 401)
        self.assertEqual(
            self.client.post("/threads", headers={"X-API-Key": "test-app-secret"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.post("/threads", headers=self.headers("a", "wrong-app-key")).status_code,
            401,
        )

    def test_unknown_and_foreign_return_same_error(self):
        tid = self.new_thread()
        foreign = self.client.get(f"/threads/{tid}", headers=self.headers("b"))
        unknown = self.client.get(
            f"/threads/{uuid.uuid4()}", headers=self.headers("b")
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(foreign.json(), unknown.json())

    def test_blank_question_rejected_before_graph(self):
        tid = self.new_thread()
        response = self.client.post(
            f"/threads/{tid}/ask", headers=self.headers("a"),
            json={"question": "   "},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.fake_graph.invoke_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
