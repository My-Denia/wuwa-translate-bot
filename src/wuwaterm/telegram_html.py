"""Telegram HTML validation and stripping helpers."""

from __future__ import annotations

from html.parser import HTMLParser


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
