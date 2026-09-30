"""Regression：thread_id 归属层的零 LLM 回归测试。

运行：python -m tests.regression.thread_access
注意：这是独立权限层单元测试，不等于 HTTP 接口和 Graph 已完成集成。
"""

import tempfile
import unittest
import uuid
from pathlib import Path

from app.core.thread_access import (
    InvalidUserIdentity,
    ThreadAccessStore,
    ThreadNotFound,
)


class ThreadAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "owners.db"
        self.store = ThreadAccessStore(self.db_path)

    def test_owner_can_access_own_thread(self) -> None:
        thread_id = self.store.create_thread("user_A")
        self.assertEqual(str(uuid.UUID(thread_id)), thread_id)
        self.assertIsNone(self.store.require_owner("user_A", thread_id))

    def test_different_user_is_denied(self) -> None:
        thread_id = self.store.create_thread("user_A")
        with self.assertRaises(ThreadNotFound):
            self.store.require_owner("user_B", thread_id)

    def test_unknown_and_foreign_ids_use_same_error(self) -> None:
        thread_id = self.store.create_thread("user_A")
        with self.assertRaises(ThreadNotFound) as foreign:
            self.store.require_owner("user_B", thread_id)
        with self.assertRaises(ThreadNotFound) as unknown:
            self.store.require_owner("user_B", str(uuid.uuid4()))
        self.assertEqual(str(foreign.exception), str(unknown.exception))

    def test_ownership_survives_restart(self) -> None:
        thread_id = self.store.create_thread("user_A")
        reopened_store = ThreadAccessStore(self.db_path)
        self.assertIsNone(reopened_store.require_owner("user_A", thread_id))
        with self.assertRaises(ThreadNotFound):
            reopened_store.require_owner("user_B", thread_id)

    def test_invalid_identity_is_rejected(self) -> None:
        with self.assertRaises(InvalidUserIdentity):
            self.store.create_thread("  ")
        with self.assertRaises(InvalidUserIdentity):
            self.store.require_owner("", "any-thread")


if __name__ == "__main__":
    unittest.main(verbosity=2)
