import logging

import asyncio

import os

import sqlite3

import time

import uuid

from pathlib import Path

import secrets

from fastapi.security import APIKeyHeader

from dotenv import load_dotenv

from fastapi import FastAPI, Request ,Depends, HTTPException, Security, status

from fastapi.responses import JSONResponse

from pydantic import BaseModel ,Field, field_validator


from app.workflow.graph import (

    RECURSION_LIMIT,

    app as workflow,


)


from app.api.sessions import create_session_router, session_lifespan
from app.core.observability import build_observed_request_usage
from app.core.paths import PROJECT_ROOT, ENV_PATH, SHOP_DB

from app.core.config import (

    APP_API_KEY,

    CONCURRENCY_WAIT_SECONDS,

    MAX_CONCURRENT_REQUESTS,

    RATE_LIMIT_REQUESTS,

    RATE_LIMIT_WINDOW_SECONDS,

)


import hashlib

import math

from collections import deque


# ============================================================

# 1. 基础配置

# ============================================================


ask_semaphore = asyncio.BoundedSemaphore(

    MAX_CONCURRENT_REQUESTS

)


rate_limit_buckets: dict[str, deque[float]] = {}


rate_limit_lock = asyncio.Lock()


logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s | %(levelname)s | %(message)s",

)


logger = logging.getLogger(__name__)


DB_PATH = SHOP_DB


# ============================================================

# 2. FastAPI

# ============================================================


app = FastAPI(

    title="Day18 AI Application Service",

    version="1.0.0",
    lifespan=session_lifespan,

)


api_key_header = APIKeyHeader(

    name="X-API-Key",

    auto_error=False,

    scheme_name="AppAPIKey",
)


def verify_api_key(

    api_key: str | None = Security(api_key_header),

) -> str:


    if not APP_API_KEY:

        raise RuntimeError(

            "APP_API_KEY 未配置"

        )


    if (

        api_key is None

        or not secrets.compare_digest(

            api_key,

            APP_API_KEY,

        )

    ):

        raise HTTPException(

            status_code=status.HTTP_401_UNAUTHORIZED,

            detail="Invalid API Key",

        )


    return api_key


async def limit_concurrency():

    acquired = False


    try:

        await asyncio.wait_for(

            ask_semaphore.acquire(),

            timeout=CONCURRENCY_WAIT_SECONDS,

        )


        acquired = True


        yield


    except asyncio.TimeoutError:

        raise HTTPException(

            status_code=status.HTTP_429_TOO_MANY_REQUESTS,

            detail="Server is busy, please retry later",

        )


    finally:

        if acquired:

            ask_semaphore.release()


async def limit_rate(

    api_key: str = Depends(verify_api_key),

) -> None:

    now = time.monotonic()


    # 不直接把真实 API Key 存进内存字典

    client_id = hashlib.sha256(

        api_key.encode("utf-8")

    ).hexdigest()


    async with rate_limit_lock:

        bucket = rate_limit_buckets.setdefault(

            client_id,

            deque(),

        )


        cutoff = now - RATE_LIMIT_WINDOW_SECONDS


        # 删除已经超过时间窗口的旧请求

        while bucket and bucket[0] <= cutoff:

            bucket.popleft()


        # 当前时间窗口内已经达到请求上限

        if len(bucket) >= RATE_LIMIT_REQUESTS:

            retry_after = math.ceil(

                RATE_LIMIT_WINDOW_SECONDS

                - (now - bucket[0])

            )


            raise HTTPException(

                status_code=status.HTTP_429_TOO_MANY_REQUESTS,

                detail="Rate limit exceeded",

                headers={

                    "Retry-After": str(

                        max(retry_after, 1)

                    )

                },

            )


        # 记录本次请求时间

        bucket.append(now)


# ============================================================

# 3. 请求 / 响应模型

# ============================================================


class AskRequest(BaseModel):

    """

    /ask 接口的输入模型。


    当前限制：

    1. question 必须是字符串

    2. 去掉首尾空白后不能为空

    3. 最长 1000 个字符

    """


    question: str = Field(

        ...,

        min_length=1,

        max_length=1000,

    )


    @field_validator("question")

    @classmethod

    def validate_question(

        cls,

        value: str,

    ) -> str:


        # 去掉首尾空格、换行、Tab

        value = value.strip()


        # "     " 这种输入原本长度 > 0，

        # 但去掉空白以后实际上是空问题。

        if not value:

            raise ValueError(

                "question 不能为空"

            )


        return value


class AskResponse(BaseModel):

    request_id: str

    route: str | None


    answer: str

    sources: list[str]


    error: str | None

    error_code: str | None


    total_cost: float


    router_tokens: dict

    service_tokens: dict

    observed_request_tokens: dict


# ============================================================

# 4. Request ID Middleware

#

# 每一个 HTTP 请求进入 FastAPI：

#

# HTTP Request

#       ↓

# Middleware

#       ↓

# 生成 request_id

#       ↓

# request.state.request_id

#       ↓

# Endpoint

#       ↓

# LangGraph State

#

# 这样同一个 request_id 就可以贯穿整个请求。

# ============================================================


@app.middleware("http")

async def request_context_middleware(

    request: Request,

    call_next,

):

    request_id = str(uuid.uuid4())


    request.state.request_id = request_id


    start = time.perf_counter()


    logger.info(

        "[%s] http request start | method=%s | path=%s",

        request_id,

        request.method,

        request.url.path,

    )


    try:

        response = await call_next(request)


        cost = time.perf_counter() - start


        response.headers[

            "X-Request-ID"

        ] = request_id


        logger.info(

            "[%s] http request end | status_code=%s | cost=%.3fs",

            request_id,

            response.status_code,

            cost,

        )


        return response


    except Exception:

        cost = time.perf_counter() - start


        logger.exception(

            "[%s] http request end | "

            "status=error | cost=%.3fs",

            request_id,

            cost,

        )


        raise


