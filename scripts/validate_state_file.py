from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.channel_reply_schema import parse_channel_reply_payload  # noqa: E402


def _chat_id_from_json(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        try:
            parsed = int(normalized)
        except ValueError:
            return None
        return parsed if str(parsed) == normalized else None
    return None


def validate_chat_settings(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("chat settings must be an object")
    public = payload.get("public", {})
    allowed = payload.get("allowed", [])
    if not isinstance(public, dict) or not isinstance(allowed, list):
        raise ValueError("chat settings schema is invalid")
    for key, value in public.items():
        if not isinstance(value, bool) or _chat_id_from_json(key) is None:
            raise ValueError("chat settings public state is invalid")
    for item in allowed:
        if _chat_id_from_json(item) is None:
            raise ValueError("chat settings allowlist is invalid")


def validate_channel_replies(payload: object) -> None:
    parse_channel_reply_payload(payload)


def validate(path: str | Path, filename: str) -> None:
    with Path(path).open(encoding="utf-8") as file:
        payload = json.load(file)
    if filename == "chat_settings.json":
        validate_chat_settings(payload)
    elif filename == "channel_replies.json":
        validate_channel_replies(payload)
    else:
        raise ValueError(f"unknown state file type: {filename}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(
            "usage: validate_state_file.py "
            "PATH chat_settings.json|channel_replies.json",
            file=sys.stderr,
        )
        return 2
    path, filename = argv
    try:
        validate(path, filename)
    except Exception as exc:
        print(f"invalid {filename}: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
