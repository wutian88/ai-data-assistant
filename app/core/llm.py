import os
import asyncio
import threading
from typing import Any

from app.core.config import (
    MAX_CONCURRENT_REQUESTS,
    MODEL_CALL_TIMEOUT_SECONDS,
    MODEL_RETRY_ATTEMPTS,
)
from app.core.paths import PROJECT_ROOT, ENV_PATH
from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings
from openai import APIConnectionError, APIStatusError

from app.core.retries import retry_async, retry_sync


# ============================================================
# 1. 环境变量
#
# 统一使用项目根目录：
#
# My_FastAPI/.env
#
# Docker 中如果环境变量已经由 compose 注入，
# os.getenv() 同样可以直接读取。
# ============================================================

# ============================================================
# 2. DashScope API Key
# ============================================================

DASHSCOPE_API_KEY = os.getenv(
    "DASHSCOPE_API_KEY"
)

if not DASHSCOPE_API_KEY:
    raise RuntimeError(
        "DASHSCOPE_API_KEY 未配置，请检查环境变量或项目根目录 .env"
    )


# ============================================================
# 3. Chat Model
# ============================================================

_sync_model_slot = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
_async_model_slot = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


def _is_transient_provider_error(error: Exception) -> bool:
    if isinstance(error, APIConnectionError):
        return True
    if isinstance(error, APIStatusError):
        return error.status_code in {408, 409, 429} or error.status_code >= 500
    return False


class RetryingChatOpenAI(ChatOpenAI):
    """ChatOpenAI with SDK retries disabled and one bounded application retry."""

    retry_attempts: int = 2
    retry_base_delay: float = 0.25
    retry_max_delay: float = 1.0

    def _generate(
        self,
        messages,
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ):
        return retry_sync(
            lambda: super(RetryingChatOpenAI, self)._generate(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            ),
            retryable=_is_transient_provider_error,
            max_attempts=self.retry_attempts,
            base_delay=self.retry_base_delay,
            max_delay=self.retry_max_delay,
            slot=_sync_model_slot,
        )

    async def _agenerate(
        self,
        messages,
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ):
        return await retry_async(
            lambda: super(RetryingChatOpenAI, self)._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs,
            ),
            retryable=_is_transient_provider_error,
            max_attempts=self.retry_attempts,
            base_delay=self.retry_base_delay,
            max_delay=self.retry_max_delay,
            slot=_async_model_slot,
        )


model = RetryingChatOpenAI(
    model="qwen3.7-flash-2026-07-15",
    api_key=DASHSCOPE_API_KEY,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0,
    timeout=MODEL_CALL_TIMEOUT_SECONDS,
    max_retries=0,
    retry_attempts=MODEL_RETRY_ATTEMPTS,
)


# ============================================================
# 4. Embedding Model
# ============================================================

embeddings = DashScopeEmbeddings(
    model="text-embedding-v4",
    dashscope_api_key=DASHSCOPE_API_KEY,
)
