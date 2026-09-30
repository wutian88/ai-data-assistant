"""Day19：thread_id 归属校验（独立权限层）。

核心原则：
1. thread_id 只是会话定位符，不是身份凭证。
2. 用户身份必须来自已经验证的鉴权结果，不能信任请求体里的 user_id。
3. 在调用 LangGraph get_state / invoke / resume 之前，先调用 require_owner()。
4. 新 thread_id 仅由服务器创建。该模块只登记归属关系，不创建 LangGraph checkpoint。

此模块不修改 Day16、Day18 的 Graph，也不进行真实 LLM 调用。
"""

import sqlite3
from contextlib import closing
import uuid
from pathlib import Path


class ThreadNotFound(Exception):
    """会话不存在，或当前用户无权访问；对外统一转换为 HTTP 404。"""


class InvalidUserIdentity(ValueError):
    """调用方没有提供有效的、已认证的用户身份。"""


class ThreadAccessStore:
    """将 thread_id -> user_id 的归属关系持久化到独立 SQLite 数据库。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        # 每次操作使用独立连接；调用方须显式关闭。
        return sqlite3.connect(self.db_path, timeout=5)

    def _initialize(self) -> None:
        # 权限登记表独立于 LangGraph checkpoint；两者不要混用。
        # sqlite3 的 with connection 只负责提交/回滚，**不会关闭连接**。
        # closing(...) 保证块退出时真正 close，避免 Windows 临时文件被占用。
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_owners (
                    thread_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_thread_owner "
                "ON thread_owners(owner_id)"
            )

    @staticmethod
    def _check_user_id(user_id: str) -> str:
        # user_id 必须由身份验证层提供；不能来自用户任意填写的 JSON 字段。
        if not isinstance(user_id, str) or not user_id or user_id != user_id.strip():
            raise InvalidUserIdentity("缺少有效的已认证用户身份")
        if len(user_id) > 128:
            raise InvalidUserIdentity("用户身份标识过长")
        return user_id

    def create_thread(self, authenticated_user_id: str) -> str:
        """服务器生成不可预测的 ID，并登记当前认证用户为所有者。"""
        user_id = self._check_user_id(authenticated_user_id)
        thread_id = str(uuid.uuid4())
        # sqlite3 的 with connection 只负责提交/回滚，**不会关闭连接**。
        # closing(...) 保证块退出时真正 close，避免 Windows 临时文件被占用。
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO thread_owners(thread_id, owner_id) VALUES (?, ?)",
                (thread_id, user_id),
            )
        return thread_id

    def require_owner(self, authenticated_user_id: str, thread_id: str) -> None:
        """访问 checkpoint 之前调用；其他用户与不存在的 ID 都抛相同异常。"""
        user_id = self._check_user_id(authenticated_user_id)
        if not isinstance(thread_id, str) or not thread_id:
            raise ThreadNotFound("会话不存在")

        # sqlite3 的 with connection 只负责提交/回滚，**不会关闭连接**。
        # closing(...) 保证块退出时真正 close，避免 Windows 临时文件被占用。
        with closing(self._connect()) as connection, connection:
            result = connection.execute(
                "SELECT 1 FROM thread_owners WHERE thread_id = ? AND owner_id = ?",
                (thread_id, user_id),
            ).fetchone()

        # 统一错误避免泄露某个 thread_id 是否属于其他用户。
        if result is None:
            raise ThreadNotFound("会话不存在")
