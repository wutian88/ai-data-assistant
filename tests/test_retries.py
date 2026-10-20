"""Provider retry policy uses fake time and preserves observable attempt usage."""

import asyncio

import pytest

from app.core.retries import merge_retry_usage, retry_async, retry_sync, track_model_usage
from app.core import llm


class TransientFailure(Exception):
    pass


class FakeSlot:
    def __init__(self):
        self.held = False

    def __enter__(self):
        assert not self.held
        self.held = True
        return self

    def __exit__(self, *_):
        self.held = False


class FakeAsyncSlot:
    def __init__(self):
        self.held = False

    async def __aenter__(self):
        assert not self.held
        self.held = True

    async def __aexit__(self, *_):
        self.held = False


def retryable(error):
    return isinstance(error, TransientFailure)


def test_sync_retry_uses_fake_clock_and_releases_slot_during_backoff():
    now = [0.0]
    delays = []
    slot = FakeSlot()
    calls = [0]

    def fake_sleep(delay):
        assert not slot.held
        delays.append(delay)
        now[0] += delay

    def operation():
        calls[0] += 1
        if calls[0] == 1:
            raise TransientFailure("temporary")
        return "done"

    with track_model_usage() as stats:
        result = retry_sync(
            operation, retryable=retryable, max_attempts=2,
            sleep=fake_sleep, slot=slot,
        )

    assert result == "done"
    assert now == [0.25] and delays == [0.25]
    assert calls == [2]
    assert (stats.attempts, stats.retries) == (2, 1)
    assert not slot.held


def test_async_retry_uses_injected_sleep_and_releases_slot():
    async def run():
        delays = []
        slot = FakeAsyncSlot()
        calls = 0

        async def fake_sleep(delay):
            assert not slot.held
            delays.append(delay)

        async def operation():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TransientFailure("temporary")
            return "done"

        with track_model_usage() as stats:
            result = await retry_async(
                operation, retryable=retryable, max_attempts=2,
                sleep=fake_sleep, slot=slot,
            )

        assert result == "done" and calls == 2 and delays == [0.25]
        assert (stats.attempts, stats.retries) == (2, 1)
        assert not slot.held

    asyncio.run(run())


def test_reported_usage_from_failed_attempt_is_added_to_successful_attempt():
    class FakeResponse:
        def json(self):
            return {"usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
                "output_token_details": {"reasoning": 1},
            }}

    error = TransientFailure("temporary")
    error.response = FakeResponse()
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return "done"

    with track_model_usage() as stats:
        retry_sync(operation, retryable=retryable, max_attempts=2, sleep=lambda _: None)
        usage = merge_retry_usage({
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "output_token_details": {"reasoning": 2},
        }, stats)

    assert usage["input_tokens"] == 13
    assert usage["output_tokens"] == 6
    assert usage["total_tokens"] == 19
    assert usage["reasoning_tokens"] == 3


def test_unknown_failed_attempt_usage_marks_totals_unknown():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientFailure("usage unavailable")
        return "done"

    with track_model_usage() as stats:
        retry_sync(operation, retryable=retryable, max_attempts=2, sleep=lambda _: None)
        usage = merge_retry_usage({
            "input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
        }, stats)

    assert usage["input_tokens"] is None
    assert usage["output_tokens"] is None
    assert usage["total_tokens"] is None


def test_non_transient_error_is_not_retried():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise ValueError("deterministic failure")

    with pytest.raises(ValueError, match="deterministic"):
        retry_sync(operation, retryable=retryable, max_attempts=2, sleep=lambda _: None)
    assert calls == 1


def test_chatopenai_sdk_retries_are_disabled_and_model_call_retries_are_wrapped(monkeypatch):
    from langchain_openai import ChatOpenAI

    assert llm.model.max_retries == 0
    assert llm.model.request_timeout == 20
    calls = 0

    def fake_generate(self, messages, stop=None, run_manager=None, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientFailure("temporary")
        return "result"

    real_retry = llm.retry_sync
    monkeypatch.setattr(ChatOpenAI, "_generate", fake_generate)
    monkeypatch.setattr(llm, "_is_transient_provider_error", retryable)
    monkeypatch.setattr(
        llm, "retry_sync",
        lambda operation, **kwargs: real_retry(operation, sleep=lambda _: None, **kwargs),
    )

    result = llm.model._generate([])
    assert result == "result" and calls == 2
