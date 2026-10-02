"""Load versioned Markdown knowledge without opening a database or calling an API."""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
SEPARATORS = ["\n\n", "\n", "。", "！", "？", " ", ""]
EMBEDDING_CONFIG = {"provider": "dashscope", "model": "text-embedding-v4"}
COLLECTION_NAME = "knowledge"
INDEX_PARAMS = {"index_type": "FLAT", "metric_type": "COSINE", "params": {}}


@dataclass(frozen=True)
class KnowledgeCorpus:
    documents: list[Document]
    fingerprint: str
    file_count: int
    configuration: dict[str, Any]


def load_knowledge_corpus(
    directory: Path,
    *,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
    embedding_config: Mapping[str, Any] | None = None,
) -> KnowledgeCorpus:
    """Read all Markdown before initialization can replace the knowledge collection."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"Knowledge directory does not exist: {directory}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SEPARATORS,
        length_function=len,
    )
    configuration = {
        "schema_version": 1,
        "splitter": "recursive-character-v1",
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "separators": SEPARATORS,
        "embedding": dict(embedding_config or EMBEDDING_CONFIG),
        "collection_name": COLLECTION_NAME,
        "index_params": INDEX_PARAMS,
    }
    records = []
    documents = []
    for path in sorted(directory.rglob("*.md"), key=lambda item: item.relative_to(directory).as_posix()):
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            continue
        title = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
        source = title.group(1).strip().rstrip("#").strip() if title else path.stem
        source = source or path.stem
        source_path = (Path("data/knowledge") / path.relative_to(directory)).as_posix()
        metadata = {"source": source, "source_path": source_path}
        chunks = splitter.create_documents([text], [metadata])
        for chunk_index, chunk in enumerate(chunks):
            chunk.metadata["chunk_index"] = chunk_index
        documents.extend(chunks)
        records.append({"path": source_path, "source": source, "content": text})

    if not documents:
        raise ValueError(f"Knowledge corpus must contain non-empty Markdown: {directory}")
    encoded = json.dumps(
        {"configuration": configuration, "files": records},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return KnowledgeCorpus(
        documents=documents,
        fingerprint=hashlib.sha256(encoded).hexdigest(),
        file_count=len(records),
        configuration=configuration,
    )
