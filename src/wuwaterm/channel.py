"""Passive linked-channel auto-translation (HTML-preserving)."""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from .bot import (
    CHANNEL_REPLY_INDEX_KEY,
    CHAT_SETTINGS_KEY,
    CONFIG_KEY,
    SERVICE_KEY,
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    TRANSLATOR_KEY,
    BotConfig,
    ChannelReplyIndex,
    _consume_rate_limit,
)
from .lookup import TermService
from .normalize import count_cjk, count_latin
from .sentence import LLMTranslationError, SentenceTranslator, _llm_configured
from .settings import ChatSettings
from .telegram_html import strip_telegram_html, validate_telegram_html
from .telegram_text import split_telegram_text, telegram_text_units


LOGGER = logging.getLogger(__name__)
LONG_OUTPUT_MODE_SUFFIX = "plain-split"


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

    # Authorization gate (fail-closed): only translate for groups on the
    # allowlist. Slash commands are gated in _is_authorized_group_sender; this
    # mirrors it for the auto-forward path so an unauthorized or revoked group
    # the bot has not yet left (e.g. a leave_chat that failed) cannot keep
    # getting its linked-channel posts translated and burn LLM budget.
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    if chat_id is None or not settings.is_allowed(chat_id):
        LOGGER.info(
            "channel autotranslate skipped: chat not authorized chat_id=%s",
            chat_id,
        )
        return

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
        if chat_id is not None:
            waited_for_original = await reply_index.wait_in_flight(
                chat_id, message.message_id
            )
            if not waited_for_original and existing_reply_id is None:
                # With a non-blocking handler, PTB may schedule an edit task
                # before the original post task has run far enough to mark
                # itself in-flight. Yield once so that already-scheduled
                # original task can register; true restart/no-entry edits still
                # skip without reaching throttle or LLM work.
                await asyncio.sleep(0)
                waited_for_original = await reply_index.wait_in_flight(
                    chat_id, message.message_id
                )
            if waited_for_original:
                existing_reply_id = reply_index.get(chat_id, message.message_id)
        if existing_reply_id is None:
            LOGGER.info(
                "channel autotranslate skipped: edit with no tracked reply "
                "message_id=%s",
                message.message_id,
            )
            return
    edit_work_token: int | None = None
    if is_edit and chat_id is not None:
        update_id = getattr(update, "update_id", None)
        edit_work_token = reply_index.begin_edit(
            chat_id,
            message.message_id,
            update_id if isinstance(update_id, int) else None,
        )
    marked_in_flight = False
    if not is_edit and chat_id is not None:
        reply_index.mark_in_flight(chat_id, message.message_id)
        marked_in_flight = True
    try:
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
        service: TermService = context.application.bot_data[SERVICE_KEY]
        lookup_result = service.lookup_exact(plain)
        official = None
        if lookup_result.exact and lookup_result.best:
            official = (
                lookup_result.best.entry.zh if to_chinese else lookup_result.best.entry.en
            )
        if not official and not _llm_configured():
            LOGGER.warning("channel autotranslate skipped: LLM endpoint not configured")
            return
        if not _consume_rate_limit(update, context):
            LOGGER.info(
                "channel autotranslate throttled message_id=%s", message.message_id
            )
            return

        if official:
            # Dictionary-first invariant: official text byte-for-byte,
            # plain, trumps formatting. Zero LLM.
            if not _passes_channel_delivery_gate(context, chat_id):
                LOGGER.info(
                    "channel autotranslate skipped: chat authorization changed "
                    "before delivery chat_id=%s",
                    chat_id,
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
                edit_work_token=edit_work_token,
                text=official,
                parse_mode=None,
                mode="dictionary",
            )
            return

        translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
        try:
            translated = await translator.translate_html_async(
                html_text, to_chinese=to_chinese
            )
        except LLMTranslationError:
            # Budget exhaustion and generic LLM failure both skip silently;
            # no chat/user ids, no response-body echo.
            LOGGER.warning("channel autotranslate skipped: translation unavailable")
            return

        if validate_telegram_html(translated):
            plain_translated = strip_telegram_html(translated)
            if telegram_text_units(plain_translated) > TELEGRAM_TEXT_MESSAGE_LIMIT:
                if not _passes_channel_delivery_gate(context, chat_id):
                    LOGGER.info(
                        "channel autotranslate skipped: chat authorization changed "
                        "before delivery chat_id=%s",
                        chat_id,
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
                    edit_work_token=edit_work_token,
                    text=plain_translated,
                    parse_mode=None,
                    mode="HTML",
                )
                return
            try:
                if not _passes_channel_delivery_gate(context, chat_id):
                    LOGGER.info(
                        "channel autotranslate skipped: chat authorization changed "
                        "before delivery chat_id=%s",
                        chat_id,
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
                    edit_work_token=edit_work_token,
                    text=translated,
                    parse_mode="HTML",
                    mode="HTML",
                )
                return
            except BadRequest:
                # Safety net: never let formatting fail the reply.
                if not _passes_channel_delivery_gate(context, chat_id):
                    LOGGER.info(
                        "channel autotranslate skipped: chat authorization changed "
                        "before delivery chat_id=%s",
                        chat_id,
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
                    edit_work_token=edit_work_token,
                    text=plain_translated,
                    parse_mode=None,
                    mode="plain-after-badrequest",
                )
                return
        if not _passes_channel_delivery_gate(context, chat_id):
            LOGGER.info(
                "channel autotranslate skipped: chat authorization changed "
                "before delivery chat_id=%s",
                chat_id,
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
            edit_work_token=edit_work_token,
            text=strip_telegram_html(translated),
            parse_mode=None,
            mode="plain",
        )
    finally:
        if marked_in_flight and chat_id is not None:
            reply_index.finish_in_flight(chat_id, message.message_id)


