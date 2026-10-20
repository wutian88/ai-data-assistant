"""Production routing/guards and real ASGI cancellation, with no vendor calls."""
import asyncio
import json
import secrets
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.api.streaming import DisconnectAwareResponse

KEY = "offline-stream-test-key"
ANSWER = '第一行\n第二行含 emoji 😀 与 event: done。' * 12


class FakeGraph:
    def __init__(self, route="rag", failure=None):
        self.route, self.failure = route, failure
        self.initial = None
        self.closed = False

    async def astream(self, state, config, stream_mode):
        self.initial = state
        assert stream_mode == "updates"
        try:
            if self.failure == "exception":
                raise RuntimeError("private vendor failure must never reach the stream")
            yield {"classify": {"route": self.route}}
            if self.failure == "state":
                yield {self.route: {"error": "private SQL details", "error_code": "MCP_TIMEOUT"}}
            else:
                # Extra internal data must not leak into the wire protocol.
                yield {self.route: {"answer": ANSWER, "sources": ["退款规则"], "internal_sql": "SECRET SQL"}}
        finally:
            self.closed = True

    async def ainvoke(self, state, config):
        self.initial = state
        return {**state, "route": self.route, "answer": ANSWER}


@pytest.fixture
def service(monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(main, "workflow", graph)
    monkeypatch.setattr(main, "APP_API_KEY", KEY)
    monkeypatch.setattr(main, "ask_semaphore", asyncio.BoundedSemaphore(1))
    monkeypatch.setattr(main, "CONCURRENCY_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(main, "rate_limit_buckets", {})
    monkeypatch.setattr(main, "rate_limit_lock", asyncio.Lock())
    monkeypatch.setattr(main, "RATE_LIMIT_REQUESTS", 100)
    return SimpleNamespace(graph=graph, client=TestClient(main.app))


def events(response):
    result = []
    for frame in response.text.split("\n\n"):
        if not frame:
            continue
        event, data = frame.split("\n")
        result.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return result


@pytest.mark.parametrize("route,stage", [("rag", "retrieving"), ("sql", "querying"), ("fallback", None)])
def test_stream_order_answer_and_no_tool_leak(service, route, stage, caplog):
    service.graph.route = route
    with caplog.at_level("INFO"):
        response = service.client.post("/ask/stream", headers={"X-API-Key": KEY}, json={"question": "  测试问题  "})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    wire = events(response)
    stages = [data["stage"] for event, data in wire if event == "status"]
    assert stages == ["routing"] + ([stage] if stage else [])
    assert wire[-1][0] == "done"
    assert wire[-1][1]["route"] == route
    assert wire[-1][1]["request_id"] == response.headers["X-Request-ID"]
    assert "".join(data["text"] for event, data in wire if event == "delta") == ANSWER
    assert len([1 for event, _ in wire if event == "delta"]) > 1
    assert "SECRET SQL" not in response.text
    assert wire[-1][1]["usage"]["total_tokens"] is None
    assert service.graph.initial["question"] == "测试问题"
    assert service.graph.initial["user_id"] is None
    assert service.graph.closed
    assert "first_status" in caplog.text and "first_delta" in caplog.text and "stream end" in caplog.text
    assert KEY not in caplog.text


@pytest.mark.parametrize("failure,code", [("exception", "INTERNAL_ERROR"), ("state", "MCP_TIMEOUT")])
def test_graph_error_is_terminal_and_redacted(service, failure, code):
    service.graph.failure = failure
    response = service.client.post("/ask/stream", headers={"X-API-Key": KEY}, json={"question": "test"})
    assert response.status_code == 200  # Headers were sent before the node ran.
    wire = events(response)
    assert wire[-1][0] == "error" and wire[-1][1]["error_code"] == code
    assert not any(event in {"delta", "done"} for event, _ in wire)
    assert "private" not in response.text
    assert service.graph.closed
    assert not main.ask_semaphore.locked()


@pytest.mark.parametrize("key", [None, "wrong"])
def test_missing_or_wrong_key_rejected_before_stream(service, key):
    response = service.client.post("/ask/stream", headers={"X-API-Key": key} if key else {}, json={"question": "test"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert service.graph.initial is None


def test_rate_limit_is_http_429_before_stream(service, monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT_REQUESTS", 1)
    headers = {"X-API-Key": KEY}
    assert service.client.post("/ask/stream", headers=headers, json={"question": "test"}).status_code == 200
    response = service.client.post("/ask/stream", headers=headers, json={"question": "test"})
    assert response.status_code == 429 and "Retry-After" in response.headers
    assert response.headers["content-type"].startswith("application/json")


def test_validation_and_existing_ask_contract(service):
    headers = {"X-API-Key": KEY}
    assert service.client.post("/ask/stream", headers=headers, json={"question": "  "}).status_code == 422
    assert service.client.post("/ask/stream", headers=headers, json={"question": "x" * 1001}).status_code == 422
    response = service.client.post("/ask", headers=headers, json={"question": "test"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["answer"] == ANSWER
    assert service.graph.initial["user_id"] is None


def test_anonymous_rag_answers_but_sql_fails_closed_at_sql_node(service, monkeypatch):
    from langgraph.graph import END, START, StateGraph
    from app.workflow.graph import AppState, sql_node

    rag_response = service.client.post(
        "/ask",
        headers={"X-API-Key": KEY},
        json={"question": "公司的退款规则是什么？"},
    )
    assert rag_response.status_code == 200
    assert rag_response.json()["route"] == "rag"
    assert service.graph.initial["user_id"] is None

    invalid_key_response = service.client.post(
        "/ask",
        headers={"X-API-Key": KEY, "X-Session-Key": "invalid"},
        json={"question": "公司的退款规则是什么？"},
    )
    assert invalid_key_response.status_code == 401
    assert service.graph.initial["user_id"] is None

    graph = StateGraph(AppState)
    graph.add_node("classify", lambda state: {"route": "sql"})
    graph.add_node("sql", sql_node)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "sql")
    graph.add_edge("sql", END)
    monkeypatch.setattr(main, "workflow", graph.compile())

    response = service.client.post(
        "/ask",
        headers={"X-API-Key": KEY},
        json={"question": "订单统计"},
    )
    assert response.status_code == 200
    assert response.json()["error_code"] == "SQL_IDENTITY_REQUIRED"

    stream = events(service.client.post(
        "/ask/stream", headers={"X-API-Key": KEY}, json={"question": "订单统计"},
    ))
    assert stream[-1][0] == "error"
    assert stream[-1][1]["error_code"] == "SQL_IDENTITY_REQUIRED"

    invalid_stream = service.client.post(
        "/ask/stream",
        headers={"X-API-Key": KEY, "X-Session-Key": "invalid"},
        json={"question": "订单统计"},
    )
    assert invalid_stream.status_code == 401


def test_valid_session_identity_is_attached_to_trusted_state(service, monkeypatch):
    session_key_a = secrets.token_urlsafe(24)
    session_key_b = secrets.token_urlsafe(24)
    monkeypatch.setenv("SESSION_USER_A_KEY", session_key_a)
    monkeypatch.setenv("SESSION_USER_B_KEY", session_key_b)
    response = service.client.post(
        "/ask",
        headers={"X-API-Key": KEY, "X-Session-Key": session_key_a},
        json={"question": "公司的退款规则是什么？"},
    )
    assert response.status_code == 200
    assert service.graph.initial["user_id"] == "user_a"


def test_end_to_end_budget_applies_to_json_and_sse(service, monkeypatch):
    class SlowGraph(FakeGraph):
        async def ainvoke(self, state, config):
            await asyncio.Event().wait()

        async def astream(self, state, config, stream_mode):
            self.initial = state
            yield {"classify": {"route": "rag"}}
            await asyncio.Event().wait()

    monkeypatch.setattr(main, "END_TO_END_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.api.streaming.END_TO_END_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(main, "workflow", SlowGraph())

    response = service.client.post("/ask", headers={"X-API-Key": KEY}, json={"question": "test"})
    assert response.status_code == 504
    assert response.json()["detail"] == "END_TO_END_TIMEOUT"

    stream = events(service.client.post(
        "/ask/stream", headers={"X-API-Key": KEY}, json={"question": "test"},
    ))
    assert stream[-1][0] == "error"
    assert stream[-1][1]["error_code"] == "END_TO_END_TIMEOUT"
    assert not main.ask_semaphore.locked()


def test_real_langgraph_updates_use_the_same_wire_contract(service, monkeypatch):
    from langgraph.graph import END, START, StateGraph
    from app.workflow.graph import AppState
    graph = StateGraph(AppState)
    graph.add_node("classify", lambda state: {"route": "sql"})
    graph.add_node("sql", lambda state: {"answer": "订单金额由数据库计算", "sources": []})
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "sql")
    graph.add_edge("sql", END)
    monkeypatch.setattr(main, "workflow", graph.compile())
    wire = events(service.client.post("/ask/stream", headers={"X-API-Key": KEY}, json={"question": "订单金额"}))
    assert wire[1] == ("status", {"stage": "querying"})
    assert wire[-2] == ("delta", {"text": "订单金额由数据库计算"})
    assert wire[-1][0] == "done"


@pytest.mark.parametrize("phase", ["graph", "delta"])
def test_disconnect_cancels_graph_and_releases_actual_guard(service, monkeypatch, phase):
    async def exercise():
        running, cancelled, delta_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
        never = asyncio.Event()

        class SlowGraph(FakeGraph):
            async def astream(self, state, config, stream_mode):
                try:
                    yield {"classify": {"route": "rag"}}
                    running.set()
                    if phase == "graph":
                        await never.wait()
                    yield {"rag": {"answer": ANSWER}}
                finally:
                    cancelled.set()

        monkeypatch.setattr(main, "workflow", SlowGraph())
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b'{"question":"test"}', "more_body": False})

        async def send(message):
            if message["type"] == "http.response.body" and b"event: delta" in message.get("body", b""):
                delta_sent.set()
                # A real transport unblocks send on completion/disconnect; do not
                # model it as an uninterruptible send outside the middleware.
                await asyncio.sleep(0.1)

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/ask/stream",
            "raw_path": b"/ask/stream", "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"x-api-key", KEY.encode())],
            "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }
        task = asyncio.create_task(main.app(scope, incoming.get, send))
        try:
            await asyncio.wait_for(running.wait() if phase == "graph" else delta_sent.wait(), 3)
            assert main.ask_semaphore.locked()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(main.app), base_url="http://test") as client:
                busy = await client.post("/ask/stream", headers={"X-API-Key": KEY}, json={"question": "second"})
                assert busy.status_code == 429
                await incoming.put({"type": "http.disconnect"})
                await asyncio.wait_for(task, 3)
                assert cancelled.is_set() and not main.ask_semaphore.locked()
                monkeypatch.setattr(main, "workflow", FakeGraph())
                retry = await client.post("/ask/stream", headers={"X-API-Key": KEY}, json={"question": "retry"})
                assert retry.status_code == 200 and events(retry)[-1][0] == "done"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())


def test_send_error_closes_iterator_on_asgi_24():
    async def exercise():
        closed = asyncio.Event()
        async def content():
            try:
                yield "event: status\ndata: {}\n\n"
                await asyncio.Event().wait()
            finally:
                closed.set()
        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("client gone")
        async def receive():
            await asyncio.Event().wait()
        await asyncio.wait_for(DisconnectAwareResponse(content())(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send,
        ), 3)
        assert closed.is_set()
    asyncio.run(exercise())
