"""Optional presentation layer: all assistant requests go through HTTP."""

import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import streamlit as st
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)
REQUEST_TIMEOUT_SECONDS = 120


def api_endpoint(base_url: str) -> str:
    """Validate the configured address before sending credentials."""
    value = base_url.strip()
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(char.isspace() for char in value)
        )
        # Accessing port also rejects malformed or out-of-range values.
        _ = parsed.port  # Keep validation without Streamlit magic rendering the port.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("API Base URL 必须是有效的 HTTP/HTTPS 地址，不能包含凭证、查询参数或片段。")
    return value.rstrip("/") + "/ask"


def redact(value, api_key: str):
    """Keep credentials out of saved responses and displayed API errors."""
    if isinstance(value, str):
        return value.replace(api_key, "[已隐藏]") if api_key else value
    if isinstance(value, list):
        return [redact(item, api_key) for item in value]
    if isinstance(value, dict):
        return {name: redact(item, api_key) for name, item in value.items()}
    return value


def error_detail(payload: dict) -> str:
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        # Validation bodies may contain the input; only expose their messages.
        return "；".join(
            item["msg"] for item in detail
            if isinstance(item, dict) and isinstance(item.get("msg"), str)
        )
    return ""


def set_error(message: str, code: str) -> None:
    st.session_state["last_error"] = message
    st.session_state["last_error_code"] = code


def submit_question(base_url: str, api_key: str, question: str) -> None:
    for name in ("last_response", "last_error", "last_error_code", "last_elapsed", "last_request_id"):
        st.session_state.pop(name, None)

    question = question.strip()
    api_key = api_key.strip()
    if not question:
        set_error("请输入问题。", "INVALID_INPUT")
        return
    if len(question) > 1000:
        set_error("问题不能超过 1000 个字符。", "INVALID_INPUT")
        return
    if not api_key:
        set_error("请在侧栏填写 API Key，或在根目录 .env 配置 APP_API_KEY。", "NO_CREDENTIALS")
        return
    try:
        endpoint = api_endpoint(base_url)
    except ValueError as exc:
        set_error(str(exc), "INVALID_URL")
        return

    start = time.perf_counter()
    try:
        response = httpx.post(
            endpoint,
            json={"question": question},
            headers={"X-API-Key": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.TimeoutException:
        set_error("请求超时。请确认后端状态后再手动发送；页面不会自动重试。", "TIMEOUT")
        return
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, UnicodeError):
        # Exception strings can include request URLs or credentials.
        set_error("无法连接 API。请检查地址、网络和后端服务。", "CONNECTION_ERROR")
        return
    finally:
        st.session_state["last_elapsed"] = time.perf_counter() - start

    st.session_state["last_request_id"] = redact(
        response.headers.get("X-Request-ID", "未知"), api_key
    )
    try:
        payload = response.json()
    except ValueError:
        set_error(f"API 返回 HTTP {response.status_code}，响应不是有效 JSON。", "INVALID_RESPONSE")
        return
    if not isinstance(payload, dict):
        set_error("API 响应格式不符合预期（需要 JSON 对象）。", "INVALID_RESPONSE")
        return
    payload = redact(payload, api_key)

    if not response.is_success:
        messages = {
            401: "鉴权失败，请检查 API Key。",
            422: "问题格式不符合 API 要求。",
            429: "请求过于频繁或服务繁忙。",
        }
        message = messages.get(response.status_code, "API 请求失败。")
        detail = error_detail(payload)
        if detail:
            message += f" {detail}"
        if response.status_code == 429:
            retry_after = redact(response.headers.get("Retry-After", ""), api_key)
            if retry_after:
                message += f" 建议在 {retry_after} 秒后手动重试。"
        set_error(f"HTTP {response.status_code}：{message}", f"HTTP_{response.status_code}")
        return

    if not isinstance(payload.get("answer"), str) or not isinstance(payload.get("sources"), list):
        set_error("API 响应缺少有效的 answer 或 sources。", "INVALID_RESPONSE")
        return
    st.session_state["last_response"] = payload
    st.session_state["last_request_id"] = payload.get("request_id") or st.session_state["last_request_id"]


def seconds(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return f"{value:.3f} s"
    return "未知"


st.set_page_config(page_title="AI Data Assistant", page_icon="💬", layout="wide")
st.markdown("""
<style>
[data-testid="stMainBlockContainer"] {padding-top: 1.5rem; padding-bottom: 1rem;}
[data-testid="stVerticalBlock"] {gap: 0.75rem;}
</style>
""", unsafe_allow_html=True)
st.title("AI Data Assistant")
st.caption("查询业务数据，或检索公司的政策知识库。")

with st.sidebar:
    st.header("API 配置")
    base_url = st.text_input(
        "API Base URL", value=os.getenv("API_BASE_URL", "http://127.0.0.1:8000"),
        key="api_base_url",
    )
    api_key = st.text_input(
        "API Key", value=os.getenv("APP_API_KEY", ""), type="password", key="api_key",
    )

st.caption("示例：北京用户有多少？ · 最近 30 天订单量是多少？ · 公司的退款规则是什么？")
with st.form("question_form"):
    question = st.text_area("问题", max_chars=1000, height=80, key="question")
    submitted = st.form_submit_button("发送", key="send")

if submitted:
    with st.spinner("正在查询…"):
        submit_question(base_url, api_key, question)

payload = st.session_state.get("last_response")
if st.session_state.get("last_error"):
    st.error(st.session_state["last_error"])
    st.metric("UI Error Code", st.session_state["last_error_code"])

if payload is not None:
    if payload.get("error") or payload.get("error_code"):
        st.error(f"服务返回业务错误：{payload.get('error_code') or '未知'}。{payload.get('error') or ''}")
    st.subheader("Answer")
    st.markdown(payload["answer"])
    st.subheader("Sources")
    st.caption(" · ".join(payload["sources"]) if payload["sources"] else "无来源")
    route_col, code_col, backend_col, token_col = st.columns(4)
    route_col.metric("Route", payload.get("route") or "未知")
    code_col.metric("Error Code", payload.get("error_code") or "无")
    backend_col.metric("后端耗时", seconds(payload.get("total_cost")))
    observed = payload.get("observed_request_tokens") or {}
    total_tokens = observed.get("total") if isinstance(observed, dict) else None
    token_col.metric("Observed Tokens", str(total_tokens) if total_tokens is not None else "未知")
    with st.expander("Token Usage", expanded=False):
        st.json({
            "router_tokens": payload.get("router_tokens"),
            "service_tokens": payload.get("service_tokens"),
            "observed_request_tokens": payload.get("observed_request_tokens"),
        })

if st.session_state.get("last_request_id") or "last_elapsed" in st.session_state:
    with st.expander("Request Details", expanded=False):
        if st.session_state.get("last_request_id"):
            st.caption("Request ID")
            st.code(st.session_state["last_request_id"], language=None)
        if "last_elapsed" in st.session_state:
            st.caption(f"HTTP 请求耗时：{seconds(st.session_state['last_elapsed'])}")
