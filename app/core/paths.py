"""Shared environment and persistent storage paths.

Legacy database basenames are retained to preserve existing Docker volumes.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
ENV_FILE = ENV_PATH
load_dotenv(ENV_PATH, override=False)

APP_DATA_DIR = os.getenv("APP_DATA_DIR")
DATA_DIR = Path(APP_DATA_DIR).expanduser() if APP_DATA_DIR else PROJECT_ROOT / "data"
if not DATA_DIR.is_absolute():
    DATA_DIR = PROJECT_ROOT / DATA_DIR
DATA_DIR = DATA_DIR.resolve()

SHOP_DB = DATA_DIR / "shop.db"
MILVUS_DB = DATA_DIR / "milvus_day14.db"
THREAD_OWNERS_DB = DATA_DIR / "day19_thread_owners.db"
CHECKPOINT_DB = DATA_DIR / "day19_session_checkpoints.db"
MILVUS_INIT_MARKER = DATA_DIR / ".milvus_day14_initialized"
