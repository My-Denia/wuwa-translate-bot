from __future__ import annotations

from wuwaterm.telegram_text import split_telegram_text, telegram_text_units


def test_split_telegram_text_prefers_word_boundary_near_limit():
    text = ("alpha beta " * 20) + "omega"
    chunks = split_telegram_text(text, limit=60)

    assert "".join(chunks) == text
    assert all(telegram_text_units(chunk) <= 60 for chunk in chunks)
    assert chunks[0].endswith(" ")
    assert not chunks[0].endswith("alph")


def test_split_telegram_text_hard_cuts_when_no_boundary_exists():
    text = "A" * 125
    chunks = split_telegram_text(text, limit=50)

    assert chunks == ["A" * 50, "A" * 50, "A" * 25]


def test_split_telegram_text_counts_utf16_units_for_emoji():
    text = "😀" * 7
    chunks = split_telegram_text(text, limit=6)

    assert [telegram_text_units(chunk) for chunk in chunks] == [6, 6, 2]
    assert "".join(chunks) == text


def test_split_telegram_text_handles_unsplittable_codepoint_over_tiny_limit():
    chunks = split_telegram_text("😀A", limit=1)

    assert chunks == ["😀", "A"]
