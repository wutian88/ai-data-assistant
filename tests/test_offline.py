"""Verify isolation while retaining actual SQL, RAG and asyncio behavior."""

import asyncio
import builtins
import io
import os
import socket
import subprocess
import sys
from pathlib import Path

import dotenv
import httpx
import pytest

from tests.offline import (
    FAKE_CREDENTIALS,
    OFFLINE_FLAG,
    PRIVATE_ENV,
    PROJECT_ROOT,
    OfflineNetworkError,
    child_environment,
)


@pytest.mark.parametrize("address", [
    ("127.0.0.1", 8000), ("192.0.2.1", 443),
])
@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_tcp_connections_are_blocked(address, method):
    with socket.socket() as connection:
        with pytest.raises(OfflineNetworkError):
            getattr(connection, method)(address)


def test_ipv6_loopback_is_blocked():
    if not socket.has_ipv6:
        pytest.skip("IPv6 socket support unavailable")
    with socket.socket(socket.AF_INET6) as connection:
        with pytest.raises(OfflineNetworkError):
            connection.connect(("::1", 8000))


def test_dns_and_udp_are_blocked():
    with pytest.raises(OfflineNetworkError):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(OfflineNetworkError):
        socket.create_connection(("127.0.0.1", 8000))
    with socket.socket(type=socket.SOCK_DGRAM) as connection:
        with pytest.raises(OfflineNetworkError):
            connection.sendto(b"offline", ("127.0.0.1", 53))


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "https://example.com"])
def test_unmocked_http_is_blocked(url):
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(OfflineNetworkError):
            client.get(url)


def test_socketpair_and_asyncio_still_work():
    left, right = socket.socketpair()
    try:
        left.sendall(b"test")
        assert right.recv(4) == b"test"
    finally:
        left.close()
        right.close()

    async def exercise_event_loop():
        return await asyncio.to_thread(lambda: "offline")

    assert asyncio.run(exercise_event_loop()) == "offline"
    # The socketpair exemption ends immediately; ordinary loopback remains denied.
    with socket.socket() as connection:
        with pytest.raises(OfflineNetworkError):
            connection.connect(("127.0.0.1", 8000))


def test_environment_replaces_private_credentials_and_proxy(tmp_path):
    inherited = {name: "pretend-private-credential" for name in FAKE_CREDENTIALS}
    inherited.update({"HTTP_PROXY": "http://127.0.0.1:1234",
                      "https_proxy": "http://127.0.0.1:1234",
                      "LANGSMITH_TRACING": "true"})
    env = child_environment(tmp_path, inherited)
    assert all(env[name] == fake for name, fake in FAKE_CREDENTIALS.items())
    assert "HTTP_PROXY" not in env and "https_proxy" not in env
    assert env["LANGSMITH_TRACING"] == "false"
    assert env["LANGCHAIN_TRACING_V2"] == "false"
    assert env[OFFLINE_FLAG] == "1"
    assert Path(env["APP_DATA_DIR"]) == tmp_path.resolve()


@pytest.mark.parametrize("opener", [builtins.open, io.open])
def test_private_root_env_cannot_be_read(opener):
    # No real credential contents are read, even when the file is absent in CI.
    with pytest.raises(PermissionError, match="Private root"):
        opener(PRIVATE_ENV, encoding="utf-8")
    with pytest.raises(PermissionError, match="Private root"):
        PRIVATE_ENV.read_text(encoding="utf-8")
    assert dotenv.load_dotenv(PRIVATE_ENV, override=True) is False
    assert dotenv.dotenv_values(PRIVATE_ENV) == {}
    assert os.environ["OPENAI_API_KEY"] == FAKE_CREDENTIALS["OPENAI_API_KEY"]
    assert os.environ["DEEPSEEK_API_KEY"] == FAKE_CREDENTIALS["DEEPSEEK_API_KEY"]


def test_temporary_dotenv_and_normal_files_still_work(tmp_path, monkeypatch):
    fixture = tmp_path / ".env"
    fixture.write_text("OFFLINE_CONFIG_FIXTURE=local-test\n", encoding="utf-8")
    monkeypatch.delenv("OFFLINE_CONFIG_FIXTURE", raising=False)
    assert dotenv.load_dotenv(fixture)
    assert os.environ["OFFLINE_CONFIG_FIXTURE"] == "local-test"
    assert dotenv.dotenv_values(fixture)["OFFLINE_CONFIG_FIXTURE"] == "local-test"
    ordinary = tmp_path / "sql_audit.log"
    ordinary.write_text("ordinary file", encoding="utf-8")
    assert ordinary.read_text(encoding="utf-8") == "ordinary file"


def _child(code, env):
    return subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def test_subprocess_guard_is_loaded_before_application_import(offline_env):
    result = _child(
        "import os, socket\n"
        "from tests.offline import OfflineNetworkError, FAKE_CREDENTIALS\n"
        "from app.core.paths import DATA_DIR, SHOP_DB\n"
        "assert str(DATA_DIR) == os.environ['APP_DATA_DIR']\n"
        "assert SHOP_DB.is_file()\n"
        "assert os.environ['DASHSCOPE_API_KEY'] == FAKE_CREDENTIALS['DASHSCOPE_API_KEY']\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 8000))\n"
        "except OfflineNetworkError:\n"
        "    print('guard active')\n"
        "else:\n"
        "    raise AssertionError('missing network guard')\n",
        offline_env,
    )
    assert result.returncode == 0, result.stderr
    assert "guard active" in result.stdout


def test_sitecustomize_requires_test_flag(offline_env):
    env = offline_env.copy()
    env.pop(OFFLINE_FLAG)
    result = _child("import sys; assert 'tests.offline' not in sys.modules", env)
    assert result.returncode == 0, result.stderr


def test_bootstrap_fails_closed_for_project_data_directory(offline_env):
    env = offline_env.copy()
    env["APP_DATA_DIR"] = str(PROJECT_ROOT / "data")
    result = _child("raise AssertionError('unsafe bootstrap continued')", env)
    assert result.returncode == 2
    assert "Offline test bootstrap failed" in result.stderr
    assert "unsafe bootstrap continued" not in result.stderr


def test_rag_regression_never_starts_milvus(offline_env, offline_data_dir):
    result = _child(
        "import sys\n"
        "from tests.regression.rag_security import main\n"
        "main()\n"
        "assert not any(name.split('.')[0] in {'pymilvus', 'milvus_lite', 'langchain_milvus'} "
        "for name in sys.modules)\n",
        offline_env,
    )
    assert result.returncode == 0, result.stderr
    assert not list(offline_data_dir.glob("*milvus*"))


def test_real_sql_success_and_blocked_audit_are_kept_in_temporary_data(offline_data_dir):
    from app.services.sql_agent import safe_sql_query

    audit = offline_data_dir / "sql_audit.log"
    before = audit.read_text(encoding="utf-8") if audit.exists() else ""
    success = safe_sql_query.invoke({"query": "SELECT COUNT(*) FROM users"})
    blocked = safe_sql_query.invoke({"query": "DELETE FROM users"})
    assert success == "[(1000,)]"
    assert blocked.startswith("SQL校验失败")
    entries = audit.read_text(encoding="utf-8")[len(before):]
    assert "status=success" in entries and "COUNT(*) FROM users" in entries
    assert "status=blocked" in entries and "DELETE FROM users" in entries
