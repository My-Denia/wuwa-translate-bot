"""Strict shared schema for the linked-channel reply index."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


CHANNEL_REPLY_INDEX_VERSION = 1


class ChannelReplyPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class ChannelReplyRow:
    chat_id: int
    message_id: int
    expires_at: float
    reply_message_ids: tuple[int, ...]


def parse_channel_reply_payload(
    payload: Any,
    *,
    allow_partial: bool = False,
) -> tuple[list[ChannelReplyRow], bool]:
    if not isinstance(payload, dict):
        raise ChannelReplyPayloadError("payload must be an object")
    if (
        type(payload.get("version")) is not int
        or payload["version"] != CHANNEL_REPLY_INDEX_VERSION
    ):
        raise ChannelReplyPayloadError("unsupported payload version")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ChannelReplyPayloadError("entries must be a list")
    parsed: list[ChannelReplyRow] = []
    malformed = False
    for raw in entries:
        try:
            parsed.append(_parse_row(raw))
        except ChannelReplyPayloadError:
            if not allow_partial:
                raise
            malformed = True
    return parsed, malformed


def _parse_row(raw: Any) -> ChannelReplyRow:
    if not isinstance(raw, dict):
        raise ChannelReplyPayloadError("entry must be an object")
    chat_id = _strict_int(raw.get("chat_id"), "chat_id")
    message_id = _strict_int(raw.get("message_id"), "message_id")
    expires_raw = raw.get("expires_at")
    if isinstance(expires_raw, bool) or not isinstance(expires_raw, (int, float)):
        raise ChannelReplyPayloadError("expires_at must be numeric")
    try:
        expires_at = float(expires_raw)
    except (OverflowError, ValueError) as exc:
        raise ChannelReplyPayloadError("expires_at is out of range") from exc
    if not math.isfinite(expires_at):
        raise ChannelReplyPayloadError("expires_at must be finite")
    reply_raw = raw.get("reply_message_ids")
    if not isinstance(reply_raw, list) or not reply_raw:
        raise ChannelReplyPayloadError("reply_message_ids must be a non-empty list")
    reply_message_ids = tuple(
        _strict_int(value, "reply_message_id") for value in reply_raw
    )
    return ChannelReplyRow(chat_id, message_id, expires_at, reply_message_ids)


def _strict_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise ChannelReplyPayloadError(f"{name} must be an integer")
    return value
