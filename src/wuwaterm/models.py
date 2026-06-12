"""Small data objects used across builder and lookup code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TermRecord:
    category: str
    source_file: str
    source_id: str
    text_key: str
    zh: str
    en: str


@dataclass(frozen=True)
class TermEntry:
    id: int
    category: str
    source_file: str
    source_id: str
    text_key: str
    zh: str
    en: str
    pinyin: str
    pinyin_abbrev: str


@dataclass(frozen=True)
class LookupCandidate:
    entry: TermEntry
    score: float
    reason: str


@dataclass(frozen=True)
class LookupResult:
    query: str
    exact: bool
    candidates: tuple[LookupCandidate, ...]

    @property
    def best(self) -> LookupCandidate | None:
        return self.candidates[0] if self.candidates else None

    def official_text(self) -> str | None:
        if not self.best:
            return None
        return self.best.entry.en
