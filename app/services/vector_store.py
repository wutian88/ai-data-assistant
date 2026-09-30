import os
from pathlib import Path

from langchain_milvus import Milvus

from app.core.llm import embeddings
from app.core.paths import MILVUS_DB


# ============================================================
# 1. Milvus Lite 数据库位置
#
# 本机开发：
#   data/milvus_day14.db
#
# Docker：
#   APP_DATA_DIR=/data
#   → /data/milvus_day14.db
# ============================================================

MILVUS_DB.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# 2. Vector Store
# ============================================================

vector_store = Milvus(
    embedding_function=embeddings,
    collection_name="knowledge",
    connection_args={
        "uri": str(MILVUS_DB),
    },
    index_params={
        "index_type": "FLAT",
        "metric_type": "COSINE",
        "params": {},
    },
)


# ============================================================
# 3. Retriever
# ============================================================

retriever = vector_store.as_retriever(
    search_kwargs={
        "k": 3,
    }
)