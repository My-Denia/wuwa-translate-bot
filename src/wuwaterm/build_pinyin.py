"""Build-only pinyin helpers for generated dictionary rows."""

from __future__ import annotations

from pypinyin import Style, lazy_pinyin

from .normalize import normalize_ascii


def pinyin_for(value: str) -> str:
    return normalize_ascii("".join(lazy_pinyin(value, style=Style.NORMAL)))


def pinyin_abbrev_for(value: str) -> str:
    parts = lazy_pinyin(value, style=Style.FIRST_LETTER)
    return normalize_ascii("".join(parts))
