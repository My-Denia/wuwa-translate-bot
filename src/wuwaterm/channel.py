"""Passive linked-channel auto-translation (HTML-preserving)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html.parser import HTMLParser

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from .bot import (
    CHANNEL_REPLY_INDEX_KEY,
    CONFIG_KEY,
    SERVICE_KEY,
    TRANSLATOR_KEY,
    BotConfig,
    ChannelReplyIndex,
    _consume_rate_limit,
)
from .lookup import TermService
from .normalize import count_cjk, count_latin
from .sentence import LLMTranslationError, SentenceTranslator, _llm_configured


LOGGER = logging.getLogger(__name__)


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


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Translate automatic forwards from the linked channel, in-thread.

    Every early exit is silent: this path never posts notices under
    channel posts (a notice comment under every post would be spam).

    An edited post updates the bot's existing reply for that post in place
    (tracked per (chat, post) in memory); an edit with no tracked reply —
    after a restart, or a post that was never translated — is skipped, so an
    edit can never add a second reply.
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

    chat = update.effective_chat
    chat_id = chat.id if chat else None
    chat_type = chat.type if chat else "unknown"

    # Edit dedup: an edited post (edit_date set) updates the reply already
    # sent for it. With no tracked reply there is nothing to update, so skip
    # before any CJK / throttle / LLM work — an edit must never add a second
    # reply (a restart drops the in-memory map; those edits skip here too).
    reply_index: ChannelReplyIndex = context.application.bot_data[
        CHANNEL_REPLY_INDEX_KEY
    ]
    is_edit = getattr(message, "edit_date", None) is not None
    existing_reply_id: int | None = None
    if is_edit:
        existing_reply_id = (
            reply_index.get(chat_id, message.message_id)
            if chat_id is not None
            else None
        )
        if existing_reply_id is None:
            LOGGER.info(
                "channel autotranslate skipped: edit with no tracked reply "
                "message_id=%s",
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
    # Direction by script: enough Chinese -> translate to English (default);
    # no Chinese but enough Latin letters -> translate to Chinese. Anything
    # else (emoji / links / numbers only) is not worth translating -> skip.
    cjk = count_cjk(plain)
    if cjk >= config.channel_min_cjk:
        to_chinese = False
    elif cjk == 0 and count_latin(plain) >= config.channel_min_latin:
        to_chinese = True
    else:
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

    service: TermService = context.application.bot_data[SERVICE_KEY]
    lookup_result = service.lookup(plain)
    if lookup_result.exact and lookup_result.best:
        official = (
            lookup_result.best.entry.zh if to_chinese else lookup_result.best.entry.en
        )
        if official:
            # Dictionary-first invariant: official text byte-for-byte,
            # plain, trumps formatting. Zero LLM.
            await _emit(
                context,
                reply_index=reply_index,
                message=message,
                chat_id=chat_id,
                chat_type=chat_type,
                is_edit=is_edit,
                existing_reply_id=existing_reply_id,
                text=official,
                parse_mode=None,
                mode="dictionary",
            )
            return

    translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
    try:
        translated = translator.translate_html(html_text, to_chinese=to_chinese)
    except LLMTranslationError:
        # Budget exhaustion and generic LLM failure both skip silently;
        # no chat/user ids, no response-body echo.
        LOGGER.warning("channel autotranslate skipped: translation unavailable")
        return

    if validate_telegram_html(translated):
        try:
            await _emit(
                context,
                reply_index=reply_index,
                message=message,
                chat_id=chat_id,
                chat_type=chat_type,
                is_edit=is_edit,
                existing_reply_id=existing_reply_id,
                text=translated,
                parse_mode="HTML",
                mode="HTML",
            )
            return
        except BadRequest:
            # Safety net: never let formatting fail the reply.
            await _emit(
                context,
                reply_index=reply_index,
                message=message,
                chat_id=chat_id,
                chat_type=chat_type,
                is_edit=is_edit,
                existing_reply_id=existing_reply_id,
                text=strip_telegram_html(translated),
                parse_mode=None,
                mode="plain-after-badrequest",
            )
            return
    await _emit(
        context,
        reply_index=reply_index,
        message=message,
        chat_id=chat_id,
        chat_type=chat_type,
        is_edit=is_edit,
        existing_reply_id=existing_reply_id,
        text=strip_telegram_html(translated),
        parse_mode=None,
        mode="plain",
    )


async def _emit(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reply_index: ChannelReplyIndex,
    message,
    chat_id: int | None,
    chat_type: str,
    is_edit: bool,
    existing_reply_id: int | None,
    text: str,
    parse_mode: str | None,
    mode: str,
) -> None:
    """Deliver one translation, then remember it for later edits.

    New post: send an in-thread reply and remember (chat, post) -> reply id.
    Edit: update that remembered reply in place instead of adding a second
    one. On the edit path a "message is not modified" error is the dedup
    ideal (identical re-translation) and is a silent no-op; an HTML edit
    Telegram rejects re-raises so the caller's plain fallback runs; a failed
    plain edit (reply deleted / uneditable) is swallowed — an edit must never
    crash the listener or leave a duplicate.
    """
    if is_edit:
        try:
            await context.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=existing_reply_id,
                parse_mode=parse_mode,
            )
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                LOGGER.info(
                    "channel edit no-op (unchanged translation) "
                    "incoming_message_id=%s reply_message_id=%s",
                    message.message_id,
                    existing_reply_id,
                )
                return
            if parse_mode is not None:
                # HTML edit rejected -> let the caller retry as plain.
                raise
            LOGGER.info(
                "channel edit skipped: reply not updatable "
                "incoming_message_id=%s reply_message_id=%s",
                message.message_id,
                existing_reply_id,
            )
            return
        _log_emit(chat_type, message, existing_reply_id, mode, edited=True)
        return

    sent_message = await message.reply_text(
        text, parse_mode=parse_mode, reply_to_message_id=message.message_id
    )
    reply_message_id = getattr(sent_message, "message_id", None)
    if chat_id is not None and reply_message_id is not None:
        reply_index.remember(chat_id, message.message_id, reply_message_id)
    _log_emit(chat_type, message, reply_message_id, mode, edited=False)


def _log_emit(chat_type: str, message, reply_message_id, mode: str, *, edited: bool) -> None:
    LOGGER.info(
        "bot_reply chat_type=%s incoming_message_id=%s reply_message_id=%s "
        "reply_to_message_id=%s mode=%s edited=%s",
        chat_type,
        message.message_id,
        reply_message_id,
        message.message_id,
        mode,
        edited,
    )
