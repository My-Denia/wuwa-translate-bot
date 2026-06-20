"""Telegram text-size helpers."""

from __future__ import annotations


TELEGRAM_TEXT_MESSAGE_LIMIT = 4096
_SPLIT_BOUNDARY_CHARS = frozenset(
    " \t\r\n"
    ".,;:!?)]}、，。！？；：）】》"
)


def telegram_text_units(text: str) -> int:
    """Telegram text limits are counted in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def split_telegram_text(
    text: str, limit: int = TELEGRAM_TEXT_MESSAGE_LIMIT
) -> list[str]:
    """Split plain text without exceeding Telegram's UTF-16 message limit.

    The splitter preserves text exactly. It prefers a whitespace or punctuation
    boundary near the limit, then falls back to a hard UTF-16-safe cut.
    """
    if telegram_text_units(text) <= limit:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        cut = _best_cut_index(text, start, limit)
        chunks.append(text[start:cut])
        start = cut
    return chunks or [""]


def _best_cut_index(text: str, start: int, limit: int) -> int:
    units = 0
    hard_cut = start
    boundary_cut = start
    boundary_units = 0
    min_boundary_units = max(1, int(limit * 0.75))
    for index in range(start, len(text)):
        char = text[index]
        char_units = telegram_text_units(char)
        if units + char_units > limit:
            break
        units += char_units
        hard_cut = index + 1
        if char.isspace() or char in _SPLIT_BOUNDARY_CHARS:
            boundary_cut = hard_cut
            boundary_units = units
    else:
        return len(text)
    if boundary_cut > start and boundary_units >= min_boundary_units:
        return boundary_cut
    if hard_cut == start:
        return start + 1
    return hard_cut