def _passes_channel_delivery_gate(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int | None
) -> bool:
    if chat_id is None:
        return False
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    return settings.is_allowed(chat_id)


async def _emit(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reply_index: ChannelReplyIndex,
    message,
    chat_id: int | None,
    chat_type: str,
    is_edit: bool,
    existing_reply_id: int | None,
    edit_work_token: int | None,
    text: str,
    parse_mode: str | None,
    mode: str,
) -> None:
    """Deliver one translation, then remember its reply chunks for later edits.

    New post: send one or more in-thread replies and remember their IDs.
    Edit: update those remembered replies in place, add continuation chunks,
    or delete stale extras. On the edit path, a "message is not modified"
    error is the dedup ideal (identical re-translation) and is a silent no-op.
    An HTML edit Telegram rejects re-raises so the caller's plain fallback
    runs. A failed plain edit (reply deleted / uneditable) is swallowed; an
    edit must never crash the listener or leave a duplicate.
    """
    chunks, delivery_parse_mode, delivery_mode = _channel_delivery_chunks(
        text, parse_mode, mode
    )
    if is_edit:
        if chat_id is not None and edit_work_token is not None:
            async with reply_index.edit_delivery_lock(chat_id, message.message_id):
                if not reply_index.is_latest_edit(
                    chat_id, message.message_id, edit_work_token
                ):
                    LOGGER.info(
                        "channel edit skipped: stale translation "
                        "incoming_message_id=%s reply_message_id=%s",
                        message.message_id,
                        existing_reply_id,
                    )
                    return
                existing_reply_ids = reply_index.get_many(chat_id, message.message_id)
                if not existing_reply_ids and existing_reply_id is not None:
                    existing_reply_ids = (existing_reply_id,)
                await _edit_reply_chunks(
                    context,
                    reply_index=reply_index,
                    message=message,
                    chat_type=chat_type,
                    existing_reply_ids=existing_reply_ids,
                    chunks=chunks,
                    chat_id=chat_id,
                    parse_mode=delivery_parse_mode,
                    mode=delivery_mode,
                )
                return
        existing_reply_ids = (
            reply_index.get_many(chat_id, message.message_id)
            if chat_id is not None
            else ()
        )
        if not existing_reply_ids and existing_reply_id is not None:
            existing_reply_ids = (existing_reply_id,)
        await _edit_reply_chunks(
            context,
            reply_index=reply_index,
            message=message,
            chat_type=chat_type,
            existing_reply_ids=existing_reply_ids,
            chunks=chunks,
            chat_id=chat_id,
            parse_mode=delivery_parse_mode,
            mode=delivery_mode,
        )
        return

    reply_message_ids = []
    for chunk in chunks:
        sent_message = await message.reply_text(
            chunk,
            parse_mode=delivery_parse_mode,
            reply_to_message_id=message.message_id,
        )
        reply_message_id = getattr(sent_message, "message_id", None)
        if reply_message_id is not None:
            reply_message_ids.append(reply_message_id)
            if chat_id is not None:
                reply_index.remember_many(
                    chat_id, message.message_id, tuple(reply_message_ids)
                )
        _log_emit(chat_type, message, reply_message_id, delivery_mode, edited=False)


