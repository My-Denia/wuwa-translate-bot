"""Dictionary-backed pair review on submitted Unicode scalar coordinates.

This module is domain core. It reads the terminology database through
``TermService`` and must not import ``sentence`` or construct a translator.
Offsets are counted on the submitted source/target strings (Unicode scalars,
half-open), never on ``prepare_text`` output.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .constants import CATEGORY_ORDER
from .lookup import TermService
from .models import TermEntry

RULE_VERSION = "review-v1"
RULE_TERM_PAIR = "review.term_pair"
RULE_SENTENCE_MEANING = "review.sentence_meaning"
MAX_SIDE_SCALARS = 2000
CHOICE_OFFICIAL_PAIR = "official_pair"
CHOICE_NOT_A_TERM = "not_a_term"
VERDICT_VERIFIED = "verified_constraint"
VERDICT_CONFLICT = "confirmed_conflict"
VERDICT_NEEDS_REVIEW = "needs_review"
VERDICT_NOT_EVALUATED = "not_evaluated"
_SURROGATE_MIN = "\ud800"
_SURROGATE_MAX = "\udfff"
_SENTENCE_END = re.compile(r"[。．.！？!?\n]")


class ReviewRequestError(ValueError):
    """Input the review engine refuses. ``code`` is an application error code."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


@dataclass(frozen=True)
class ReviewResolution:
    mention_id: str
    choice: str
    zh: str | None = None
    en: str | None = None


@dataclass(frozen=True)
class ReviewSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ReviewSource:
    source_file: str
    source_id: str


@dataclass(frozen=True)
class ReviewCandidate:
    zh: str
    en: str
    category: str
    sources: tuple[ReviewSource, ...]


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    verdict: str
    rule_id: str
    source_span: ReviewSpan
    target_span: ReviewSpan | None
    candidates: tuple[ReviewCandidate, ...]
    candidates_truncated: bool = False


@dataclass(frozen=True)
class ReviewDictionary:
    schema_version: str | None
    source_commit: str | None
    term_count: int


@dataclass(frozen=True)
class ReviewCoverage:
    evaluated: int
    not_evaluated: int
    rules: tuple[str, ...]


@dataclass(frozen=True)
class ReviewReport:
    source_revision: str
    target_revision: str
    rule_version: str
    dictionary: ReviewDictionary
    coverage: ReviewCoverage
    findings: tuple[ReviewFinding, ...]
    truncated: bool = False


@dataclass(frozen=True)
class _TermSpan:
    start: int
    end: int
    source: str
    order: int


def mention_id(start: int, end: int, text: str) -> str:
    return f"{start}:{end}:{text}"


def text_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _has_isolated_surrogate(text: str) -> bool:
    return any(_SURROGATE_MIN <= char <= _SURROGATE_MAX for char in text)


