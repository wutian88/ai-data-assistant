"""Loaded only through the offline regression subprocess PYTHONPATH."""

import os
import sys
from pathlib import Path


if os.environ.get("AI_DATA_ASSISTANT_OFFLINE_TESTS") == "1":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from tests.offline import activate

        activate()
    except BaseException:
        # Python normally ignores sitecustomize exceptions. A broken isolation
        # bootstrap must stop the child instead of continuing without its guards.
        sys.stderr.write("Offline test bootstrap failed; subprocess stopped.\n")
        sys.stderr.flush()
        os._exit(2)
