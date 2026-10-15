"""Shared environment and persistent storage paths.

Legacy database basenames are retained to preserve existing Docker volumes.
"""
import os
import logging
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
ENV_FILE = ENV_PATH
load_dotenv(ENV_PATH, override=False)

# Versioned input documents live with the source, not in the runtime volume.
KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"

APP_DATA_DIR = os.getenv("APP_DATA_DIR")
DATA_DIR = Path(APP_DATA_DIR).expanduser() if APP_DATA_DIR else PROJECT_ROOT / "data"
if not DATA_DIR.is_absolute():
    DATA_DIR = PROJECT_ROOT / DATA_DIR
DATA_DIR = DATA_DIR.resolve()


def _runtime_path(current_name: str, legacy_name: str) -> Path:
    """Prefer current names, retaining existing volumes without moving files."""
    current = DATA_DIR / current_name
    legacy = DATA_DIR / legacy_name
    return legacy if not current.exists() and legacy.exists() else current


def _thread_database_paths() -> tuple[Path, Path]:
    """Select ownership/checkpoint names together, never mix generations."""
    current = (DATA_DIR / "thread_owners.db", DATA_DIR / "session_checkpoints.db")
    legacy = (DATA_DIR / "day19_thread_owners.db", DATA_DIR / "day19_session_checkpoints.db")
    has_current = any(path.exists() for path in current)
    has_legacy = any(path.exists() for path in legacy)
    if has_current and has_legacy:
        if not all(path.exists() for path in current):
            raise RuntimeError("Mixed thread database generations: the current pair is incomplete")
        logging.getLogger(__name__).warning("Both thread database generations exist; using the current pair")
    return legacy if has_legacy and not has_current else current


SHOP_DB = DATA_DIR / "shop.db"
MILVUS_DB = _runtime_path("milvus.db", "milvus_day14.db")
THREAD_OWNERS_DB, CHECKPOINT_DB = _thread_database_paths()
# A marker must describe the selected vector store, not a different generation.
MILVUS_INIT_MARKER = DATA_DIR / (
    ".milvus_initialized" if MILVUS_DB.name == "milvus.db" else ".milvus_day14_initialized"
)