def _is_ascii_word_char(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _ascii_word_boundaries_ok(text: str, start: int, end: int, source: str) -> bool:
    if not source:
        return False
    if _is_ascii_word_char(source[0]) and start > 0 and _is_ascii_word_char(text[start - 1]):
        return False
    if (
        _is_ascii_word_char(source[-1])
        and end < len(text)
        and _is_ascii_word_char(text[end])
    ):
        return False
    return True


def _source_priority(entry: TermEntry) -> int:
    if entry.source_id.startswith("OccupationConfig_") and entry.source_id.endswith(
        "_Name"
    ):
        return 0
    if "RoleInfo" in entry.source_file:
        return 1
    if entry.category == "speaker":
        return 8
    return 5


def _validate_side(name: str, text: object) -> str:
    if not isinstance(text, str) or text == "":
        raise ReviewRequestError("invalid_request", f"{name} must be a non-empty string")
    if _has_isolated_surrogate(text):
        raise ReviewRequestError("invalid_request", f"{name} contains an isolated surrogate")
    if len(text) > MAX_SIDE_SCALARS:
        raise ReviewRequestError("input_too_long", f"{name} exceeds {MAX_SIDE_SCALARS} Unicode scalars")
    return text


def _validate_direction(direction: object) -> str:
    if direction not in {"en", "zh"}:
        raise ReviewRequestError("invalid_request", "direction must be en or zh")
    return direction


def _coerce_resolution(item: object) -> ReviewResolution:
    if isinstance(item, ReviewResolution):
        resolution = item
    elif isinstance(item, dict):
        mention_id_value = item.get("mention_id")
        choice = item.get("choice")
        if not isinstance(mention_id_value, str) or not mention_id_value:
            raise ReviewRequestError("invalid_request", "resolution mention_id is required")
        if choice not in {CHOICE_OFFICIAL_PAIR, CHOICE_NOT_A_TERM}:
            raise ReviewRequestError("invalid_request", "resolution choice is not valid")
        allowed = (
            {"mention_id", "choice", "zh", "en"}
            if choice == CHOICE_OFFICIAL_PAIR
            else {"mention_id", "choice"}
        )
        if set(item) != allowed:
            raise ReviewRequestError("invalid_request", "resolution keys are not valid")
        zh = item.get("zh") if choice == CHOICE_OFFICIAL_PAIR else None
        en = item.get("en") if choice == CHOICE_OFFICIAL_PAIR else None
        if choice == CHOICE_OFFICIAL_PAIR and (
            not isinstance(zh, str) or not zh or not isinstance(en, str) or not en
        ):
            raise ReviewRequestError("invalid_request", "official_pair requires zh and en")
        resolution = ReviewResolution(
            mention_id=mention_id_value, choice=choice, zh=zh, en=en
        )
    else:
        raise ReviewRequestError("invalid_request", "resolution is not valid")
    if resolution.choice == CHOICE_OFFICIAL_PAIR and (
        not resolution.zh or not resolution.en
    ):
        raise ReviewRequestError("invalid_request", "official_pair requires zh and en")
    if resolution.choice not in {CHOICE_OFFICIAL_PAIR, CHOICE_NOT_A_TERM}:
        raise ReviewRequestError("invalid_request", "resolution choice is not valid")
    if not resolution.mention_id:
        raise ReviewRequestError("invalid_request", "resolution mention_id is required")
    return resolution


def _dictionary_snapshot(service: TermService) -> ReviewDictionary:
    metadata = service.metadata()
    return ReviewDictionary(
        schema_version=metadata.get("schema_version"),
        source_commit=metadata.get("source_commit"),
        term_count=service.term_count(),
    )


def _surface_index(
    entries: Sequence[TermEntry],
) -> dict[str, list[TermEntry]]:
    surfaces: dict[str, list[TermEntry]] = {}
    for entry in entries:
        if "\n" in entry.zh or "\n" in entry.en:
            continue
        if entry.zh:
            surfaces.setdefault(entry.zh, []).append(entry)
        if entry.en and entry.en != entry.zh:
            surfaces.setdefault(entry.en, []).append(entry)
    return surfaces


def _collect_mentions(
    source: str, surfaces: dict[str, list[TermEntry]]
) -> list[_TermSpan]:
    spans: list[_TermSpan] = []
    for order, surface in enumerate(surfaces):
        start = source.find(surface)
        while start != -1:
            end = start + len(surface)
            if _ascii_word_boundaries_ok(source, start, end, surface):
                spans.append(_TermSpan(start=start, end=end, source=surface, order=order))
            start = source.find(surface, start + 1)
    selected: list[_TermSpan] = []
    occupied: list[tuple[int, int]] = []
    for span in sorted(
        spans,
        key=lambda item: (-(item.end - item.start), item.order, item.start, item.source),
    ):
        if any(span.start < end and start < span.end for start, end in occupied):
            continue
        selected.append(span)
        occupied.append((span.start, span.end))
    selected.sort(key=lambda item: (item.start, -item.end, item.source))
    return selected


def _candidates_for(entries: Sequence[TermEntry]) -> tuple[ReviewCandidate, ...]:
    grouped: dict[tuple[str, str, str], list[ReviewSource]] = {}
    first_entry: dict[tuple[str, str, str], TermEntry] = {}
    for entry in entries:
        key = (entry.zh, entry.en, entry.category)
        first_entry.setdefault(key, entry)
        sources = grouped.setdefault(key, [])
        source = ReviewSource(source_file=entry.source_file, source_id=entry.source_id)
        if source not in sources:
            sources.append(source)
    candidates = [
        ReviewCandidate(
            zh=zh, en=en, category=category, sources=tuple(grouped[(zh, en, category)])
        )
        for zh, en, category in grouped
    ]
    candidates.sort(
        key=lambda candidate: (
            CATEGORY_ORDER.get(candidate.category, 999),
            _source_priority(first_entry[(candidate.zh, candidate.en, candidate.category)]),
            len(candidate.zh),
            candidate.en,
            candidate.zh,
        )
    )
    return tuple(candidates)


def _sentence_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        if start < end:
            ranges.append((start, end))
        start = end
    if start < len(text):
        ranges.append((start, len(text)))
    if not ranges and text:
        ranges.append((0, len(text)))
    return ranges


def _sentence_index(ranges: Sequence[tuple[int, int]], start: int) -> int | None:
    for index, (lo, hi) in enumerate(ranges):
        if lo <= start < hi:
            return index
    if ranges and start == ranges[-1][1]:
        return len(ranges) - 1
    return None


def _find_occurrences(text: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    found: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index == -1:
            break
        end = index + len(needle)
        if _ascii_word_boundaries_ok(text, index, end, needle):
            found.append((index, end))
        start = index + 1
    return found


def _official_form(candidate: ReviewCandidate, direction: str) -> str:
    return candidate.en if direction == "en" else candidate.zh


def _span_at(text: str, start: int, end: int) -> ReviewSpan:
    return ReviewSpan(start=start, end=end, text=text[start:end])


def _elsewhere_has_form(
    target: str,
    sentence: tuple[int, int],
    forms: Sequence[str],
) -> bool:
    lo, hi = sentence
    for form in forms:
        for start, end in _find_occurrences(target, form):
            if start < lo or end > hi:
                return True
    return False


def _next_unused(
    occurrences: Sequence[tuple[int, int]], used: set[tuple[int, int]]
) -> tuple[int, int] | None:
    for span in occurrences:
        if span not in used:
            return span
    return None


def _judge_mention(
    *,
    source: str,
    target: str,
    direction: str,
    mention: _TermSpan,
    candidates: tuple[ReviewCandidate, ...],
    source_sentences: Sequence[tuple[int, int]],
    target_sentences: Sequence[tuple[int, int]],
    used_target: set[tuple[int, int]],
    resolution: ReviewResolution | None,
) -> ReviewFinding:
    finding_id = mention_id(mention.start, mention.end, mention.source)
    source_span = _span_at(source, mention.start, mention.end)
    source_index = _sentence_index(source_sentences, mention.start)
    corresponding: tuple[int, int] | None = None
    if (
        source_index is not None
        and target_sentences
        and source_index < len(target_sentences)
    ):
        corresponding = target_sentences[source_index]

    if resolution is not None and resolution.choice == CHOICE_NOT_A_TERM:
        return ReviewFinding(
            id=finding_id,
            verdict=VERDICT_NOT_EVALUATED,
            rule_id=RULE_TERM_PAIR,
            source_span=source_span,
            target_span=None,
            candidates=candidates,
        )

    selected: ReviewCandidate | None = None
    if resolution is not None and resolution.choice == CHOICE_OFFICIAL_PAIR:
        for candidate in candidates:
            if candidate.zh == resolution.zh and candidate.en == resolution.en:
                selected = candidate
                break
        if selected is None:
            raise ReviewRequestError(
                "invalid_request",
                "official_pair is not a candidate of this mention",
            )

    forms = (
        (_official_form(selected, direction),)
        if selected is not None
        else tuple(dict.fromkeys(_official_form(candidate, direction) for candidate in candidates))
    )

    if corresponding is None:
        return ReviewFinding(
            id=finding_id,
            verdict=VERDICT_NOT_EVALUATED,
            rule_id=RULE_TERM_PAIR,
            source_span=source_span,
            target_span=None,
            candidates=candidates,
        )

    lo, hi = corresponding
    region = target[lo:hi]
    in_region: dict[str, list[tuple[int, int]]] = {}
    appearing: list[str] = []
    for form in forms:
        local = []
        for start, end in _find_occurrences(region, form):
            absolute = (lo + start, lo + end)
            if absolute not in used_target:
                local.append(absolute)
        in_region[form] = local
        if local:
            appearing.append(form)

    if selected is not None:
        chosen_form = _official_form(selected, direction)
        hit = _next_unused(in_region.get(chosen_form, ()), used_target)
        if hit is not None:
            used_target.add(hit)
            return ReviewFinding(
                id=finding_id,
                verdict=VERDICT_VERIFIED,
                rule_id=RULE_TERM_PAIR,
                source_span=source_span,
                target_span=_span_at(target, hit[0], hit[1]),
                candidates=candidates,
            )
        # Corresponding sentence is a reliable alignment region.
        mismatch = None
        for form, hits in in_region.items():
            if form == chosen_form:
                continue
            mismatch = _next_unused(hits, used_target)
            if mismatch is not None:
                break
        target_span = (
            _span_at(target, mismatch[0], mismatch[1])
            if mismatch is not None
            else _span_at(target, lo, hi)
        )
        return ReviewFinding(
            id=finding_id,
            verdict=VERDICT_CONFLICT,
            rule_id=RULE_TERM_PAIR,
            source_span=source_span,
            target_span=target_span,
            candidates=candidates,
        )

    if len(appearing) > 1:
        hit = _next_unused(in_region[appearing[0]], used_target)
        if hit is not None:
            used_target.add(hit)
        return ReviewFinding(
            id=finding_id,
            verdict=VERDICT_NEEDS_REVIEW,
            rule_id=RULE_TERM_PAIR,
            source_span=source_span,
            target_span=_span_at(target, hit[0], hit[1]) if hit is not None else None,
            candidates=candidates,
        )

    if appearing:
        hit = _next_unused(in_region[appearing[0]], used_target)
        if hit is not None:
            used_target.add(hit)
            return ReviewFinding(
                id=finding_id,
                verdict=VERDICT_VERIFIED,
                rule_id=RULE_TERM_PAIR,
                source_span=source_span,
                target_span=_span_at(target, hit[0], hit[1]),
                candidates=candidates,
            )

    if _elsewhere_has_form(target, corresponding, forms):
        return ReviewFinding(
            id=finding_id,
            verdict=VERDICT_NOT_EVALUATED,
            rule_id=RULE_TERM_PAIR,
            source_span=source_span,
            target_span=None,
            candidates=candidates,
        )
    return ReviewFinding(
        id=finding_id,
        verdict=VERDICT_NEEDS_REVIEW,
        rule_id=RULE_TERM_PAIR,
        source_span=source_span,
        target_span=None,
        candidates=candidates,
    )


def review_pair(
    service: TermService,
    source: str,
    target: str,
    direction: str,
    resolutions: Sequence[object] = (),
) -> ReviewReport:
    """Review ``source`` against ``target`` using one ``TermService`` snapshot."""
    source = _validate_side("source", source)
    target = _validate_side("target", target)
    direction = _validate_direction(direction)
    parsed_resolutions = tuple(_coerce_resolution(item) for item in resolutions)
    dictionary = _dictionary_snapshot(service)
    surfaces = _surface_index(service.entries())
    mentions = _collect_mentions(source, surfaces)
    source_sentences = _sentence_ranges(source)
    target_sentences = _sentence_ranges(target)
    used_target: set[tuple[int, int]] = set()
    findings: list[ReviewFinding] = []
    seen_resolution_ids: set[str] = set()
    by_id: dict[str, ReviewResolution] = {}
    for resolution in parsed_resolutions:
        if resolution.mention_id in by_id:
            raise ReviewRequestError("invalid_request", "duplicate resolution mention_id")
        by_id[resolution.mention_id] = resolution

    for mention in mentions:
        candidates = _candidates_for(surfaces[mention.source])
        finding_id = mention_id(mention.start, mention.end, mention.source)
        resolution = by_id.get(finding_id)
        if resolution is not None:
            seen_resolution_ids.add(finding_id)
        findings.append(
            _judge_mention(
                source=source,
                target=target,
                direction=direction,
                mention=mention,
                candidates=candidates,
                source_sentences=source_sentences,
                target_sentences=target_sentences,
                used_target=used_target,
                resolution=resolution,
            )
        )

    missing = set(by_id) - seen_resolution_ids
    if missing:
        raise ReviewRequestError("invalid_request", "resolution mention_id does not match a finding")

    evaluated = sum(
        1
        for finding in findings
        if finding.verdict in {VERDICT_VERIFIED, VERDICT_CONFLICT, VERDICT_NEEDS_REVIEW}
    )
    not_evaluated = 1 + sum(
        1 for finding in findings if finding.verdict == VERDICT_NOT_EVALUATED
    )
    return ReviewReport(
        source_revision=text_revision(source),
        target_revision=text_revision(target),
        rule_version=RULE_VERSION,
        dictionary=dictionary,
        coverage=ReviewCoverage(
            evaluated=evaluated,
            not_evaluated=not_evaluated,
            rules=(RULE_TERM_PAIR, RULE_SENTENCE_MEANING),
        ),
        findings=tuple(findings),
        truncated=False,
    )