# ============================================================

# 5. Liveness

#

# 只判断 Web 服务进程是否存活。

# 不访问数据库、不调用模型。

# ============================================================


@app.get("/health/live")

def health_live() -> dict:

    return {

        "status": "alive"

    }


# ============================================================

# 6. Readiness 辅助函数

# ============================================================


def check_database() -> tuple[bool, str]:

    if not DB_PATH.exists():

        return (

            False,

            "database file not found",

        )


    try:

        conn = sqlite3.connect(

            f"file:{DB_PATH}?mode=ro",

            uri=True,

            timeout=2,

        )


        try:

            cursor = conn.cursor()

            cursor.execute("SELECT 1")

            cursor.fetchone()


        finally:

            conn.close()


        return (

            True,

            "ok",

        )


    except Exception as e:

        return (

            False,

            str(e),

        )


def check_llm_config() -> tuple[bool, str]:

    api_key = os.getenv(

        "DASHSCOPE_API_KEY"

    )


    if not api_key:

        return (

            False,

            "DASHSCOPE_API_KEY missing",

        )


    # 注意：

    # 只检查配置是否存在。

    # 不调用 model.invoke()，避免 health check 消耗 Token。

    return (

        True,

        "configured",

    )


# ============================================================

# 7. Readiness

# ============================================================


@app.get("/health/ready")

def health_ready():

    db_ok, db_message = (

        check_database()

    )


    llm_ok, llm_message = (

        check_llm_config()

    )


    ready = (

        db_ok

        and llm_ok

    )


    body = {

        "status": (

            "ready"

            if ready

            else "not_ready"

        ),

        "checks": {

            "database": {

                "ok": db_ok,

                "message": db_message,

            },

            "llm_config": {

                "ok": llm_ok,

                "message": llm_message,

            },

        },

    }


    if ready:

        return body


    return JSONResponse(

        status_code=503,

        content=body,

    )


# ============================================================

# 8. AI 主接口

#

# HTTP

# ↓

# request_id

# ↓

# LangGraph

# ↓

# Router

# ↓

# RAG / SQL / fallback

# ↓

# MCP

# ↓

# Response

# ============================================================

@app.get("/rate-limit-test")

async def rate_limit_test(

    _: None = Depends(limit_rate),

):

    return {

        "status": "ok"

    }


@app.post(

    "/ask",

    response_model=AskResponse,

)

async def ask(

    body: AskRequest,

    request: Request,

    _: None = Depends(limit_rate),

    __: None = Depends(limit_concurrency),

):

    request_id = (

        request.state.request_id

    )


    start = time.perf_counter()


    # --------------------------------------------------------

    # 把 HTTP 层生成的 request_id

    # 直接传入 LangGraph State。

    #

    # 这里不要重新 uuid.uuid4()。

    # --------------------------------------------------------


    result = await workflow.ainvoke(

        {

            "request_id": request_id,

            "question": body.question,


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

        },

        config={

            "recursion_limit": (

                RECURSION_LIMIT

            )

        },

    )


    total_cost = (

        time.perf_counter()

        - start

    )


    observed_usage = (

        build_observed_request_usage(

            result

        )

    )


    status = (

        "error"

        if result.get("error")

        else "success"

    )


    logger.info(

        "[%s] ai request end | "

        "route=%s | "

        "status=%s | "

        "error_code=%s | "

        "total_cost=%.3fs | "

        "router_total_tokens=%s | "

        "service_total_tokens=%s | "

        "observed_request_total_tokens=%s",

        request_id,

        result.get("route"),

        status,

        result.get("error_code"),

        total_cost,

        result.get(

            "router_total_tokens"

        ),

        result.get(

            "service_total_tokens"

        ),

        observed_usage.get(

            "total_tokens"

        ),

    )


    return AskResponse(

        request_id=request_id,


        route=result.get(

            "route"

        ),


        answer=result.get(

            "answer",

            "",

        ),


        sources=result.get(

            "sources",

            [],

        ),


        error=result.get(

            "error"

        ),


        error_code=result.get(

            "error_code"

        ),


        total_cost=round(

            total_cost,

            3,

        ),


        router_tokens={

            "input": result.get(

                "router_input_tokens"

            ),

            "output": result.get(

                "router_output_tokens"

            ),

            "total": result.get(

                "router_total_tokens"

            ),

            "reasoning": result.get(

                "router_reasoning_tokens"

            ),

        },


        service_tokens={

            "input": result.get(

                "service_input_tokens"

            ),

            "output": result.get(

                "service_output_tokens"

            ),

            "total": result.get(

                "service_total_tokens"

            ),

            "reasoning": result.get(

                "service_reasoning_tokens"

            ),

        },


        observed_request_tokens={

            "input": observed_usage.get(

                "input_tokens"

            ),

            "output": observed_usage.get(

                "output_tokens"

            ),

            "total": observed_usage.get(

                "total_tokens"

            ),

            "reasoning": observed_usage.get(

                "reasoning_tokens"

            ),

        },

    )

# ============================================================
# Day19：新增受保护会话路由（保留原 /ask 不变）
# 此处放在所有鉴权、限流依赖定义之后，避免循环导入。
# ============================================================
app.include_router(
    create_session_router(verify_api_key, limit_rate, limit_concurrency)
)
