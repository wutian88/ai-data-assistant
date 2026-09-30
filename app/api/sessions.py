"""Day19：受保护的 LangGraph 会话 HTTP 接口。

设计边界：
- APP_API_KEY 负责应用级访问；X-Session-Key 负责演示用的独立用户身份。
- user_id 只能来自服务端凭证映射，不能让客户端填写。
- 每次读取 / 执行 Graph 之前，都检查 thread_id 归属。
- Day18 Graph 原文件不改：运行时使用相同 graph 定义，另行编译带 SQLite checkpointer 的实例。
- 这是本地双用户教学版鉴权；正式项目应接真实登录系统/JWT 校验。
"""

import asyncio
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from app.core.thread_access import ThreadAccessStore, ThreadNotFound
from app.core.paths import DATA_DIR, THREAD_OWNERS_DB, CHECKPOINT_DB


# 测试身份：实际密钥必须放到项目根目录的 .env，不能提交到 Git。
# 所有 /threads 请求还要求原来的 X-API-Key，通过调用方注入的 verify_api_key 校验。
SESSION_HEADER = APIKeyHeader(name="X-Session-Key", auto_error=False, scheme_name="SessionAPIKey",)


def resolve_user_id(session_key: str | None) -> str:
    """凭证 -> 固定身份。缺失配置、凭证重复时直接拒绝服务。"""
    key_a = os.getenv("DAY19_USER_A_KEY")
    key_b = os.getenv("DAY19_USER_B_KEY")

    if not key_a or not key_b or secrets.compare_digest(key_a, key_b):
        # 配置错误是服务端错误，绝不能退化成匿名用户或信任请求体 user_id。
        raise HTTPException(status_code=503, detail="Session authentication not configured")

    if session_key is None:
        raise HTTPException(status_code=401, detail="Invalid session credentials")

    # 两组密钥均执行定时安全的字符串比较；只返回服务端固定身份。
    matches_a = secrets.compare_digest(session_key, key_a)
    matches_b = secrets.compare_digest(session_key, key_b)
    if matches_a:
        return "user_a"
    if matches_b:
        return "user_b"
    raise HTTPException(status_code=401, detail="Invalid session credentials")


def storage_dir() -> Path:
    """Local data directory or the persistent APP_DATA_DIR mounted by Docker."""
    return DATA_DIR


@asynccontextmanager
async def session_lifespan(app: FastAPI):
    """FastAPI 启动时打开 SQLite Checkpointer；退出时彻底释放文件句柄。"""
    # 放在启动阶段再 import：普通单元测试不必初始化 Milvus、MCP 或模型。
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app.workflow.graph import graph, RECURSION_LIMIT

    folder = storage_dir()
    folder.mkdir(parents=True, exist_ok=True)
    app.state.thread_store = ThreadAccessStore(THREAD_OWNERS_DB)
    app.state.thread_locks = {}
    app.state.thread_locks_guard = asyncio.Lock()
    app.state.session_recursion_limit = RECURSION_LIMIT

    async with AsyncSqliteSaver.from_conn_string(
        str(CHECKPOINT_DB)
    ) as saver:
        await saver.setup()
        app.state.session_graph = graph.compile(checkpointer=saver)
        try:
            yield
        finally:
            # saver 退出 async with 时关闭连接，Windows 不会残留 sqlite 文件锁。
            app.state.session_graph = None


class SessionAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question 不能为空")
        return value


class ThreadCreateResponse(BaseModel):
    thread_id: str


class ThreadStateResponse(BaseModel):
    thread_id: str
    started: bool
    question: str | None
    answer: str | None
    route: str | None
    sources: list[str]


class SessionAskResponse(BaseModel):
    thread_id: str
    request_id: str
    route: str | None
    answer: str
    sources: list[str]
    error_code: str | None
    total_cost: float
    observed_request_tokens: dict[str, Any]


def _require_owner(app: FastAPI, user_id: str, thread_id: str) -> None:
    """统一把别人的会话与不存在的会话映射成 404，防止会话枚举。"""
    try:
        app.state.thread_store.require_owner(user_id, thread_id)
    except ThreadNotFound as exc:
        raise HTTPException(status_code=404, detail="Thread not found") from exc


