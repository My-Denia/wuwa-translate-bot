from __future__ import annotations

import json
import os
import stat

import pytest

from wuwaterm.channel_reply_index import ChannelReplyIndex
from wuwaterm.channel_reply_schema import (
    ChannelReplyPayloadError,
    parse_channel_reply_payload,
)


VALID_PAYLOAD = {
    "version": 1,
    "entries": [
        {
            "chat_id": -2001,
            "message_id": 4001,
            "expires_at": 1100.0,
            "reply_message_ids": [5001],
        }
    ],
}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(version=999),
        lambda payload: payload["entries"][0].update(chat_id=True),
        lambda payload: payload["entries"][0].update(message_id=1.5),
        lambda payload: payload["entries"][0].update(expires_at=float("nan")),
        lambda payload: payload["entries"][0].update(reply_message_ids=[False]),
        lambda payload: payload["entries"][0].update(reply_message_ids=[]),
    ],
)
def test_channel_reply_schema_rejects_ambiguous_types(mutate) -> None:
    payload = json.loads(json.dumps(VALID_PAYLOAD))
    mutate(payload)

    with pytest.raises(ChannelReplyPayloadError):
        parse_channel_reply_payload(payload)


def test_channel_reply_index_rejects_unknown_version(tmp_path) -> None:
    path = tmp_path / "channel_replies.json"
    payload = json.loads(json.dumps(VALID_PAYLOAD))
    payload["version"] = 2
    path.write_text(json.dumps(payload), encoding="utf-8")

    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)

    assert index.entry_count() == 0
    assert index.load_failure_count() == 1
    assert index.last_load_succeeded() is False


def test_oversized_expiry_is_malformed_without_crashing_runtime_load(tmp_path) -> None:
    payload = json.loads(json.dumps(VALID_PAYLOAD))
    payload["entries"][0]["expires_at"] = 10**1000

    rows, malformed = parse_channel_reply_payload(payload, allow_partial=True)

    assert rows == []
    assert malformed is True

    path = tmp_path / "channel_replies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    assert index.entry_count() == 0
    assert index.load_failure_count() == 1
    assert index.last_load_succeeded() is False


def test_channel_reply_file_fsync_failure_preserves_old_file(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    index.remember(1, 1, 101)
    old_bytes = path.read_bytes()
    real_fsync = os.fsync

    def fail_regular_file(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("file fsync failed")
        real_fsync(fd)

    monkeypatch.setattr("wuwaterm.channel_reply_index.os.fsync", fail_regular_file)
    index.remember(2, 2, 202)

    assert path.read_bytes() == old_bytes
    assert index.last_save_succeeded() is False
    assert index.last_save_durable() is False


def test_channel_reply_write_failure_preserves_old_file_and_cleans_temp(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    index.remember(1, 1, 101)
    old_bytes = path.read_bytes()

    def partial_write_then_fail(_payload, file, **_kwargs) -> None:
        file.write('{"partial":')
        raise OSError("write failed")

    monkeypatch.setattr(
        "wuwaterm.channel_reply_index.json.dump", partial_write_then_fail
    )
    index.remember(2, 2, 202)

    assert path.read_bytes() == old_bytes
    assert index.last_save_succeeded() is False
    assert index.last_save_durable() is False
    assert not list(tmp_path.glob(".channel_replies.json.*"))


def test_channel_reply_flush_failure_preserves_old_file_and_cleans_temp(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    index.remember(1, 1, 101)
    old_bytes = path.read_bytes()

    def fail_flush(_file) -> None:
        raise OSError("flush failed")

    monkeypatch.setattr("wuwaterm.channel_reply_index._flush_file", fail_flush)
    index.remember(2, 2, 202)

    assert path.read_bytes() == old_bytes
    assert index.last_save_succeeded() is False
    assert index.last_save_durable() is False
    assert not list(tmp_path.glob(".channel_replies.json.*"))


def test_channel_reply_replace_failure_preserves_old_file(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    index.remember(1, 1, 101)
    old_bytes = path.read_bytes()

    def fail_replace(_source, _target) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("wuwaterm.channel_reply_index.os.replace", fail_replace)
    index.remember(2, 2, 202)

    assert path.read_bytes() == old_bytes
    assert index.last_save_succeeded() is False


def test_channel_reply_directory_fsync_reports_uncertain_with_new_file_readable(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    index.remember(1, 1, 101)
    real_fsync = os.fsync

    def fail_directory(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr("wuwaterm.channel_reply_index.os.fsync", fail_directory)
    index.remember(2, 2, 202)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert {row["chat_id"] for row in persisted["entries"]} == {1, 2}
    assert index.last_save_succeeded() is True
    assert index.last_save_durable() is False
    assert index.save_failure_count() == 1


def test_channel_reply_directory_open_failure_reports_uncertain_after_replace(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(storage_path=path, clock=lambda: 1000.0)
    index.remember(1, 1, 101)
    real_open = os.open

    def fail_directory_open(path_arg, flags, *args, **kwargs):
        if os.fspath(path_arg) == os.fspath(tmp_path):
            raise OSError("directory open failed")
        return real_open(path_arg, flags, *args, **kwargs)

    monkeypatch.setattr("wuwaterm.channel_reply_index.os.open", fail_directory_open)
    index.remember(2, 2, 202)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert {row["chat_id"] for row in persisted["entries"]} == {1, 2}
    assert index.last_save_succeeded() is True
    assert index.last_save_durable() is False
    assert index.save_failure_count() == 1
