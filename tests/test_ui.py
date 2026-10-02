"""Exercise the real Streamlit page with offline HTTP responses."""

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from streamlit.testing.v1 import AppTest


UI_FILE = Path(__file__).resolve().parents[1] / "ui" / "app.py"
TEST_KEY = "ui-offline-test-credential"


@pytest.fixture
def http_post(monkeypatch):
    # Never load a developer's .env or issue real model-backed HTTP requests.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("APP_API_KEY", TEST_KEY)
    monkeypatch.setenv("API_BASE_URL", "http://127.0.0.1:8000")
    post = Mock(side_effect=AssertionError("An offline UI test attempted unexpected HTTP"))
    monkeypatch.setattr(httpx, "post", post)
    return post


def ui():
    page = AppTest.from_file(UI_FILE, default_timeout=15).run()
    assert not page.exception
    return page


def send(page, question="数据库一共有多少位用户？"):
    page.text_area(key="question").input(question)
    page.button(key="send").click().run()
    assert not page.exception
    return page


def payload(route="sql", sources=None, **updates):
    result = {
        "answer": "数据库共有 1000 位用户。",
        "route": route,
        "sources": sources or [],
        "error": None,
        "error_code": None,
        "request_id": "ui-request-123",
        "total_cost": 1.234,
        "router_tokens": {"input": 10, "output": 2, "total": 12, "reasoning": None},
        "service_tokens": {"input": 20, "output": 5, "total": 25, "reasoning": None},
        "observed_request_tokens": {"input": 30, "output": 7, "total": 37, "reasoning": None},
    }
    result.update(updates)
    return result


def respond(post, body, status=200, headers=None):
    post.side_effect = None
    post.return_value = httpx.Response(status, json=body, headers=headers)


def metrics(page):
    return {item.label: item.value for item in page.metric}


def test_sql_response_and_http_contract(http_post):
    respond(http_post, payload())
    page = ui()
    page.text_input(key="api_base_url").input("http://127.0.0.1:9000/api/").run()
    send(page, "  数据库一共有多少位用户？  ")
    http_post.assert_called_once_with(
        "http://127.0.0.1:9000/api/ask",
        json={"question": "数据库一共有多少位用户？"},
        headers={"X-API-Key": TEST_KEY}, timeout=120, follow_redirects=False,
    )
    assert page.session_state["last_response"]["answer"] == "数据库共有 1000 位用户。"
    assert "数据库共有 1000 位用户。" in [item.value for item in page.markdown]
    assert metrics(page) == {"Route": "sql", "Error Code": "无", "后端耗时": "1.234 s", "Observed Tokens": "37"}
    assert page.code[0].value == "ui-request-123"
    assert json.loads(page.json[0].value)["service_tokens"]["total"] == 25
    assert any("HTTP 请求耗时" in item.value for item in page.caption)
    assert "headers" not in page.session_state["last_response"]


def test_rag_sources_and_unknown_tokens(http_post):
    body = payload("rag", ["售后规则"], answer="7 天内可以申请退款。",
                   observed_request_tokens={"total": None}, total_cost=None)
    respond(http_post, body)
    page = send(ui(), "公司的退款规则是什么？")
    assert metrics(page)["Route"] == "rag"
    assert metrics(page)["Observed Tokens"] == "未知"
    assert metrics(page)["后端耗时"] == "未知"
    assert "售后规则" in [item.value for item in page.markdown]


@pytest.mark.parametrize("question,base_url,key,message", [
    ("   ", "http://127.0.0.1:8000", TEST_KEY, "请输入问题"),
    ("测试", "file:///tmp/backend", TEST_KEY, "API Base URL"),
    ("测试", "http://name:password@example.com", TEST_KEY, "API Base URL"),
    ("测试", "http://example.com:99999", TEST_KEY, "API Base URL"),
    ("测试", "http://127.0.0.1:8000", "", "填写 API Key"),
])
def test_input_validation_prevents_http(http_post, question, base_url, key, message):
    page = ui()
    page.text_input(key="api_base_url").input(base_url)
    page.text_input(key="api_key").input(key).run()
    send(page, question)
    http_post.assert_not_called()
    assert message in page.error[0].value
    assert metrics(page)["UI Error Code"] in {"INVALID_INPUT", "INVALID_URL", "NO_CREDENTIALS"}


@pytest.mark.parametrize("status,detail,extra_headers,message", [
    (401, "Invalid API Key", {}, "鉴权失败"),
    (422, [{"msg": "question is required", "input": TEST_KEY}], {}, "question is required"),
    (429, "Rate limit exceeded", {"Retry-After": "60"}, "60 秒后手动重试"),
])
def test_http_errors_clear_previous_answer(http_post, status, detail, extra_headers, message):
    respond(http_post, payload())
    page = send(ui())
    respond(http_post, {"detail": detail}, status, {"X-Request-ID": "failed-request", **extra_headers})
    send(page, "第二个问题")
    assert message in page.error[0].value
    assert f"HTTP {status}" in page.error[0].value
    assert page.code[0].value == "failed-request"
    assert metrics(page) == {"UI Error Code": f"HTTP_{status}"}
    assert "数据库共有 1000 位用户。" not in [item.value for item in page.markdown]
    assert TEST_KEY not in page.error[0].value
    assert "last_response" not in page.session_state
    assert http_post.call_count == 2  # No automatic retry.


@pytest.mark.parametrize("exception,message", [
    (httpx.ReadTimeout(TEST_KEY), "请求超时"),
    (httpx.ConnectError(TEST_KEY), "无法连接 API"),
    (httpx.InvalidURL(TEST_KEY), "无法连接 API"),
])
def test_transport_errors_do_not_expose_credentials(http_post, exception, message):
    http_post.side_effect = exception
    page = send(ui())
    assert message in page.error[0].value
    assert TEST_KEY not in page.error[0].value
    assert page.session_state["last_elapsed"] >= 0
    assert metrics(page)["UI Error Code"] in {"TIMEOUT", "CONNECTION_ERROR"}
    http_post.assert_called_once()


@pytest.mark.parametrize("response,message", [
    (httpx.Response(200, text="<html>not JSON</html>"), "不是有效 JSON"),
    (httpx.Response(200, json=[]), "需要 JSON 对象"),
    (httpx.Response(200, json={"sources": []}), "answer 或 sources"),
])
def test_malformed_responses(http_post, response, message):
    http_post.side_effect = None
    http_post.return_value = response
    page = send(ui())
    assert message in page.error[0].value
    assert metrics(page) == {"UI Error Code": "INVALID_RESPONSE"}


def test_business_error_and_response_redaction(http_post):
    respond(http_post, payload(error=f"Backend failed with {TEST_KEY}", error_code="MCP_ERROR"))
    page = send(ui())
    assert "MCP_ERROR" in page.error[0].value
    assert "[已隐藏]" in page.error[0].value
    assert TEST_KEY not in json.dumps(page.session_state["last_response"])
    assert metrics(page)["Error Code"] == "MCP_ERROR"


def test_success_after_http_error_clears_error_state(http_post):
    respond(http_post, {"detail": "Invalid API Key"}, 401)
    page = send(ui())
    assert metrics(page)["UI Error Code"] == "HTTP_401"
    respond(http_post, payload())
    send(page)
    assert not page.error
    assert "last_error_code" not in page.session_state
    assert "UI Error Code" not in metrics(page)
    assert metrics(page)["Route"] == "sql"
