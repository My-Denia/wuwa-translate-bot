from __future__ import annotations

import json
from pathlib import Path
from threading import Barrier, Thread

import pytest

import wuwaterm.settings as settings_module
from wuwaterm.settings import (
    ChatSettings,
    ChatSettingsDurabilityError,
    ChatSettingsError,
)


def test_default_is_closed_when_file_missing(tmp_path: Path):
    settings = ChatSettings(tmp_path / "missing.json")
    assert settings.is_public(-2001) is False
    assert not (tmp_path / "missing.json").exists()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("false", False),
        ("true", False),
        (1, False),
        (0, False),
        (None, False),
        ({}, False),
        ([], False),
    ],
)
def test_public_loader_requires_json_boolean_true(tmp_path: Path, value, expected):
    path = tmp_path / "chat_settings.json"
    path.write_text(
        json.dumps({"public": {"-2001": value}, "allowed": []}),
        encoding="utf-8",
    )

    settings = ChatSettings(path)

    assert settings.is_public(-2001) is expected
    assert settings.public_count() == (1 if expected else 0)


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


def test_corrupt_file_loads_closed_but_mutation_refuses_to_overwrite(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    original = "{this is not json"
    path.write_text(original, encoding="utf-8")

    settings = ChatSettings(path)
    assert settings.is_public(-2001) is False

    with pytest.raises(ChatSettingsError):
        settings.set_public(-2001, True)

    assert settings.is_public(-2001) is False
    assert path.read_text(encoding="utf-8") == original


def test_invalid_utf8_loads_closed_but_mutation_refuses_to_overwrite(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    original = b"\xff\xfe"
    path.write_bytes(original)

    settings = ChatSettings(path)
    assert settings.is_public(-2001) is False
    assert settings.is_allowed(-2001) is False

    with pytest.raises(ChatSettingsError):
        settings.set_public(-2001, True)
    with pytest.raises(ChatSettingsError):
        settings.allow(-2001)

    assert path.read_bytes() == original


def test_invalid_chat_id_types_never_become_authorized(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    original = json.dumps(
        {
            "public": {"01": True, "-2001": False},
            "allowed": [True, -2001.9],
        }
    )
    path.write_text(original, encoding="utf-8")

    settings = ChatSettings(path)
    assert settings.is_public(1) is False
    assert settings.is_allowed(1) is False
    assert settings.is_allowed(-2001) is False

    with pytest.raises(ChatSettingsError):
        settings.allow(-3001)

    assert path.read_text(encoding="utf-8") == original


def test_corrupt_reload_keeps_revoke_fail_closed_in_memory(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.set_public(-2001, True)
    settings.allow(-2001)
    original = "{this is not json"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ChatSettingsError):
        settings.set_public(-2001, False)
    assert settings.is_public(-2001) is False

    with pytest.raises(ChatSettingsError):
        settings.disallow(-2001)
    assert settings.is_allowed(-2001) is False
    assert path.read_text(encoding="utf-8") == original


def test_lock_failure_keeps_revoke_fail_closed_in_memory(tmp_path: Path, monkeypatch):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.allow(-2001)

    def boom(_path):
        raise OSError("lock unavailable")

    monkeypatch.setattr(settings_module, "_file_lock", boom)
    with pytest.raises(OSError):
        settings.disallow(-2001)

    assert settings.is_allowed(-2001) is False
    assert ChatSettings(path).is_allowed(-2001) is True


def test_atomic_write_does_not_leave_partial_file(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.set_public(-2001, True)

    # After a successful save the directory holds the real file and no stragglers.
    siblings = {item.name for item in tmp_path.iterdir()}
    assert siblings == {"chat_settings.json", "chat_settings.json.lock"}
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

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save_state", boom)
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

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save_state", boom)
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

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save_state", boom)
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

    real_save = settings._save_state
    calls = {"n": 0}

    def flaky(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_save(*args)

    monkeypatch.setattr(settings, "_save_state", flaky)
    with pytest.raises(OSError):
        settings.disallow(-2001)  # first attempt: the write fails
    assert settings.is_allowed(-2001) is False  # denied in memory...
    assert ChatSettings(path).is_allowed(-2001) is True  # ...but disk still stale

    settings.disallow(-2001)  # retry once storage recovers
    assert ChatSettings(path).is_allowed(-2001) is False  # now durable on disk


def test_failed_stale_revoke_does_not_resurrect_other_revocation(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "chat_settings.json"
    seed = ChatSettings(path)
    seed.allow(-2001)
    seed.allow(-2002)
    stale = ChatSettings(path)
    revoker = ChatSettings(path)

    assert revoker.disallow(-2002) is True

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(stale, "_save_state", boom)
    with pytest.raises(OSError):
        stale.disallow(-2001)

    # The fresh candidate includes both revocations. Falling back to stale's
    # pre-reload cache here would incorrectly authorize -2002 in this instance.
    assert stale.is_allowed(-2001) is False
    assert stale.is_allowed(-2002) is False
    reloaded = ChatSettings(path)
    assert reloaded.is_allowed(-2001) is True
    assert reloaded.is_allowed(-2002) is False


def test_stale_instances_preserve_independent_updates(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    first = ChatSettings(path)
    stale = ChatSettings(path)

    assert first.allow(-2001) is True
    assert stale.set_public(-3001, True) is True

    reloaded = ChatSettings(path)
    assert reloaded.is_allowed(-2001) is True
    assert reloaded.is_public(-3001) is True


def test_stale_instance_cannot_resurrect_revoked_chat(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    seed = ChatSettings(path)
    seed.allow(-2001)
    revoker = ChatSettings(path)
    stale = ChatSettings(path)

    assert revoker.disallow(-2001) is True
    assert stale.set_public(-3001, True) is True

    reloaded = ChatSettings(path)
    assert reloaded.is_allowed(-2001) is False
    assert reloaded.is_public(-3001) is True


def test_concurrent_instances_serialize_without_lost_updates(tmp_path: Path):
    path = tmp_path / "chat_settings.json"
    first = ChatSettings(path)
    second = ChatSettings(path)
    barrier = Barrier(3)
    errors: list[BaseException] = []

    def run(action):
        try:
            barrier.wait()
            action()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        Thread(target=run, args=(lambda: first.allow(-2001),)),
        Thread(target=run, args=(lambda: second.set_public(-3001, True),)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    reloaded = ChatSettings(path)
    assert reloaded.is_allowed(-2001) is True
    assert reloaded.is_public(-3001) is True


def test_set_public_off_failure_remains_denied_in_memory(tmp_path: Path, monkeypatch):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)
    settings.set_public(-2001, True)

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save_state", boom)
    with pytest.raises(OSError):
        settings.set_public(-2001, False)

    assert settings.is_public(-2001) is False
    assert ChatSettings(path).is_public(-2001) is True


def test_replace_failure_does_not_publish_grant(tmp_path: Path, monkeypatch):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)

    def boom(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr(settings_module.os, "replace", boom)
    with pytest.raises(OSError):
        settings.allow(-2001)

    assert settings.is_allowed(-2001) is False
    assert not path.exists()
    assert not list(tmp_path.glob(".chat_settings.*"))


def test_directory_fsync_failure_publishes_visible_candidate(tmp_path: Path, monkeypatch):
    path = tmp_path / "chat_settings.json"
    settings = ChatSettings(path)

    def boom(_path):
        raise OSError("directory fsync failed")

    monkeypatch.setattr(settings_module, "_fsync_parent_directory", boom)
    with pytest.raises(ChatSettingsDurabilityError):
        settings.allow(-2001)

    assert settings.is_allowed(-2001) is True
    assert ChatSettings(path).is_allowed(-2001) is True


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
