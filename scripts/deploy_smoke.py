#!/usr/bin/env python3
"""Deployment reachability smoke for Telegram Bot API.

This script verifies Bot API reachability. It does not prove that the deployed
long-polling handler consumed the message; that requires an external Telegram
client or test account observing the bot reply.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.logging_utils import redact_id  # noqa: E402


DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_TEXT = "wuwaterm deploy reachability smoke"


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    lines: tuple[str, ...]
    sent_message: bool = False


def run_smoke(
    *,
    token: str | None,
    chat_id: str | None,
    text: str = DEFAULT_TEXT,
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> SmokeResult:
    if not token:
        return SmokeResult(False, ("TELEGRAM_BOT_TOKEN: missing; smoke skipped",))
    with httpx.Client(timeout=timeout, transport=transport) as client:
        try:
            me = client.get(f"{api_base}/bot{token}/getMe")
            me.raise_for_status()
            data = me.json()
        except (httpx.HTTPError, ValueError):
            return SmokeResult(False, ("Bot API getMe: failed",))
        if not data.get("ok"):
            return SmokeResult(False, ("Bot API getMe: failed",))
        lines = ["Bot API getMe: ok"]
        if not chat_id:
            lines.append("TELEGRAM_TEST_CHAT_ID: missing; sendMessage skipped")
            return SmokeResult(True, tuple(lines))

        try:
            sent = client.post(
                f"{api_base}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            sent.raise_for_status()
            sent_data = sent.json()
        except (httpx.HTTPError, ValueError):
            return SmokeResult(False, tuple(lines + ["sendMessage: failed"]))
    if not sent_data.get("ok"):
        return SmokeResult(False, tuple(lines + ["sendMessage: failed"]))
    message_id = sent_data.get("result", {}).get("message_id")
    lines.append(f"sendMessage: ok message_id={redact_id(message_id)}")
    return SmokeResult(True, tuple(lines), sent_message=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.getenv("TELEGRAM_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--text", default=os.getenv("TELEGRAM_TEST_TEXT", DEFAULT_TEXT))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    result = run_smoke(
        token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_TEST_CHAT_ID"),
        text=args.text,
        api_base=args.api_base.rstrip("/"),
        timeout=args.timeout,
    )
    for line in result.lines:
        print(line)
    if not result.ok:
        return 2
    if result.sent_message:
        print("Handler E2E: not verified by this script")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
