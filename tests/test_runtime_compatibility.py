"""Naming changes preserve existing data and fail closed for credentials."""
import importlib.util

import pytest
from fastapi import HTTPException

from app.api.sessions import resolve_optional_user_id, resolve_user_id
from app.core import paths


@pytest.fixture
def keys(monkeypatch):
    for prefix in ("SESSION", "DAY19"):
        for user in ("A", "B"):
            monkeypatch.delenv(f"{prefix}_USER_{user}_KEY", raising=False)
    return monkeypatch


def test_new_credentials_override_legacy_as_a_pair(keys):
    keys.setenv("SESSION_USER_A_KEY", "new-a")
    keys.setenv("SESSION_USER_B_KEY", "new-b")
    keys.setenv("DAY19_USER_A_KEY", "old-a")
    keys.setenv("DAY19_USER_B_KEY", "old-b")
    assert resolve_user_id("new-a") == "user_a"
    assert resolve_user_id("new-b") == "user_b"
    with pytest.raises(HTTPException) as error:
        resolve_user_id("old-a")
    assert error.value.status_code == 401


def test_legacy_only_credentials_still_work(keys):
    keys.setenv("DAY19_USER_A_KEY", "old-a")
    keys.setenv("DAY19_USER_B_KEY", "old-b")
    assert resolve_user_id("old-a") == "user_a"
    assert resolve_user_id("old-b") == "user_b"


def test_optional_request_identity_defers_missing_and_invalid_keys_to_sql_node(keys):
    keys.setenv("SESSION_USER_A_KEY", "new-a")
    keys.setenv("SESSION_USER_B_KEY", "new-b")
    assert resolve_optional_user_id(None) is None
    with pytest.raises(HTTPException) as error:
        resolve_optional_user_id("invalid")
    assert error.value.status_code == 401
    assert resolve_optional_user_id("new-a") == "user_a"


@pytest.mark.parametrize("a,b", [("new-a", None), ("", "new-b"), ("same", "same")])
def test_incomplete_empty_or_duplicate_new_pair_never_falls_back(keys, a, b):
    if a is not None:
        keys.setenv("SESSION_USER_A_KEY", a)
    if b is not None:
        keys.setenv("SESSION_USER_B_KEY", b)
    keys.setenv("DAY19_USER_A_KEY", "old-a")
    keys.setenv("DAY19_USER_B_KEY", "old-b")
    with pytest.raises(HTTPException) as error:
        resolve_user_id("old-a")
    assert error.value.status_code == 503


@pytest.mark.parametrize("current,legacy,is_directory", [
    ("milvus.db", "milvus_day14.db", True),
    ("thread_owners.db", "day19_thread_owners.db", False),
    ("session_checkpoints.db", "day19_session_checkpoints.db", False),
])
def test_existing_volume_names_are_retained_without_modification(tmp_path, monkeypatch, current, legacy, is_directory):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    assert paths._runtime_path(current, legacy) == tmp_path / current
    old = tmp_path / legacy
    if is_directory:
        old.mkdir()
        content = old / "preserve"
    else:
        content = old
    content.write_bytes(b"existing user data")
    assert paths._runtime_path(current, legacy) == old
    assert content.read_bytes() == b"existing user data"
    new = tmp_path / current
    if is_directory:
        new.mkdir()
    else:
        new.touch()
    assert paths._runtime_path(current, legacy) == new
    assert content.read_bytes() == b"existing user data"


@pytest.mark.parametrize("old_exists,new_exists,expected", [
    (True, False, ("milvus_day14.db", ".milvus_day14_initialized")),
    (True, True, ("milvus.db", ".milvus_initialized")),
    (False, False, ("milvus.db", ".milvus_initialized")),
])
def test_marker_follows_selected_vector_store(tmp_path, monkeypatch, old_exists, new_exists, expected):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    if old_exists:
        (tmp_path / "milvus_day14.db").mkdir()
    if new_exists:
        (tmp_path / "milvus.db").mkdir()
    spec = importlib.util.spec_from_file_location("isolated_runtime_paths", paths.__file__)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    assert (loaded.MILVUS_DB.name, loaded.MILVUS_INIT_MARKER.name) == expected


def test_thread_databases_always_select_a_single_generation(tmp_path, monkeypatch):
    current = ("thread_owners.db", "session_checkpoints.db")
    legacy = ("day19_thread_owners.db", "day19_session_checkpoints.db")
    layouts = [([], current), (list(legacy), legacy), ([legacy[0]], legacy),
               ([legacy[1]], legacy), ([current[0]], current), ([current[1]], current)]
    for index, (existing, expected) in enumerate(layouts):
        folder = tmp_path / str(index)
        folder.mkdir()
        for name in existing:
            (folder / name).write_bytes(b"preserve")
        monkeypatch.setattr(paths, "DATA_DIR", folder)
        selected = paths._thread_database_paths()
        assert tuple(path.name for path in selected) == expected
        assert all((folder / name).read_bytes() == b"preserve" for name in existing)


@pytest.mark.parametrize("old_names", [
    ["day19_thread_owners.db"],
    ["day19_thread_owners.db", "day19_session_checkpoints.db"],
])
def test_incomplete_current_pair_with_legacy_data_fails_closed(tmp_path, monkeypatch, old_names):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    for name in ["thread_owners.db", *old_names]:
        (tmp_path / name).write_bytes(b"preserve")
    with pytest.raises(RuntimeError, match="current pair is incomplete"):
        paths._thread_database_paths()
    assert all(path.read_bytes() == b"preserve" for path in tmp_path.iterdir())


def test_complete_current_pair_takes_precedence_with_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    names = ["thread_owners.db", "session_checkpoints.db", "day19_thread_owners.db", "day19_session_checkpoints.db"]
    for name in names:
        (tmp_path / name).write_bytes(b"preserve")
    with caplog.at_level("WARNING"):
        selected = paths._thread_database_paths()
    assert tuple(path.name for path in selected) == tuple(names[:2])
    assert "using the current pair" in caplog.text
    assert all((tmp_path / name).read_bytes() == b"preserve" for name in names)
