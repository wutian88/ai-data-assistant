"""Real localhost HTTP/SSE using a deterministic graph and blocked outbound IO.

Run python -m tests.manual.sse_offline_demo --port 8765 --duration 60.
This is a test server with public fake credentials, never a deployment command.
"""
import argparse
import asyncio
import os
import tempfile

from tests.offline import activate, child_environment


async def serve(port: int, duration: float) -> None:
    import uvicorn
    from langgraph.graph import END, START, StateGraph
    import app.main as main
    from app.workflow.graph import AppState

    async def classify(state):
        await asyncio.sleep(0.15)
        return {"route": "rag"}

    async def answer(state):
        await asyncio.sleep(0.25)
        return {"answer": "离线演示：图执行完成后分块发送最终答案。\n" * 6, "sources": ["Mock 文档"]}

    graph = StateGraph(AppState)
    graph.add_node("classify", classify)
    graph.add_node("rag", answer)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "rag")
    graph.add_edge("rag", END)
    main.workflow = graph.compile()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="info"))

    async def stop():
        await asyncio.sleep(duration)
        server.should_exit = True

    stopper = asyncio.create_task(stop())
    try:
        print(f"OFFLINE SSE demo on 127.0.0.1:{port}; fake X-API-Key: offline-app-key", flush=True)
        await server.serve()
    finally:
        stopper.cancel()
        await asyncio.gather(stopper, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--duration", type=float, default=60)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="assistant-sse-demo-") as runtime:
        os.environ.update(child_environment(runtime))
        activate()  # Reject external model/Embedding calls and private .env reads.
        asyncio.run(serve(args.port, args.duration))


if __name__ == "__main__":
    main()