def _channel_delivery_chunks(
    text: str, parse_mode: str | None, mode: str
) -> tuple[list[str], str | None, str]:
    if parse_mode == "HTML":
        visible_text = strip_telegram_html(text)
        if telegram_text_units(visible_text) > TELEGRAM_TEXT_MESSAGE_LIMIT:
            return (
                split_telegram_text(visible_text),
                None,
                f"{mode}-{LONG_OUTPUT_MODE_SUFFIX}",
            )
        return [text], parse_mode, mode
    chunks = split_telegram_text(text)
    if len(chunks) > 1:
        return chunks, parse_mode, f"{mode}-{LONG_OUTPUT_MODE_SUFFIX}"
    return chunks, parse_mode, mode


async def _edit_reply_chunks(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reply_index: ChannelReplyIndex,
    message,
    chat_type: str,
    existing_reply_ids: tuple[int, ...],
    chunks: list[str],
    chat_id: int | None,
    parse_mode: str | None,
    mode: str,
) -> None:
    if chat_id is None or not existing_reply_ids:
        return
    remembered_reply_ids: list[int] = []
    remaining_reply_ids = list(existing_reply_ids)
    for chunk in chunks:
        while remaining_reply_ids:
            reply_message_id = remaining_reply_ids.pop(0)
            edit_applied = await _edit_existing_reply(
                context,
                message=message,
                chat_type=chat_type,
                existing_reply_id=reply_message_id,
                text=chunk,
                chat_id=chat_id,
                parse_mode=parse_mode,
                mode=mode,
            )
            if edit_applied:
                remembered_reply_ids.append(reply_message_id)
                break
            if not remembered_reply_ids:
                failed_delete_ids = await _delete_reply_chunks(
                    context,
                    message=message,
                    chat_id=chat_id,
                    reply_message_ids=tuple(remaining_reply_ids),
                )
                if failed_delete_ids:
                    reply_index.remember_many(
                        chat_id, message.message_id, failed_delete_ids
                    )
                else:
                    reply_index.forget(chat_id, message.message_id)
                return
        else:
            sent_message = await message.reply_text(
                chunk,
                parse_mode=parse_mode,
                reply_to_message_id=message.message_id,
            )
            reply_message_id = getattr(sent_message, "message_id", None)
            if reply_message_id is not None:
                remembered_reply_ids.append(reply_message_id)
                reply_index.remember_many(
                    chat_id, message.message_id, tuple(remembered_reply_ids)
                )
            _log_emit(chat_type, message, reply_message_id, mode, edited=False)
    if remaining_reply_ids:
        failed_delete_ids = await _delete_reply_chunks(
            context,
            message=message,
            chat_id=chat_id,
            reply_message_ids=tuple(remaining_reply_ids),
        )
        remembered_reply_ids.extend(failed_delete_ids)
    if remembered_reply_ids:
        reply_index.remember_many(
            chat_id, message.message_id, tuple(remembered_reply_ids)
        )
    else:
        reply_index.forget(chat_id, message.message_id)


async def _delete_reply_chunks(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    message,
    chat_id: int,
    reply_message_ids: tuple[int, ...],
) -> tuple[int, ...]:
    failed_reply_message_ids = []
    for reply_message_id in reply_message_ids:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=reply_message_id,
            )
        except BadRequest as exc:
            if _delete_error_means_already_gone(exc):
                LOGGER.info(
                    "channel edit extra chunk already gone "
                    "incoming_message_id=%s reply_message_id=%s",
                    message.message_id,
                    reply_message_id,
                )
                continue
            failed_reply_message_ids.append(reply_message_id)
            LOGGER.info(
                "channel edit extra chunk delete skipped "
                "incoming_message_id=%s reply_message_id=%s",
                message.message_id,
                reply_message_id,
            )
        except TelegramError:
            failed_reply_message_ids.append(reply_message_id)
            LOGGER.info(
                "channel edit extra chunk delete skipped "
                "incoming_message_id=%s reply_message_id=%s",
                message.message_id,
                reply_message_id,
            )
    return tuple(failed_reply_message_ids)


def _delete_error_means_already_gone(exc: BadRequest) -> bool:
    text = str(exc).lower()
    return "not found" in text or "message to delete not found" in text


async def _edit_existing_reply(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    message,
    chat_type: str,
    existing_reply_id: int | None,
    text: str,
    chat_id: int | None,
    parse_mode: str | None,
    mode: str,
) -> bool:
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
            return True
        if parse_mode is not None:
            # HTML edit rejected -> let the caller retry as plain.
            raise
        LOGGER.info(
            "channel edit skipped: reply not updatable "
            "incoming_message_id=%s reply_message_id=%s",
            message.message_id,
            existing_reply_id,
        )
        return False
    _log_emit(chat_type, message, existing_reply_id, mode, edited=True)
    return True


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
