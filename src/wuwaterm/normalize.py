"""Normalization helpers for term matching."""

from __future__ import annotations

import re
import unicodedata

from pypinyin import Style, lazy_pinyin

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
VERSION_TAG_RE = re.compile(
    r"(?i)(?:[\[(（【]\s*)?\b(?:ww|wuthering\s*waves)\s*\d+(?:\.\d+){1,2}\b(?:\s*[\])）】])?"
)
SPOILER_RE = re.compile(
    r"(?i)(?:[\[(（【#*_ -]*\s*(?:spoiler|spoilers|spolier|spoliers|剧透)\s*[:：-]?\s*[\])）】#*_ -]*)"
)
QUOTE_BAR_RE = re.compile(r"(?m)^\s*(?:[>|｜│┃▌▍▏]+\s*)+")
BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_markup(value: str) -> str:
    value = TAG_RE.sub("", value)
    value = value.replace("\\n", "\n")
    return value.strip()


def clean_source_text(value: str) -> str:
    value = strip_markup(value)
    if value.startswith("dnt/"):
        value = value[4:]
    return value.strip()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = SPACE_RE.sub("", value)
    return value.casefold()


def normalize_user_text(value: str) -> str:
    """Normalize user-supplied Telegram text before lookup or translation."""

    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = VERSION_TAG_RE.sub("", value)
    value = SPOILER_RE.sub("", value)
    value = QUOTE_BAR_RE.sub("", value)
    lines = [SPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    value = "\n".join(line for line in lines if line)
    value = BLANK_LINES_RE.sub("\n\n", value)
    return value.strip()


def normalize_ascii(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^0-9A-Za-z]+", "", value)
    return value.casefold()


def pinyin_for(value: str) -> str:
    return normalize_ascii("".join(lazy_pinyin(value, style=Style.NORMAL)))


def pinyin_abbrev_for(value: str) -> str:
    parts = lazy_pinyin(value, style=Style.FIRST_LETTER)
    return normalize_ascii("".join(parts))
