"""Stage events and chunked final answers; never stream SQL or tool arguments."""
import asyncio
import json
import logging
import time
from contextlib import aclosing
from typing import Callable

import anyio
from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.api.sessions import _build_initial_state
from app.core.observability import build_observed_request_usage

logger = logging.getLogger(__name__)
ANSWER_CHUNK_CHARS = 80


def sse_event(event: str, data: dict) -> str:
    # json.dumps escapes embedded newlines, so one payload cannot inject events.
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class DisconnectAwareResponse(StreamingResponse):
    """Cancel the graph even if the client disconnects while no body is ready.

    Watch receive on all ASGI versions, including 2.4 where StreamingResponse
    normally detects disconnect only on the next send. Explicitly close the
    iterator on send errors too; its finally block closes the graph iterator.
    """

    async def __call__(self, scope, receive, send):
        try:
            async with anyio.create_task_group() as tasks:
                async def transmit():
                    try:
                        await self.stream_response(send)
                    except OSError:
                        # A disconnected transport cannot receive an error event.
                        pass
                    finally:
                        tasks.cancel_scope.cancel()

                tasks.start_soon(transmit)
                await self.listen_for_disconnect(receive)
                tasks.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


async def answer_events(workflow, question: str, request_id: str, recursion_limit: int):
    started = time.perf_counter()
    state = _build_initial_state(question, request_id)
    outcome = "cancelled"
    first_delta = None
    logger.info("[%s] stream connected | usage=null", request_id)
    try:
        logger.info("[%s] stream first_status | elapsed=%.3fs", request_id, time.perf_counter() - started)
        yield sse_event("status", {"stage": "routing"})
        updates = workflow.astream(
            state.copy(), config={"recursion_limit": recursion_limit}, stream_mode="updates",
        )
        async with aclosing(updates):
            async for update in updates:
                for node, values in update.items():
                    if not isinstance(values, dict):
                        raise ValueError("Invalid graph update")
                    state.update(values)
                    if state.get("error") or state.get("error_code"):
                        outcome = "error"
                        # Return a stable code, never the raw exception or SQL.
                        code = state.get("error_code") or "INTERNAL_ERROR"
                        yield sse_event("error", {"error_code": code, "request_id": request_id})
                        return
                    if node == "classify":
                        stage = {"rag": "retrieving", "sql": "querying"}.get(state.get("route"))
                        if stage:
                            yield sse_event("status", {"stage": stage})

        answer = state.get("answer")
        if state.get("route") not in {"rag", "sql", "fallback"} or not isinstance(answer, str) or not answer:
            raise ValueError("Graph did not complete an answer")
        # Only a completed graph answer is emitted; SQL/JSON tools remain atomic.
        for offset in range(0, len(answer), ANSWER_CHUNK_CHARS):
            if first_delta is None:
                first_delta = time.perf_counter() - started
                logger.info("[%s] stream first_delta | elapsed=%.3fs", request_id, first_delta)
            yield sse_event("delta", {"text": answer[offset:offset + ANSWER_CHUNK_CHARS]})
            await asyncio.sleep(0)  # Cancellation point; no artificial typing delay.
        yield sse_event("done", {
            "status": "completed", "request_id": request_id, "route": state["route"],
            "sources": state.get("sources") or [],
            "usage": build_observed_request_usage(state),
        })
        outcome = "completed"
    except Exception as exc:
        outcome = "error"
        logger.warning("[%s] stream failed | exception_type=%s", request_id, type(exc).__name__)
        yield sse_event("error", {"error_code": "INTERNAL_ERROR", "request_id": request_id})
    finally:
        logger.info(
            "[%s] stream end | status=%s | elapsed=%.3fs | first_delta=%s | usage=%s",
            request_id, outcome, time.perf_counter() - started, first_delta,
            json.dumps(build_observed_request_usage(state)),
        )


def create_streaming_router(request_model, get_workflow: Callable, recursion_limit: int,
                            limit_rate: Callable, limit_concurrency: Callable) -> APIRouter:
    router = APIRouter(tags=["Streaming answers"])

    @router.post("/ask/stream")
    async def ask_stream(
        body: request_model,
        request: Request,
        _: None = Depends(limit_rate),
        # Keep the same guard until the response finishes or disconnects.
        __: None = Depends(limit_concurrency, scope="request"),
    ):
        return DisconnectAwareResponse(
            answer_events(get_workflow(), body.question, request.state.request_id, recursion_limit),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
