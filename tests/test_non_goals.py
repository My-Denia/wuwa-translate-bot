from __future__ import annotations

from pathlib import Path


def test_no_webhook_inline_alias_or_messagehandler_in_runtime_code():
    root = Path(__file__).resolve().parents[1]
    runtime_files = list((root / "src" / "wuwaterm").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    assert "run_webhook" not in text
    assert "set_webhook" not in text
    assert "InlineQueryHandler" not in text
    assert "MessageHandler" not in text
    assert "alias" not in text.casefold()
