"""Keep every pytest module offline before application imports are collected."""

import os
import tempfile
from pathlib import Path

import pytest

from tests.offline import activate, child_environment


_runtime = tempfile.TemporaryDirectory(prefix="ai-assistant-pytest-")
os.environ.update(child_environment(_runtime.name))
activate()

from scripts.init_demo_db import init_database  # noqa: E402

init_database(Path(_runtime.name) / "shop.db")


@pytest.fixture(scope="session")
def offline_env():
    """The runner and pytest subprocesses share the same guarded environment."""
    return child_environment(_runtime.name)


@pytest.fixture(scope="session")
def offline_data_dir():
    return Path(_runtime.name)


def pytest_unconfigure(config):
    _runtime.cleanup()
