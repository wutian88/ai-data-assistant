"""Initialize the Markdown demo corpus; only replace the knowledge collection."""
import json
import os
import tempfile
from pathlib import Path

from app.core.paths import KNOWLEDGE_DIR, MILVUS_DB, MILVUS_INIT_MARKER
from app.services.knowledge import (
    COLLECTION_NAME,
    EMBEDDING_CONFIG,
    INDEX_PARAMS,
    KnowledgeCorpus,
    load_knowledge_corpus,
)

INIT_MARKER = MILVUS_INIT_MARKER
MARKER_VERSION = 1


def _read_marker() -> dict | None:
    try:
        marker = json.loads(INIT_MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def _collection_row_count() -> int | None:
    """Validate an existing local collection without making an embedding request."""
    from pymilvus import MilvusClient

    client = MilvusClient(uri=str(MILVUS_DB))
    try:
        if not client.has_collection(COLLECTION_NAME, timeout=10):
            return None
        stats = client.get_collection_stats(COLLECTION_NAME, timeout=10)
        count = stats.get("row_count")
        return int(count) if count is not None else None
    finally:
        client.close()


def _build_index(corpus: KnowledgeCorpus) -> None:
    # Defer model configuration and network-capable clients until rebuilding is needed.
    from langchain_milvus import Milvus
    from app.core.llm import embeddings

    if embeddings.model != EMBEDDING_CONFIG["model"]:
        raise RuntimeError("Knowledge fingerprint embedding configuration is out of date")
    Milvus.from_documents(
        documents=corpus.documents,
        embedding=embeddings,
        collection_name=COLLECTION_NAME,
        connection_args={"uri": str(MILVUS_DB)},
        index_params=INDEX_PARAMS,
        drop_old=True,
    )


def _write_marker(marker: dict) -> None:
    """Publish success atomically after all chunks have been verified."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=INIT_MARKER.parent,
            prefix=".knowledge-", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(marker, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, INIT_MARKER)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def init_knowledge() -> None:
    # Validate every input before even opening Milvus, or removing an existing marker.
    corpus = load_knowledge_corpus(KNOWLEDGE_DIR)
    expected_count = len(corpus.documents)
    marker = _read_marker()
    if (
        MILVUS_DB.exists()
        and marker is not None
        and marker.get("version") == MARKER_VERSION
        and marker.get("fingerprint") == corpus.fingerprint
        and marker.get("chunk_count") == expected_count
    ):
        try:
            actual_count = _collection_row_count()
        except Exception as exc:
            # A stale or unreadable index must not be reported as initialized.
            print(f"[init-rag] existing collection validation failed: {type(exc).__name__}")
        else:
            if actual_count == expected_count:
                print(f"[init-rag] unchanged corpus; initialization skipped: {MILVUS_DB}")
                return

    MILVUS_DB.parent.mkdir(parents=True, exist_ok=True)
    INIT_MARKER.unlink(missing_ok=True)
    print(f"[init-rag] rebuilding knowledge collection: {MILVUS_DB}")
    try:
        _build_index(corpus)
        if _collection_row_count() != expected_count:
            raise RuntimeError("Knowledge collection row count does not match the corpus")
        _write_marker({
            "version": MARKER_VERSION,
            "fingerprint": corpus.fingerprint,
            "file_count": corpus.file_count,
            "chunk_count": expected_count,
            "configuration": corpus.configuration,
        })
    except BaseException:
        INIT_MARKER.unlink(missing_ok=True)
        raise
    print(f"[init-rag] initialized {corpus.file_count} Markdown files / {expected_count} chunks")


if __name__ == "__main__":
    init_knowledge()
