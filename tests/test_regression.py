"""Run the eight original regression modules with their existing entry points."""
import subprocess
import sys

import pytest

from scripts.run_regression import PROJECT_ROOT, TEST_MODULES


@pytest.mark.parametrize("module", TEST_MODULES)
def test_regression_module(module: str, offline_env) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=PROJECT_ROOT,
        env=offline_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{module} exited with {result.returncode}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )
