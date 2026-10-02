"""Exercise ingestion and index lifecycle without any embedding or LLM request."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.paths import KNOWLEDGE_DIR
from app.services.knowledge import EMBEDDING_CONFIG, load_knowledge_corpus
from scripts import init_knowledge as initializer


def test_demo_corpus_keeps_original_facts_and_named_sources():
    corpus = load_knowledge_corpus(KNOWLEDGE_DIR)
    contents = "\n".join(doc.page_content for doc in corpus.documents)
    for fact in (
        "商品支持签收后7天内申请退款。",
        "退款申请审核通过后，款项将在3个工作日内原路退回。",
        "商品出现质量问题时，可以申请退货或者换货。",
        "非质量问题商品需要保持商品完整，不影响二次销售。",
        "会员用户每月可以领取一张满100减10元优惠券。",
    ):
        assert fact in contents
    assert corpus.file_count == 5
    assert {doc.metadata["source"] for doc in corpus.documents} == {
        "售后规则", "物流规则", "会员规则", "支付规则", "商品规则",
    }
    assert all(len(doc.page_content) <= 500 for doc in corpus.documents)
    assert all("合成业务演示资料" in path.read_text(encoding="utf-8") for path in KNOWLEDGE_DIR.glob("*.md"))


def test_long_markdown_is_chunked_with_overlap_and_source(tmp_path):
    directory = tmp_path / "knowledge"
    directory.mkdir()
    body = "".join(chr(0x4E00 + index) for index in range(1300))
    (directory / "policy.md").write_text("# 售后规则\n\n" + body, encoding="utf-8-sig")
    corpus = load_knowledge_corpus(directory)
    assert len(corpus.documents) > 2
    assert all(len(doc.page_content) <= 500 for doc in corpus.documents)
    assert [doc.metadata["chunk_index"] for doc in corpus.documents] == list(range(len(corpus.documents)))
    assert all(doc.metadata["source"] == "售后规则" for doc in corpus.documents)
    assert all(doc.metadata["source_path"] == "data/knowledge/policy.md" for doc in corpus.documents)
    body_chunks = [doc.page_content for doc in corpus.documents if len(doc.page_content) > 100]
    for previous, current in zip(body_chunks, body_chunks[1:]):
        assert previous[-80:] == current[:80]


def test_missing_title_uses_filename_and_nested_source_path(tmp_path):
    nested = tmp_path / "knowledge" / "nested"
    nested.mkdir(parents=True)
    (nested / "policy.md").write_text("Policy content", encoding="utf-8")
    corpus = load_knowledge_corpus(nested.parent)
    assert corpus.documents[0].metadata == {
        "source": "policy", "source_path": "data/knowledge/nested/policy.md", "chunk_index": 0,
    }


def test_fingerprint_is_portable_and_sensitive_to_content_and_configuration(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "policy.md").write_bytes(b"# Policy\r\n\r\nBusiness facts\r\n")
    (second / "policy.md").write_text("# Policy\n\nBusiness facts\n", encoding="utf-8")
    original = load_knowledge_corpus(first).fingerprint
    assert load_knowledge_corpus(second).fingerprint == original
    assert load_knowledge_corpus(first, chunk_size=400).fingerprint != original
    assert load_knowledge_corpus(first, chunk_overlap=50).fingerprint != original
    assert load_knowledge_corpus(first, embedding_config={"model": "different-model"}).fingerprint != original
    (first / "policy.md").write_text("# Policy\n\nChanged facts", encoding="utf-8")
    changed = load_knowledge_corpus(first).fingerprint
    assert changed != original
    (first / "policy.md").rename(first / "renamed.md")
    assert load_knowledge_corpus(first).fingerprint != changed


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    policy = knowledge / "policy.md"
    policy.write_text("# Policy\n\nDemo business facts.", encoding="utf-8")
    database = tmp_path / "runtime" / "milvus_day14.db"
    marker = database.parent / ".milvus_day14_initialized"
    monkeypatch.setattr(initializer, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(initializer, "MILVUS_DB", database)
    monkeypatch.setattr(initializer, "INIT_MARKER", marker)

    def create_index(corpus):
        database.mkdir(exist_ok=True)

    builder = Mock(side_effect=create_index)
    count = Mock(return_value=len(load_knowledge_corpus(knowledge).documents))
    monkeypatch.setattr(initializer, "_build_index", builder)
    monkeypatch.setattr(initializer, "_collection_row_count", count)
    return SimpleNamespace(knowledge=knowledge, policy=policy, database=database, marker=marker, builder=builder, count=count)


def test_success_atomically_publishes_marker_and_unchanged_corpus_skips(sandbox, monkeypatch):
    replace = Mock(wraps=os.replace)
    monkeypatch.setattr(initializer.os, "replace", replace)
    initializer.init_knowledge()
    marker = json.loads(sandbox.marker.read_text(encoding="utf-8"))
    corpus = load_knowledge_corpus(sandbox.knowledge)
    assert marker["fingerprint"] == corpus.fingerprint
    assert marker["file_count"] == 1
    assert marker["chunk_count"] == len(corpus.documents)
    assert marker["configuration"]["embedding"] == EMBEDDING_CONFIG
    temporary, destination = replace.call_args.args
    assert temporary.parent == destination.parent == sandbox.marker.parent
    assert destination == sandbox.marker
    assert not list(sandbox.marker.parent.glob(".knowledge-*.tmp"))
    initializer.init_knowledge()
    assert sandbox.builder.call_count == 1
    assert sandbox.count.call_count == 2


def test_changed_markdown_rebuilds_and_refreshes_fingerprint(sandbox):
    initializer.init_knowledge()
    first = json.loads(sandbox.marker.read_text(encoding="utf-8"))["fingerprint"]
    sandbox.policy.write_text("# Policy\n\nUpdated demo facts.", encoding="utf-8")
    initializer.init_knowledge()
    assert sandbox.builder.call_count == 2
    assert json.loads(sandbox.marker.read_text(encoding="utf-8"))["fingerprint"] != first


@pytest.mark.parametrize("reason", ["legacy", "invalid_json", "version", "fingerprint", "missing_database", "missing_collection", "wrong_count"])
def test_stale_or_incomplete_index_rebuilds(sandbox, reason):
    sandbox.database.parent.mkdir()
    if reason != "missing_database":
        sandbox.database.mkdir()
    corpus = load_knowledge_corpus(sandbox.knowledge)
    marker = {"version": 1, "fingerprint": corpus.fingerprint, "chunk_count": len(corpus.documents)}
    if reason == "version":
        marker["version"] = 0
    if reason == "fingerprint":
        marker["fingerprint"] = "old-fingerprint"
    text = json.dumps(marker)
    if reason == "legacy":
        text = "initialized"
    if reason == "invalid_json":
        text = "{"
    sandbox.marker.write_text(text, encoding="utf-8")
    if reason in {"missing_collection", "wrong_count"}:
        sandbox.count.side_effect = [None if reason == "missing_collection" else 0, len(corpus.documents)]
    initializer.init_knowledge()
    sandbox.builder.assert_called_once()
    assert json.loads(sandbox.marker.read_text(encoding="utf-8"))["fingerprint"] == corpus.fingerprint


@pytest.mark.parametrize("failure", ["embedding_or_insert", "row_count", "marker_publication"])
def test_failed_initialization_never_leaves_success_marker(sandbox, monkeypatch, failure):
    sandbox.database.parent.mkdir()
    sandbox.marker.write_text("initialized", encoding="utf-8")
    if failure == "embedding_or_insert":
        sandbox.builder.side_effect = RuntimeError("simulated embedding/insert failure")
    elif failure == "row_count":
        sandbox.count.return_value = 0
    else:
        monkeypatch.setattr(initializer.os, "replace", Mock(side_effect=OSError("simulated marker failure")))
    with pytest.raises((RuntimeError, OSError)):
        initializer.init_knowledge()
    assert not sandbox.marker.exists()
    assert not list(sandbox.marker.parent.glob(".knowledge-*.tmp"))


@pytest.mark.parametrize("invalid_input", ["empty", "missing", "invalid_utf8"])
def test_invalid_corpus_is_rejected_before_database_is_touched(sandbox, invalid_input):
    sandbox.database.parent.mkdir()
    sandbox.marker.write_text("initialized", encoding="utf-8")
    if invalid_input == "empty":
        sandbox.policy.write_text("\n\n", encoding="utf-8")
    elif invalid_input == "missing":
        sandbox.policy.unlink()
        sandbox.knowledge.rmdir()
    else:
        sandbox.policy.write_bytes(b"\xff")
    with pytest.raises((ValueError, UnicodeDecodeError)):
        initializer.init_knowledge()
    sandbox.builder.assert_not_called()
    sandbox.count.assert_not_called()
    assert sandbox.marker.read_text(encoding="utf-8") == "initialized"
    assert not sandbox.database.exists()


def test_collection_validation_uses_only_milvus_metadata_and_closes_client(monkeypatch):
    client = SimpleNamespace(
        has_collection=Mock(return_value=True),
        get_collection_stats=Mock(return_value={"row_count": "7"}),
        close=Mock(),
    )
    factory = Mock(return_value=client)
    monkeypatch.setitem(sys.modules, "pymilvus", SimpleNamespace(MilvusClient=factory))
    assert initializer._collection_row_count() == 7
    factory.assert_called_once_with(uri=str(initializer.MILVUS_DB))
    client.has_collection.assert_called_once_with("knowledge", timeout=10)
    client.close.assert_called_once()


def test_index_builder_preserves_embedding_collection_and_index_contract(tmp_path, monkeypatch):
    (tmp_path / "policy.md").write_text("# Policy\n\nDemo facts.", encoding="utf-8")
    corpus = load_knowledge_corpus(tmp_path)
    embeddings = SimpleNamespace(model="text-embedding-v4")
    build = Mock()
    monkeypatch.setitem(sys.modules, "app.core.llm", SimpleNamespace(embeddings=embeddings))
    monkeypatch.setitem(sys.modules, "langchain_milvus", SimpleNamespace(Milvus=SimpleNamespace(from_documents=build)))
    initializer._build_index(corpus)
    kwargs = build.call_args.kwargs
    assert kwargs["documents"] is corpus.documents
    assert kwargs["embedding"] is embeddings
    assert kwargs["collection_name"] == "knowledge"
    assert kwargs["drop_old"] is True
    assert kwargs["index_params"] == {"index_type": "FLAT", "metric_type": "COSINE", "params": {}}