def _build_initial_state(question: str, request_id: str) -> dict[str, Any]:
    """与旧 /ask 的 State 字段一致，避免破坏 Router/MCP 及 Token 统计。"""
    return {
        "request_id": request_id,
        "question": question,
        "route": None,
        "answer": "",
        "sources": [],
        "error": None,
        "error_code": None,
        "router_input_tokens": None,
        "router_output_tokens": None,
        "router_total_tokens": None,
        "router_reasoning_tokens": None,
        "service_input_tokens": None,
        "service_output_tokens": None,
        "service_total_tokens": None,
        "service_reasoning_tokens": None,
    }


def create_session_router(
    verify_api_key: Callable,
    limit_rate: Callable,
    limit_concurrency: Callable,
) -> APIRouter:
    """用工厂注入 Day19 原有鉴权/限流依赖，避免模块间循环导入。"""
    router = APIRouter(tags=["Day19 protected sessions"])

    async def authenticated_user(
        _app_key: str = Depends(verify_api_key),
        session_key: str | None = Security(SESSION_HEADER),
    ) -> str:
        return resolve_user_id(session_key)

    @router.post(
        "/threads",
        status_code=201,
        response_model=ThreadCreateResponse,
        dependencies=[Depends(limit_rate)],
    )
    async def create_thread(
        request: Request,
        user_id: str = Depends(authenticated_user),
    ):
        # ID 在服务端生成；客户端无权传入 thread_id 或 user_id。
        new_id = request.app.state.thread_store.create_thread(user_id)
        return ThreadCreateResponse(thread_id=new_id)

    @router.get(
        "/threads/{thread_id}",
        response_model=ThreadStateResponse,
        dependencies=[Depends(limit_rate)],
    )
    async def read_thread(
        thread_id: str,
        request: Request,
        user_id: str = Depends(authenticated_user),
    ):
        # 授权必须位于 aget_state 之前；不得先读取数据再过滤。
        _require_owner(request.app, user_id, thread_id)
        snapshot = await request.app.state.session_graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        values = snapshot.values if snapshot is not None else {}
        values = values or {}
        return ThreadStateResponse(
            thread_id=thread_id,
            started=bool(values.get("question")),
            question=values.get("question"),
            answer=values.get("answer"),
            route=values.get("route"),
            sources=values.get("sources") or [],
        )

    @router.post(
        "/threads/{thread_id}/ask",
        response_model=SessionAskResponse,
        dependencies=[Depends(limit_rate), Depends(limit_concurrency)],
    )
    async def ask_in_thread(
        thread_id: str,
        body: SessionAskRequest,
        request: Request,
        user_id: str = Depends(authenticated_user),
    ):
        # 先校验归属、再读/写 checkpoint。不能让客户端直接选择别人的会话。
        _require_owner(request.app, user_id, thread_id)
        async with request.app.state.thread_locks_guard:
            thread_lock = request.app.state.thread_locks.setdefault(
                thread_id, asyncio.Lock()
            )

        request_id = request.state.request_id
        start = time.perf_counter()
        # 同一 thread_id 的写操作串行化，避免并发覆盖 Checkpoint。
        async with thread_lock:
            result = await request.app.state.session_graph.ainvoke(
                _build_initial_state(body.question, request_id),
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": request.app.state.session_recursion_limit,
                },
            )

        elapsed = time.perf_counter() - start
        from app.core.observability import build_observed_request_usage
        # 当前 Graph 的 State 只存最新问题/答案；尚不支持多轮聊天历史拼接。
        return SessionAskResponse(
            thread_id=thread_id,
            request_id=request_id,
            route=result.get("route"),
            answer=result.get("answer", ""),
            sources=result.get("sources") or [],
            error_code=result.get("error_code"),
            total_cost=round(elapsed, 3),
            observed_request_tokens=build_observed_request_usage(result),
        )

    return router
