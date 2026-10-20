"""Regression：受保护会话的 HTTP 权限测试（零 LLM / 零 MCP）。

运行：python -m tests.regression.session_http
独立构造最小 FastAPI 测试容器，验证真实的 session_api 路由与身份校验。
实际 main.py 的 Checkpointer 连通性另由 test_session_checkpoint 测试。
"""

import asyncio
import os
import secrets
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from fastapi.testclient import TestClient
import httpx

from app.api.sessions import create_session_router
from app.core.thread_access import ThreadAccessStore


class FakeGraph:
    """仅替换耗费 Token 的业务 Graph；会话路由、SQLite 归属和 HTTP 全是真实代码。"""

    def __init__(self):
        self.get_calls = []
        self.invoke_calls = []
        self.invoke_states = []
        self.saved = {}
        self.block_next = False
        self.raise_next = False
        self.entered = threading.Event()
        self.release = threading.Event()

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        self.get_calls.append(tid)
        return SimpleNamespace(values=self.saved.get(tid, {}))

    async def ainvoke(self, state, config):
        tid = config["configurable"]["thread_id"]
        self.invoke_calls.append(tid)
        self.invoke_states.append(state)
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("synthetic graph failure")
        if self.block_next:
            self.entered.set()
            await asyncio.to_thread(self.release.wait, 3)
            self.block_next = False
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
        self.session_key_a = secrets.token_urlsafe(24)
        self.session_key_b = secrets.token_urlsafe(24)
        self.env = patch.dict(
            os.environ,
            {
                "SESSION_USER_A_KEY": self.session_key_a,
                "SESSION_USER_B_KEY": self.session_key_b,
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.fake_graph = FakeGraph()
        app = FastAPI()
        self.app = app
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
            "X-Session-Key": self.session_key_a if user == "a" else self.session_key_b,
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
        self.assertEqual(self.fake_graph.invoke_states[0]["user_id"], "user_a")
        self.assertFalse(self.app.state.thread_locks[tid].locked())
        state = self.client.get(f"/threads/{tid}", headers=self.headers("a"))
        self.assertEqual(state.json()["question"], "测试问题")

    def test_thread_lock_releases_after_graph_exception(self):
        tid = self.new_thread()
        self.fake_graph.raise_next = True
        with self.assertRaises(RuntimeError):
            self.client.post(
                f"/threads/{tid}/ask", headers=self.headers("a"),
                json={"question": "first"},
            )
        self.assertFalse(self.app.state.thread_locks[tid].locked())
        recovered = self.client.post(
            f"/threads/{tid}/ask", headers=self.headers("a"),
            json={"question": "retry after exception"},
        )
        self.assertEqual(recovered.status_code, 200, recovered.text)

    def test_thread_lock_releases_after_request_cancellation(self):
        tid = self.new_thread()

        async def exercise():
            entered = asyncio.Event()
            cancelled = asyncio.Event()

            class CancelGraph:
                async def ainvoke(inner_self, state, config):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

            self.app.state.session_graph = CancelGraph()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                task = asyncio.create_task(client.post(
                    f"/threads/{tid}/ask", headers=self.headers("a"),
                    json={"question": "cancel this request"},
                ))
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.assertTrue(cancelled.is_set())
                self.assertFalse(self.app.state.thread_locks[tid].locked())
                self.app.state.session_graph = FakeGraph()
                recovered = await client.post(
                    f"/threads/{tid}/ask", headers=self.headers("a"),
                    json={"question": "after cancellation"},
                )
                self.assertEqual(recovered.status_code, 200, recovered.text)

        asyncio.run(exercise())

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

    def test_second_concurrent_thread_write_returns_409_immediately(self):
        tid = self.new_thread()
        self.fake_graph.block_next = True
        headers = self.headers("a")
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                self.client.post,
                f"/threads/{tid}/ask",
                headers=headers,
                json={"question": "first"},
            )
            self.assertTrue(self.fake_graph.entered.wait(3), "first request did not reach graph")
            second = self.client.post(
                f"/threads/{tid}/ask", headers=headers, json={"question": "second"},
            )
            self.assertEqual(second.status_code, 409, second.text)
            self.assertEqual(len(self.fake_graph.invoke_calls), 1)
            self.fake_graph.release.set()
            self.assertEqual(first.result(timeout=3).status_code, 200)
        self.assertFalse(self.app.state.thread_locks[tid].locked())


if __name__ == "__main__":
    unittest.main(verbosity=2)
