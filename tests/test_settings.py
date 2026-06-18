from __future__ import annotations

import json
from pathlib import Path

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
    assert payload == {"public": {"-2001": True}}


def test_atomic_write_does_not_leave_partial_file(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.set_public(-2001, True)

    # After a successful save the directory holds the real file and no stragglers.
    siblings = [p.name for p in tmp_path.iterdir()]
    assert siblings == ["chat_settings.json"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"public": {"-2001": True}}


def test_independent_chats_have_independent_state(tmp_path: Path):
    settings = ChatSettings(tmp_path / "chat_settings.json")
    settings.set_public(-2001, True)
    settings.set_public(-2002, False)

    assert settings.is_public(-2001) is True
    assert settings.is_public(-2002) is False
    assert settings.is_public(-2003) is False  # untouched chat defaults to closed
