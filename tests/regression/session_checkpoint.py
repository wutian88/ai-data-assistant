"""Regression：真实 AsyncSqliteSaver 持久化测试（零 LLM）。

运行：python -m tests.regression.session_checkpoint
只创建一个简单图专门验证 SQLite checkpoint，避免因调用业务模型产生费用。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph


class TestState(TypedDict):
    question: str
    answer: str


def build_test_graph():
    builder = StateGraph(TestState)

    async def echo(state: TestState):
        return {"answer": "已保存：" + state["question"]}

    builder.add_node("echo", echo)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder


async def verify_persistence(path):
    config = {"configurable": {"thread_id": "test-thread-persist"}}
    builder = build_test_graph()
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        graph = builder.compile(checkpointer=saver)
        answer = await graph.ainvoke(
            {"question": "第一次请求", "answer": ""}, config=config
        )
        assert answer["answer"] == "已保存：第一次请求"
        snap = await graph.aget_state(config)
        assert snap.values["question"] == "第一次请求"

    # 退出上面的 saver 后重新打开：模拟应用重启。
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()
        restored = builder.compile(checkpointer=saver)
        snap = await restored.aget_state(config)
        assert snap.values["answer"] == "已保存：第一次请求"


class CheckpointTests(unittest.TestCase):
    def test_sqlite_checkpoint_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(verify_persistence(Path(tmp) / "checkpoint.db"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
