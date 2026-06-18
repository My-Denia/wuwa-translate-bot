from __future__ import annotations

import json
from pathlib import Path

import pytest

from wuwaterm.settings import ChatSettings


def test_default_is_closed_when_file_missing(tmp_path: Path):
    settings = ChatSettings(tmp_path / "missing.json")
    assert settings.is_public(-2001) is False
    assert not (tmp_path / "missing.json").exists()


def test_set_public_writes_file_and_reload_round_trips(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)

    assert settings.set_public(-2001, True) is True
    assert settings.is_public(-2001) is True
    assert path.exists()

    # A fresh instance reading the same file sees the persisted state.
    reloaded = ChatSettings(path)
    assert reloaded.is_public(-2001) is True
    assert reloaded.is_public(-9999) is False


def test_set_public_is_idempotent_no_op(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.set_public(-2001, True)
    first_mtime = path.stat().st_mtime_ns

    # Setting the same value again returns False and does NOT rewrite the file.
    assert settings.set_public(-2001, True) is False
    assert path.stat().st_mtime_ns == first_mtime


def test_set_public_off_then_on_persists(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    a = ChatSettings(path)
    a.set_public(-2001, True)
    a.set_public(-2001, False)

    b = ChatSettings(path)
    assert b.is_public(-2001) is False


def test_corrupt_file_does_not_crash_loader(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    path.write_text("{this is not json", encoding="utf-8")

    settings = ChatSettings(path)
    # Corrupt -> treated as empty, every chat at default (closed).
    assert settings.is_public(-2001) is False

    # Writing again rewrites the file cleanly (overwrites the garbage).
    settings.set_public(-2001, True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"public": {"-2001": True}, "allowed": []}


def test_atomic_write_does_not_leave_partial_file(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.set_public(-2001, True)

    # After a successful save the directory holds the real file and no stragglers.
    siblings = [p.name for p in tmp_path.iterdir()]
    assert siblings == ["chat_settings.json"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"public": {"-2001": True}, "allowed": []}


def test_independent_chats_have_independent_state(tmp_path: Path):
    settings = ChatSettings(tmp_path / "chat_settings.json")
    settings.set_public(-2001, True)
    settings.set_public(-2002, False)

    assert settings.is_public(-2001) is True
    assert settings.is_public(-2002) is False
    assert settings.is_public(-2003) is False  # untouched chat defaults to closed


def test_save_failure_rolls_back_public(tmp_path: Path, monkeypatch):
    settings = ChatSettings(tmp_path / "chat_settings.json")

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save", boom)
    with pytest.raises(OSError):
        settings.set_public(-2001, True)
    # In-memory state must match the (unwritten) disk: still at the default.
    assert settings.is_public(-2001) is False


def test_allowlist_round_trips_and_persists(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    assert settings.is_allowed(-2001) is False
    assert settings.allow(-2001) is True
    assert settings.is_allowed(-2001) is True
    assert settings.allow(-2001) is False  # idempotent, no change

    reloaded = ChatSettings(path)
    assert reloaded.is_allowed(-2001) is True
    assert reloaded.is_allowed(-2002) is False
    assert reloaded.allowed_chats() == [-2001]


def test_disallow_removes_from_allowlist(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    a = ChatSettings(path)
    a.allow(-2001)
    a.allow(-2002)
    assert a.disallow(-2001) is True
    assert a.disallow(-2001) is False  # already gone

    b = ChatSettings(path)
    assert b.is_allowed(-2001) is False
    assert b.is_allowed(-2002) is True
    assert b.allowed_chats() == [-2002]


def test_save_failure_rolls_back_allowlist(tmp_path: Path, monkeypatch):
    settings = ChatSettings(tmp_path / "chat_settings.json")

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save", boom)
    with pytest.raises(OSError):
        settings.allow(-2001)
    assert settings.is_allowed(-2001) is False


def test_save_failure_keeps_disallow_removed(tmp_path: Path, monkeypatch):
    # disallow is fail-closed: a write failure must NOT roll the removal back,
    # so a /revoke whose save fails still denies service for this session.
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.allow(-2001)
    assert settings.is_allowed(-2001) is True

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save", boom)
    with pytest.raises(OSError):
        settings.disallow(-2001)
    # Removal is kept in memory (deny wins) even though the disk write failed.
    assert settings.is_allowed(-2001) is False
    # On-disk file is unchanged (still authorized) until a later successful save
    # heals it — the durable revoke needs a retry the owner is told to make.
    reloaded = ChatSettings(path)
    assert reloaded.is_allowed(-2001) is True


def test_disallow_retry_after_failed_save_persists(tmp_path: Path, monkeypatch):
    # After a failed-save disallow the chat is gone from memory but still on
    # disk; a retry once storage recovers must REWRITE the file rather than
    # short-circuit as a no-op and leave the stale on-disk authorization.
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.allow(-2001)

    real_save = settings._save
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_save()

    monkeypatch.setattr(settings, "_save", flaky)
    with pytest.raises(OSError):
        settings.disallow(-2001)  # first attempt: the write fails
    assert settings.is_allowed(-2001) is False  # denied in memory...
    assert ChatSettings(path).is_allowed(-2001) is True  # ...but disk still stale

    settings.disallow(-2001)  # retry once storage recovers
    assert ChatSettings(path).is_allowed(-2001) is False  # now durable on disk


def test_public_and_allowlist_coexist_in_one_file(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    s = ChatSettings(path)
    s.set_public(-2001, True)
    s.allow(-3001)

    reloaded = ChatSettings(path)
    assert reloaded.is_public(-2001) is True
    assert reloaded.is_allowed(-3001) is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"public": {"-2001": True}, "allowed": [-3001]}
