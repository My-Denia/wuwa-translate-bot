"""Passive linked-channel auto-translation (HTML-preserving)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .bot import (
    CONFIG_KEY,
    SERVICE_KEY,
    TRANSLATOR_KEY,
    BotConfig,
    _consume_rate_limit,
)
from .lookup import TermService
from .sentence import LLMTranslationError, SentenceTranslator, _llm_configured


LOGGER = logging.getLogger(__name__)

# CJK Ext A, CJK Unified, CJK Compatibility Ideographs, CJK Ext B-F.
CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ebef]")

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


def count_cjk(text: str) -> int:
    return len(CJK_RE.findall(text))


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


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Translate automatic forwards from the linked channel, in-thread.

    Every early exit is silent: this path never posts notices under
    channel posts (a notice comment under every post would be spam).
    """
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    if not config.channel_autotranslate:
        return
    message = update.effective_message
    if message is None:
        return
    # Freshness gate: Telegram replays updates (restart backlog, admin
    # promotion, 24h queue) — historical channel posts must never be
    # translated, regardless of how their update arrives.
    message_date = getattr(message, "date", None)
    if message_date is not None:
        age_seconds = (datetime.now(timezone.utc) - message_date).total_seconds()
        if age_seconds > config.channel_max_age_seconds:
            LOGGER.info(
                "channel autotranslate skipped: stale post message_id=%s",
                message.message_id,
            )
            return
    if message.text:
        plain = message.text
        html_text = message.text_html
        length_limit = config.channel_text_limit
    elif message.caption:
        plain = message.caption
        html_text = message.caption_html
        length_limit = config.channel_caption_limit
    else:
        return
    if not html_text:
        return
    if count_cjk(plain) < config.channel_min_cjk:
        # Free check: no LLM call and no throttle consumption.
        return
    if len(plain) > length_limit:
        LOGGER.debug(
            "channel autotranslate skipped: over length cap message_id=%s",
            message.message_id,
        )
        return
    if not _llm_configured():
        LOGGER.warning("channel autotranslate skipped: LLM endpoint not configured")
        return
    if not _consume_rate_limit(update, context):
        LOGGER.info(
            "channel autotranslate throttled message_id=%s", message.message_id
        )
        return

    chat = update.effective_chat
    chat_type = chat.type if chat else "unknown"

    service: TermService = context.application.bot_data[SERVICE_KEY]
    if service.lookup(plain).exact:
        official = service.term_text(plain)
        if official:
            # Dictionary-first invariant: official text byte-for-byte,
            # plain, trumps formatting. Zero LLM.
            sent_message = await message.reply_text(
                official, reply_to_message_id=message.message_id
            )
            _log_reply(chat_type, message, sent_message, "dictionary")
            return

    translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
    try:
        translated = translator.translate_html(html_text)
    except LLMTranslationError:
        # Budget exhaustion and generic LLM failure both skip silently;
        # no chat/user ids, no response-body echo.
        LOGGER.warning("channel autotranslate skipped: translation unavailable")
        return

    if validate_telegram_html(translated):
        try:
            sent_message = await message.reply_text(
                translated,
                parse_mode="HTML",
                reply_to_message_id=message.message_id,
            )
            _log_reply(chat_type, message, sent_message, "HTML")
            return
        except BadRequest:
            # Safety net: never let formatting fail the reply.
            sent_message = await message.reply_text(
                strip_telegram_html(translated),
                reply_to_message_id=message.message_id,
            )
            _log_reply(chat_type, message, sent_message, "plain-after-badrequest")
            return
    sent_message = await message.reply_text(
        strip_telegram_html(translated), reply_to_message_id=message.message_id
    )
    _log_reply(chat_type, message, sent_message, "plain")


def _log_reply(chat_type: str, message, sent_message, mode: str) -> None:
    LOGGER.info(
        "bot_reply chat_type=%s incoming_message_id=%s reply_message_id=%s "
        "reply_to_message_id=%s mode=%s",
        chat_type,
        message.message_id,
        getattr(sent_message, "message_id", None),
        message.message_id,
        mode,
    )
