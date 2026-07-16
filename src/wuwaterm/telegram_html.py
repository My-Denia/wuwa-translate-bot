"""Telegram HTML validation and stripping helpers."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
import secrets


# Telegram's HTML subset: https://core.telegram.org/bots/api#html-style
ALLOWED_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "a",
        "code",
        "pre",
        "blockquote",
        "span",
        "tg-spoiler",
        "tg-emoji",
    }
)

_ENTITY_RE = re.compile(
    r"&(?:#[xX][0-9A-Fa-f]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);"
)
_RESERVED_HTML_PLACEHOLDER_RE = re.compile(
    r"__WUWA_HTML_[0-9a-f]{16}_[0-9]{4}__"
)


class TelegramHTMLIntegrityError(ValueError):
    """Translated HTML no longer contains the protected source structure."""


@dataclass(frozen=True)
class ProtectedTelegramHTML:
    """Telegram HTML with every structural token replaced by an opaque token."""

    protected_text: str
    structures: tuple[tuple[str, str], ...]
    placeholder_prefix: str

    def visible_segments(self) -> tuple[str, ...]:
        """Return text-node segments without exposing structural bytes."""

        segments: list[str] = []
        cursor = 0
        for placeholder, _raw in self.structures:
            position = self.protected_text.find(placeholder, cursor)
            if position < 0:  # pragma: no cover - constructor invariant
                raise TelegramHTMLIntegrityError("protected HTML is inconsistent")
            segments.append(self.protected_text[cursor:position])
            cursor = position + len(placeholder)
        segments.append(self.protected_text[cursor:])
        return tuple(segments)

    def interleave_visible_segments(self, segments: tuple[str, ...]) -> str:
        if len(segments) != len(self.structures) + 1:
            raise TelegramHTMLIntegrityError("visible segment count changed")
        parts: list[str] = []
        for segment, (placeholder, _raw) in zip(
            segments, self.structures, strict=False
        ):
            parts.extend((segment, placeholder))
        parts.append(segments[-1])
        return "".join(parts)

    def restore(self, translated: str) -> str:
        """Restore exact source bytes after enforcing structural integrity."""

        positions: list[int] = []
        without_known = translated
        for placeholder, _raw in self.structures:
            if translated.count(placeholder) != 1:
                raise TelegramHTMLIntegrityError(
                    "structural placeholder is missing or duplicated"
                )
            positions.append(translated.index(placeholder))
            without_known = without_known.replace(placeholder, "")
        if positions != sorted(positions):
            raise TelegramHTMLIntegrityError("structural placeholder order changed")
        if (
            self.placeholder_prefix in without_known
            or _RESERVED_HTML_PLACEHOLDER_RE.search(without_known)
        ):
            raise TelegramHTMLIntegrityError("unknown structural placeholder")
        if "<" in without_known:
            raise TelegramHTMLIntegrityError("translated text added tag-like structure")
        if _structural_tokens(without_known):
            raise TelegramHTMLIntegrityError("translated text added HTML structure")

        restored = translated
        for placeholder, raw in self.structures:
            restored = restored.replace(placeholder, raw)
        if _structural_tokens(restored) != tuple(raw for _placeholder, raw in self.structures):
            raise TelegramHTMLIntegrityError("restored HTML structure changed")
        if not validate_telegram_html(restored):
            raise TelegramHTMLIntegrityError("restored HTML is invalid")
        return restored


def _new_html_placeholder_prefix(source_text: str) -> str:
    while True:
        prefix = f"__WUWA_HTML_{secrets.token_hex(8)}_"
        if prefix not in source_text:
            return prefix


def _tag_end(text: str, start: int) -> int | None:
    quote: str | None = None
    for index in range(start + 1, len(text)):
        char = text[index]
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == ">":
            return index + 1
    return None


def _structural_spans(html_text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(html_text):
        if html_text[index] == "<":
            end = _tag_end(html_text, index)
            if end is not None:
                spans.append((index, end))
                index = end
                continue
        if html_text[index] == "&":
            match = _ENTITY_RE.match(html_text, index)
            if match is not None:
                spans.append((index, match.end()))
                index = match.end()
                continue
        index += 1
    return tuple(spans)


def _structural_tokens(html_text: str) -> tuple[str, ...]:
    return tuple(html_text[start:end] for start, end in _structural_spans(html_text))


def protect_telegram_html(html_text: str) -> ProtectedTelegramHTML:
    """Replace tags, exact attribute bytes, and entities with opaque tokens."""

    prefix = _new_html_placeholder_prefix(html_text)
    parts: list[str] = []
    structures: list[tuple[str, str]] = []
    cursor = 0
    for index, (start, end) in enumerate(_structural_spans(html_text)):
        parts.append(html_text[cursor:start])
        placeholder = f"{prefix}{index:04d}__"
        raw = html_text[start:end]
        parts.append(placeholder)
        structures.append((placeholder, raw))
        cursor = end
    parts.append(html_text[cursor:])
    return ProtectedTelegramHTML(
        protected_text="".join(parts),
        structures=tuple(structures),
        placeholder_prefix=prefix,
    )


class _TelegramHTMLValidator(HTMLParser):
    """Conservative validator: false negatives only cost formatting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.valid = True
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS or not self._attrs_allowed(tag, dict(attrs)):
            self.valid = False
            return
        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.valid = False
            return
        self.stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # None of Telegram's subset is void; self-closing tags are invalid.
        self.valid = False

    def handle_comment(self, data: str) -> None:
        self.valid = False

    def handle_decl(self, decl: str) -> None:
        self.valid = False

    def handle_pi(self, data: str) -> None:
        self.valid = False

    def unknown_decl(self, data: str) -> None:
        self.valid = False

    @staticmethod
    def _attrs_allowed(tag: str, attrs: dict[str, str | None]) -> bool:
        if tag == "a":
            return set(attrs) == {"href"} and bool(attrs.get("href"))
        if tag == "span":
            return set(attrs) == {"class"} and attrs.get("class") == "tg-spoiler"
        if tag == "tg-emoji":
            return set(attrs) == {"emoji-id"} and bool(attrs.get("emoji-id"))
        if tag == "code":
            if not attrs:
                return True
            return set(attrs) == {"class"} and str(attrs.get("class") or "").startswith(
                "language-"
            )
        if tag == "blockquote":
            return not attrs or set(attrs) == {"expandable"}
        return not attrs


class _TelegramHTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def validate_telegram_html(html_text: str) -> bool:
    validator = _TelegramHTMLValidator()
    try:
        validator.feed(html_text)
        validator.close()
    except Exception:  # noqa: BLE001 - any parser failure means "not valid"
        return False
    return validator.valid and not validator.stack


def strip_telegram_html(html_text: str) -> str:
    stripper = _TelegramHTMLStripper()
    try:
        stripper.feed(html_text)
        stripper.close()
    except Exception:  # noqa: BLE001 - must never raise; plain send is safe
        return html_text
    return "".join(stripper.parts)
