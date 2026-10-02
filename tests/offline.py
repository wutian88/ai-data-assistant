"""Test-only isolation shared by pytest and the eight-module regression runner."""

import builtins
import io
import os
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_DIR = PROJECT_ROOT / "tests" / "bootstrap"
OFFLINE_FLAG = "AI_DATA_ASSISTANT_OFFLINE_TESTS"
PRIVATE_ENV = PROJECT_ROOT / ".env"
FAKE_CREDENTIALS = {
    "DASHSCOPE_API_KEY": "offline-dashscope-key",
    "OPENAI_API_KEY": "offline-openai-key",
    "DEEPSEEK_API_KEY": "offline-deepseek-key",
    "ANTHROPIC_API_KEY": "offline-anthropic-key",
    "AZURE_OPENAI_API_KEY": "offline-azure-key",
    "GOOGLE_API_KEY": "offline-google-key",
    "GEMINI_API_KEY": "offline-gemini-key",
    "HF_TOKEN": "offline-huggingface-key",
    "HUGGINGFACEHUB_API_TOKEN": "offline-huggingface-key",
    "APP_API_KEY": "offline-app-key",
    "DAY19_USER_A_KEY": "offline-user-a-key",
    "DAY19_USER_B_KEY": "offline-user-b-key",
    "LANGSMITH_API_KEY": "offline-langsmith-key",
    "LANGCHAIN_API_KEY": "offline-langchain-key",
}
_active = False


class OfflineNetworkError(RuntimeError):
    """A test attempted a real socket connection or DNS lookup."""


def child_environment(data_dir: Path | str, inherited=None) -> dict[str, str]:
    """Build a subprocess environment without loading the private root .env."""
    env = dict(os.environ if inherited is None else inherited)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                 "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT"):
        env.pop(name, None)
    env.update(FAKE_CREDENTIALS)
    env.update({
        OFFLINE_FLAG: "1",
        "APP_DATA_DIR": str(Path(data_dir).resolve()),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "OTEL_SDK_DISABLED": "true",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    })
    # Only test children receive sitecustomize; regular application startup does not.
    parts = [str(BOOTSTRAP_DIR), str(PROJECT_ROOT)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


@contextmanager
def regression_environment():
    """Seed one temporary SQL database for all regression subprocesses."""
    with tempfile.TemporaryDirectory(prefix="ai-assistant-offline-") as runtime:
        env = child_environment(runtime)
        result = subprocess.run(
            [sys.executable, "-m", "scripts.init_demo_db"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if result.returncode:
            raise RuntimeError("Offline demo database initialization failed:\n"
                               + result.stdout[-2000:] + result.stderr[-2000:])
        yield env


def activate() -> None:
    """Install process-local guards before importing application modules."""
    global _active
    if _active:
        return
    if os.environ.get(OFFLINE_FLAG) != "1":
        raise RuntimeError("Offline test bootstrap requires its explicit test flag")
    runtime_value = os.environ.get("APP_DATA_DIR")
    if not runtime_value:
        raise RuntimeError("Offline tests require an isolated APP_DATA_DIR")
    runtime = Path(runtime_value).resolve()
    if runtime == PROJECT_ROOT or PROJECT_ROOT in runtime.parents:
        raise RuntimeError("Offline test data must be outside the project checkout")
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ.update(FAKE_CREDENTIALS)
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ[name] = "false"
    os.environ["OTEL_SDK_DISABLED"] = "true"

    _guard_network()
    _guard_files(runtime)
    _guard_dotenv()
    _active = True


def _guard_network() -> None:
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    socketpair_code = getattr(original_socketpair, "__code__", None)
    local = threading.local()

    def blocked(*args, **kwargs):
        raise OfflineNetworkError("Real network access is disabled in offline tests")

    def guarded_connect(sock, address):
        # Python's Windows fallback uses one synchronous loopback connect to create
        # asyncio's wake-up socketpair. No general loopback connection is allowed.
        if (sys.platform == "win32" and getattr(local, "socketpair", False)
                and socketpair_code is not None
                and sys._getframe(1).f_code is socketpair_code
                and isinstance(address, tuple)
                and address[0] in {"127.0.0.1", "::1"}):
            return original_connect(sock, address)
        return blocked()

    @wraps(original_socketpair)
    def guarded_socketpair(*args, **kwargs):
        previous = getattr(local, "socketpair", False)
        local.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            local.socketpair = previous

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = blocked
    socket.socket.sendto = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    socket.gethostbyname = blocked
    socket.gethostbyname_ex = blocked
    socket.gethostbyaddr = blocked
    socket.socketpair = guarded_socketpair


def _resolved_file(file):
    if isinstance(file, (str, bytes, os.PathLike)):
        return Path(os.fsdecode(file)).resolve()
    return None  # File descriptors and file-like objects keep their normal behavior.


def _guard_files(runtime: Path) -> None:
    root_audit = PROJECT_ROOT / "sql_audit.log"
    for module in (builtins, io):
        original_open = module.open

        @wraps(original_open)
        def guarded_open(file, *args, _original=original_open, **kwargs):
            path = _resolved_file(file)
            if path == PRIVATE_ENV:
                raise PermissionError("Private root .env is unavailable to offline tests")
            if path == root_audit:
                file = runtime / "sql_audit.log"
            return _original(file, *args, **kwargs)

        module.open = guarded_open


def _guard_dotenv() -> None:
    import dotenv
    import dotenv.main

    for name, empty in (("load_dotenv", False), ("dotenv_values", {})):
        original = getattr(dotenv.main, name)

        @wraps(original)
        def guarded(*args, _original=original, _empty=empty, **kwargs):
            candidate = kwargs.get("dotenv_path", args[0] if args else None)
            if candidate is None and kwargs.get("stream") is None:
                candidate = dotenv.find_dotenv(usecwd=True)
            if candidate and _resolved_file(candidate) == PRIVATE_ENV:
                return _empty.copy() if isinstance(_empty, dict) else _empty
            return _original(*args, **kwargs)

        setattr(dotenv.main, name, guarded)
        setattr(dotenv, name, guarded)
