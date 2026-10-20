"""Bounded provider retries with injectable time and per-attempt usage tracking."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class RetryUsage:
    attempts: int = 0
    retries: int = 0
    usage_complete: bool = True
    failed_usage: dict[str, int] = field(default_factory=lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
    })


_current_usage: contextvars.ContextVar[RetryUsage | None] = contextvars.ContextVar(
    "model_retry_usage", default=None,
)


@contextmanager
def track_model_usage():
    stats = RetryUsage()
    token = _current_usage.set(stats)
    try:
        yield stats
    finally:
        _current_usage.reset(token)


def _reported_usage(error: Exception) -> dict[str, int] | None:
    """Read usage only if a provider error explicitly returned it."""
    response = getattr(error, "response", None)
    if response is None:
        return None
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage") or payload.get("usage_metadata")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
    completion = usage.get("output_tokens", usage.get("completion_tokens"))
    total = usage.get("total_tokens")
    if not all(isinstance(value, int) and value >= 0 for value in (prompt, completion, total)):
        return None
    output_details = usage.get("output_token_details") or {}
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": total,
        "reasoning_tokens": int(output_details.get("reasoning") or 0),
    }


def _record_failure(stats: RetryUsage | None, error: Exception) -> None:
    if stats is None:
        return
    usage = _reported_usage(error)
    if usage is None:
        stats.usage_complete = False
        return
    for name, value in usage.items():
        stats.failed_usage[name] += value


def retry_sync(
    operation: Callable[[], Any],
    *,
    retryable: Callable[[Exception], bool],
    max_attempts: int,
    base_delay: float = 0.25,
    max_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    slot=None,
):
    """Retry transient model errors; release the model slot before backoff."""
    stats = _current_usage.get()
    for attempt in range(1, max_attempts + 1):
        if stats is not None:
            stats.attempts += 1
        try:
            with slot if slot is not None else nullcontext():
                return operation()
        except Exception as error:
            _record_failure(stats, error)
            if attempt >= max_attempts or not retryable(error):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if stats is not None:
                stats.retries += 1
            logger.warning(
                "model call transient failure; retry=%s/%s delay=%.3fs type=%s",
                attempt, max_attempts - 1, delay, type(error).__name__,
            )
            # No model concurrency slot is held while sleeping.
            sleep(delay)


async def retry_async(
    operation: Callable[[], Any],
    *,
    retryable: Callable[[Exception], bool],
    max_attempts: int,
    base_delay: float = 0.25,
    max_delay: float = 1.0,
    sleep: Callable[[float], Any] = asyncio.sleep,
    slot=None,
):
    """Async model retry loop with the same attempt and usage semantics."""
    stats = _current_usage.get()
    for attempt in range(1, max_attempts + 1):
        if stats is not None:
            stats.attempts += 1
        try:
            if slot is None:
                return await operation()
            async with slot:
                return await operation()
        except Exception as error:
            _record_failure(stats, error)
            if attempt >= max_attempts or not retryable(error):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if stats is not None:
                stats.retries += 1
            logger.warning(
                "model call transient failure; retry=%s/%s delay=%.3fs type=%s",
                attempt, max_attempts - 1, delay, type(error).__name__,
            )
            await sleep(delay)


def merge_retry_usage(usage: dict[str, Any] | None, stats: RetryUsage) -> dict[str, Any]:
    """Include provider-reported failed-attempt usage; mark unknown totals None."""
    result = dict(usage or {})
    if not stats.retries:
        return result
    if not stats.usage_complete:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "reasoning_tokens": None,
            "output_token_details": {"reasoning": None},
        }

    reasoning = result.get("reasoning_tokens")
    if reasoning is None:
        reasoning = (result.get("output_token_details") or {}).get("reasoning")
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        value = result.get(name)
        extra = stats.failed_usage[name]
        result[name] = value + extra if isinstance(value, int) else None
    if isinstance(reasoning, int):
        reasoning += stats.failed_usage["reasoning_tokens"]
    result["reasoning_tokens"] = reasoning
    result["output_token_details"] = {"reasoning": reasoning}
    return result
