"""Passive linked-channel auto-translation (HTML-preserving)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from .channel_reply_index import ChannelReplyIndex, OriginalPostClaim
from .channel_runtime import ChannelRuntime
from .runtime_keys import (
    CHANNEL_REPLY_INDEX_KEY,
    CHANNEL_RUNTIME_KEY,
    CHAT_SETTINGS_KEY,
    CONFIG_KEY,
    SERVICE_KEY,
    TRANSLATOR_KEY,
)
from .lookup import TermService
from .logging_utils import redact_id, safe_error_type, safe_text_len
from .normalize import count_cjk, count_latin
from .sentence import (
    LLMTranslationError,
    TRANSLATION_UNAVAILABLE_NOTICE,
    SentenceTranslator,
    _llm_configured,
)
from .settings import ChatSettings
from .telegram_html import strip_telegram_html, validate_telegram_html
from .telegram_text import (
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    split_telegram_text,
    telegram_text_units,
)
from .translation_policy import LLM_INPUT_CHAR_LIMIT

if TYPE_CHECKING:
    from .bot import BotConfig


LOGGER = logging.getLogger(__name__)
LONG_OUTPUT_MODE_SUFFIX = "plain-split"


def _channel_runtime(
    context: ContextTypes.DEFAULT_TYPE, config: "BotConfig"
) -> ChannelRuntime:
    runtime = context.application.bot_data.get(CHANNEL_RUNTIME_KEY)
    if isinstance(runtime, ChannelRuntime):
        return runtime
    runtime = ChannelRuntime(
        max_active=config.llm_max_concurrency,
        max_pending=config.channel_max_pending,
        llm_calls_per_minute=config.channel_llm_calls_per_minute,
    )
    context.application.bot_data[CHANNEL_RUNTIME_KEY] = runtime
    return runtime


def _channel_event(
    runtime: ChannelRuntime,
    *,
    stage: str,
    reason: str,
    message=None,
    chat_id: int | None = None,
    is_edit: bool = False,
    direction: str = "unknown",
    text_len: int = 0,
    mode: str = "none",
) -> None:
    runtime.record(stage, reason)
    snapshot = runtime.snapshot()
    log = (
        LOGGER.warning
        if stage in {"llm", "delivery"} and reason not in {"started", "success"}
        else LOGGER.info
    )
    log(
        "channel_translation stage=%s reason=%s chat=%s incoming_message=%s "
        "edit=%s direction=%s text_len=%s mode=%s active=%s pending=%s high_water=%s",
        stage,
        reason,
        redact_id(chat_id),
        redact_id(getattr(message, "message_id", None)),
        is_edit,
        direction,
        text_len,
        mode,
        snapshot.active,
        snapshot.pending,
        snapshot.high_water,
    )


def _message_is_fresh(message, max_age_seconds: int) -> bool:
    message_date = getattr(message, "date", None)
    if message_date is None:
        return True
    return (
        datetime.now(timezone.utc) - message_date
    ).total_seconds() <= max_age_seconds


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Translate automatic forwards from the linked channel, in-thread.

    Every early exit is silent: this path never posts notices under
    channel posts (a notice comment under every post would be spam).

    An edited post updates the bot's existing reply for that post in place
    (tracked per (chat, post) in memory). If a fresh edit has no tracked reply,
    handle it as the first translatable version of that post; Telegram can
    deliver media posts before their caption/text is available.
    """
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    runtime = _channel_runtime(context, config)
    message = update.effective_message
    if message is None:
        _channel_event(runtime, stage="skipped", reason="no_message")
        return
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    chat_type = chat.type if chat else "unknown"
    is_edit = getattr(message, "edit_date", None) is not None
    initial_text = message.text or message.caption or ""
    _channel_event(
        runtime,
        stage="received",
        reason="update",
        message=message,
        chat_id=chat_id,
        is_edit=is_edit,
        text_len=safe_text_len(initial_text),
    )
    if not config.channel_autotranslate:
        _channel_event(
            runtime,
            stage="skipped",
            reason="disabled",
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(initial_text),
        )
        return
    # Freshness gate: Telegram replays updates (restart backlog, admin
    # promotion, queue delivery). Linked-channel content is trusted, so the
    # default window is deliberately broad and can accept a bounded restart or
    # admin-promotion backlog; the gate only blocks posts outside the configured
    # channel replay horizon.
    message_date = getattr(message, "date", None)
    if not _message_is_fresh(message, config.channel_max_age_seconds):
        _channel_event(
            runtime,
            stage="skipped",
            reason="stale",
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(initial_text),
        )
        return

    # Authorization gate (fail-closed): only translate for groups on the
    # allowlist. Slash commands are gated in _is_authorized_group_sender; this
    # mirrors it for the auto-forward path so an unauthorized or revoked group
    # the bot has not yet left (e.g. a leave_chat that failed) cannot keep
    # getting its linked-channel posts translated and burn LLM budget.
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    if chat_id is None or not settings.is_allowed(chat_id):
        _channel_event(
            runtime,
            stage="skipped",
            reason="not_authorized",
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(initial_text),
        )
        return

    # Edit dedup: an edited post (edit_date set) updates the reply already
    # sent for it. If this process already observed the post without replying,
    # a fresh edit can be the first translatable version; this covers Telegram
    # media forwards whose caption/text arrives only on the edited update.
    reply_index: ChannelReplyIndex = context.application.bot_data[
        CHANNEL_REPLY_INDEX_KEY
    ]
    resume_observed = False
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
            if message_date is None:
                _channel_event(
                    runtime,
                    stage="skipped",
                    reason="edit_untracked_no_date",
                    message=message,
                    chat_id=chat_id,
                    is_edit=True,
                    text_len=safe_text_len(initial_text),
                )
                return
            if chat_id is None or not reply_index.was_observed_without_reply(
                chat_id, message.message_id
            ):
                _channel_event(
                    runtime,
                    stage="skipped",
                    reason="edit_untracked",
                    message=message,
                    chat_id=chat_id,
                    is_edit=True,
                    text_len=safe_text_len(initial_text),
                )
                return
            # message.date is the original post date, not edit_date, so the
            # freshness gate above still blocks old replayed posts. This accepts
            # a fresh edit only when this process saw the post without replying;
            # after a restart, the sentinel is gone and the edit stays skipped.
            LOGGER.info(
                "channel autotranslate treating fresh edit as new post "
                "incoming_message=%s",
                redact_id(message.message_id),
            )
            is_edit = False
            resume_observed = True
    edit_work_token: int | None = None
    if is_edit and chat_id is not None:
        update_id = getattr(update, "update_id", None)
        edit_work_token = reply_index.begin_edit(
            chat_id,
            message.message_id,
            update_id if isinstance(update_id, int) else None,
        )
    original_claim: OriginalPostClaim | None = None
    if not is_edit and chat_id is not None:
        claim = reply_index.claim_original(
            chat_id,
            message.message_id,
            resume_observed=resume_observed,
        )
        if claim.role == "done":
            _channel_event(
                runtime,
                stage="skipped",
                reason="duplicate",
                message=message,
                chat_id=chat_id,
                text_len=safe_text_len(initial_text),
            )
            return
        if claim.role == "waiter":
            await reply_index.wait_for_original(claim)
            _channel_event(
                runtime,
                stage="skipped",
                reason="duplicate_in_flight",
                message=message,
                chat_id=chat_id,
                text_len=safe_text_len(initial_text),
            )
            return
        original_claim = claim
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
            _channel_event(
                runtime,
                stage="skipped",
                reason="no_text",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
            )
            return
        if not html_text:
            _channel_event(
                runtime,
                stage="skipped",
                reason="no_html",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                text_len=safe_text_len(plain),
            )
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
            _channel_event(
                runtime,
                stage="skipped",
                reason="language_threshold",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                text_len=safe_text_len(plain),
            )
            return
        direction = "to_zh" if to_chinese else "to_en"
        if len(plain) > length_limit:
            _channel_event(
                runtime,
                stage="skipped",
                reason="too_long",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
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
            _channel_event(
                runtime,
                stage="skipped",
                reason="llm_not_configured",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
            )
            return
        if official:
            # Dictionary-first invariant: official text byte-for-byte,
            # plain, trumps formatting. Zero LLM.
            if not _passes_channel_delivery_gate(context, chat_id):
                _channel_event(
                    runtime,
                    stage="skipped",
                    reason="authorization_changed",
                    message=message,
                    chat_id=chat_id,
                    is_edit=is_edit,
                    direction=direction,
                    text_len=safe_text_len(plain),
                )
                return
            _channel_event(
                runtime,
                stage="dictionary",
                reason="exact_hit",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
                mode="dictionary",
            )
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

        required_calls = (
            1
            if len(plain) <= LLM_INPUT_CHAR_LIMIT
            else len(split_telegram_text(plain, limit=LLM_INPUT_CHAR_LIMIT))
        )
        admission, rejection_reason = runtime.reserve(required_calls)
        if admission is None:
            _channel_event(
                runtime,
                stage="skipped",
                reason=rejection_reason or "admission_rejected",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
            )
            return
        translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
        async with admission:
            if not _message_is_fresh(message, config.channel_max_age_seconds):
                _channel_event(
                    runtime,
                    stage="skipped",
                    reason="stale_after_wait",
                    message=message,
                    chat_id=chat_id,
                    is_edit=is_edit,
                    direction=direction,
                    text_len=safe_text_len(plain),
                )
                return
            if not _passes_channel_delivery_gate(context, chat_id):
                _channel_event(
                    runtime,
                    stage="skipped",
                    reason="authorization_changed_after_wait",
                    message=message,
                    chat_id=chat_id,
                    is_edit=is_edit,
                    direction=direction,
                    text_len=safe_text_len(plain),
                )
                return
            started_calls = 0

            def mark_channel_llm_call_started() -> None:
                """Recheck volatile policy after the shared LLM-slot wait."""

                nonlocal started_calls

                if not _message_is_fresh(
                    message, config.channel_max_age_seconds
                ):
                    raise LLMTranslationError(
                        TRANSLATION_UNAVAILABLE_NOTICE,
                        reason="stale_before_llm",
                    )
                if not _passes_channel_delivery_gate(context, chat_id):
                    raise LLMTranslationError(
                        TRANSLATION_UNAVAILABLE_NOTICE,
                        reason="authorization_changed_before_llm",
                    )
                admission.mark_call_started()
                started_calls += 1
                _channel_event(
                    runtime,
                    stage="llm",
                    reason="started",
                    message=message,
                    chat_id=chat_id,
                    is_edit=is_edit,
                    direction=direction,
                    text_len=safe_text_len(plain),
                    mode=f"call:{started_calls}/{required_calls}",
                )

            try:
                (
                    translated,
                    translated_parse_mode,
                    translated_mode,
                ) = await _translate_channel_input(
                    translator,
                    plain,
                    html_text,
                    to_chinese=to_chinese,
                    before_llm_call=mark_channel_llm_call_started,
                )
            except LLMTranslationError as exc:
                reason = getattr(exc, "reason", "translation_unavailable")
                _channel_event(
                    runtime,
                    stage=(
                        "skipped"
                        if reason
                        in {
                            "stale_before_llm",
                            "authorization_changed_before_llm",
                        }
                        else "llm"
                    ),
                    reason=reason,
                    message=message,
                    chat_id=chat_id,
                    is_edit=is_edit,
                    direction=direction,
                    text_len=safe_text_len(plain),
                )
                return
            _channel_event(
                runtime,
                stage="llm",
                reason="success",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
                mode=translated_mode,
            )

        if translated_parse_mode == "HTML" and not validate_telegram_html(translated):
            _channel_event(
                runtime,
                stage="llm",
                reason="invalid_html",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
            )
            return
        if translated_parse_mode == "HTML":
            plain_translated = strip_telegram_html(translated)
            if telegram_text_units(plain_translated) > TELEGRAM_TEXT_MESSAGE_LIMIT:
                if not _passes_channel_delivery_gate(context, chat_id):
                    _channel_event(
                        runtime,
                        stage="skipped",
                        reason="authorization_changed_before_delivery",
                        message=message,
                        chat_id=chat_id,
                        is_edit=is_edit,
                        direction=direction,
                        text_len=safe_text_len(plain),
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
                    mode=translated_mode,
                )
                return
            try:
                if not _passes_channel_delivery_gate(context, chat_id):
                    _channel_event(
                        runtime,
                        stage="skipped",
                        reason="authorization_changed_before_delivery",
                        message=message,
                        chat_id=chat_id,
                        is_edit=is_edit,
                        direction=direction,
                        text_len=safe_text_len(plain),
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
                    mode=translated_mode,
                )
                return
            except BadRequest:
                # Safety net: never let formatting fail the reply.
                if not _passes_channel_delivery_gate(context, chat_id):
                    _channel_event(
                        runtime,
                        stage="skipped",
                        reason="authorization_changed_before_delivery",
                        message=message,
                        chat_id=chat_id,
                        is_edit=is_edit,
                        direction=direction,
                        text_len=safe_text_len(plain),
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
            _channel_event(
                runtime,
                stage="skipped",
                reason="authorization_changed_before_delivery",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                direction=direction,
                text_len=safe_text_len(plain),
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
            text=strip_telegram_html(translated)
            if translated_parse_mode == "HTML"
            else translated,
            parse_mode=None,
            mode="plain" if translated_parse_mode == "HTML" else translated_mode,
        )
    finally:
        if original_claim is not None and chat_id is not None:
            try:
                if reply_index.get(chat_id, message.message_id) is None:
                    reply_index.remember_observed_without_reply(
                        chat_id, message.message_id
                    )
            finally:
                reply_index.finish_original(original_claim)


def _passes_channel_delivery_gate(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int | None
) -> bool:
    if chat_id is None:
        return False
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    return settings.is_allowed(chat_id)


def _passes_final_delivery_gate(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    message,
    chat_id: int | None,
    is_edit: bool,
    text: str,
    mode: str,
) -> bool:
    """Recheck volatile policy immediately before a Telegram API call."""

    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    runtime = _channel_runtime(context, config)
    if not _message_is_fresh(message, config.channel_max_age_seconds):
        _channel_event(
            runtime,
            stage="skipped",
            reason="stale_before_delivery",
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(text),
            mode=mode,
        )
        return False
    if not _passes_channel_delivery_gate(context, chat_id):
        _channel_event(
            runtime,
            stage="skipped",
            reason="authorization_changed_before_delivery",
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(text),
            mode=mode,
        )
        return False
    return True


async def _translate_channel_input(
    translator: SentenceTranslator,
    plain: str,
    html_text: str,
    *,
    to_chinese: bool,
    before_llm_call,
) -> tuple[str, str | None, str]:
    if len(plain) <= LLM_INPUT_CHAR_LIMIT:
        return (
            await translator.translate_html_async(
                html_text,
                to_chinese=to_chinese,
                before_llm_call=before_llm_call,
            ),
            "HTML",
            "HTML",
        )
    translated_chunks: list[str] = []
    for chunk in split_telegram_text(plain, limit=LLM_INPUT_CHAR_LIMIT):
        translated = await translator.translate_async(
            chunk,
            to_chinese=to_chinese,
            before_llm_call=before_llm_call,
            propagate_errors=True,
        )
        translated_chunks.append(translated)
    return "\n".join(translated_chunks), None, "plain-input-split"


async def _emit(
    context: ContextTypes.DEFAULT_TYPE,
    **kwargs,
) -> None:
    message = kwargs["message"]
    chat_id = kwargs.get("chat_id")
    is_edit = bool(kwargs.get("is_edit"))
    text = kwargs["text"]
    mode = kwargs["mode"]
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    runtime = _channel_runtime(context, config)
    if not _passes_final_delivery_gate(
        context,
        message=message,
        chat_id=chat_id,
        is_edit=is_edit,
        text=text,
        mode=mode,
    ):
        return
    try:
        outcome = await _emit_unchecked(context, **kwargs)
    except BadRequest as exc:
        _channel_event(
            runtime,
            stage="delivery",
            reason=safe_error_type(exc),
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(text),
            mode=mode,
        )
        if kwargs.get("parse_mode") == "HTML":
            raise
    except TelegramError as exc:
        _channel_event(
            runtime,
            stage="delivery",
            reason=safe_error_type(exc),
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(text),
            mode=mode,
        )
    else:
        if outcome == "gated":
            return
        if outcome == "stale_edit":
            _channel_event(
                runtime,
                stage="skipped",
                reason="stale_edit_before_delivery",
                message=message,
                chat_id=chat_id,
                is_edit=is_edit,
                text_len=safe_text_len(text),
                mode=mode,
            )
            return
        _channel_event(
            runtime,
            stage="delivery",
            reason=outcome,
            message=message,
            chat_id=chat_id,
            is_edit=is_edit,
            text_len=safe_text_len(text),
            mode=mode,
        )


async def _emit_unchecked(
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
) -> str:
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
                        "incoming_message=%s reply_message=%s",
                        redact_id(message.message_id),
                        redact_id(existing_reply_id),
                    )
                    return "stale_edit"
                if not _passes_final_delivery_gate(
                    context,
                    message=message,
                    chat_id=chat_id,
                    is_edit=True,
                    text=text,
                    mode=delivery_mode,
                ):
                    return "gated"
                existing_reply_ids = reply_index.get_many(chat_id, message.message_id)
                if not existing_reply_ids and existing_reply_id is not None:
                    existing_reply_ids = (existing_reply_id,)
                return await _edit_reply_chunks(
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
        existing_reply_ids = (
            reply_index.get_many(chat_id, message.message_id)
            if chat_id is not None
            else ()
        )
        if not existing_reply_ids and existing_reply_id is not None:
            existing_reply_ids = (existing_reply_id,)
        if not _passes_final_delivery_gate(
            context,
            message=message,
            chat_id=chat_id,
            is_edit=True,
            text=text,
            mode=delivery_mode,
        ):
            return "gated"
        return await _edit_reply_chunks(
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

    reply_message_ids = []
    for chunk in chunks:
        if not _passes_final_delivery_gate(
            context,
            message=message,
            chat_id=chat_id,
            is_edit=False,
            text=chunk,
            mode=delivery_mode,
        ):
            return "gated"
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
        _log_emit(
            chat_type,
            message,
            reply_message_id,
            delivery_mode,
            edited=False,
            text=chunk,
        )
    return "success"


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
) -> str:
    if chat_id is None or not existing_reply_ids:
        return "edit_target_unavailable"
    remembered_reply_ids: list[int] = []
    remaining_reply_ids = list(existing_reply_ids)
    for chunk in chunks:
        while remaining_reply_ids:
            if not _passes_final_delivery_gate(
                context,
                message=message,
                chat_id=chat_id,
                is_edit=True,
                text=chunk,
                mode=mode,
            ):
                return "gated"
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
                return "edit_target_unavailable"
        else:
            if not _passes_final_delivery_gate(
                context,
                message=message,
                chat_id=chat_id,
                is_edit=True,
                text=chunk,
                mode=mode,
            ):
                return "gated"
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
            _log_emit(
                chat_type,
                message,
                reply_message_id,
                mode,
                edited=False,
                text=chunk,
            )
    if remaining_reply_ids:
        failed_delete_ids = await _delete_reply_chunks(
            context,
            message=message,
            chat_id=chat_id,
            reply_message_ids=tuple(remaining_reply_ids),
        )
        remembered_reply_ids.extend(failed_delete_ids)
    else:
        failed_delete_ids = ()
    if remembered_reply_ids:
        reply_index.remember_many(
            chat_id, message.message_id, tuple(remembered_reply_ids)
        )
    else:
        reply_index.forget(chat_id, message.message_id)
    if failed_delete_ids:
        return "stale_chunks_retained"
    return "success"


async def _delete_reply_chunks(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    message,
    chat_id: int,
    reply_message_ids: tuple[int, ...],
) -> tuple[int, ...]:
    failed_reply_message_ids = []
    for index, reply_message_id in enumerate(reply_message_ids):
        if not _passes_final_delivery_gate(
            context,
            message=message,
            chat_id=chat_id,
            is_edit=True,
            text="",
            mode="edit-delete",
        ):
            failed_reply_message_ids.extend(reply_message_ids[index:])
            break
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=reply_message_id,
            )
        except BadRequest as exc:
            if _delete_error_means_already_gone(exc):
                LOGGER.info(
                    "channel edit extra chunk already gone "
                    "incoming_message=%s reply_message=%s",
                    redact_id(message.message_id),
                    redact_id(reply_message_id),
                )
                continue
            failed_reply_message_ids.append(reply_message_id)
            LOGGER.info(
                "channel edit extra chunk delete skipped "
                "incoming_message=%s reply_message=%s",
                redact_id(message.message_id),
                redact_id(reply_message_id),
            )
        except TelegramError:
            failed_reply_message_ids.append(reply_message_id)
            LOGGER.info(
                "channel edit extra chunk delete skipped "
                "incoming_message=%s reply_message=%s",
                redact_id(message.message_id),
                redact_id(reply_message_id),
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
                "incoming_message=%s reply_message=%s",
                redact_id(message.message_id),
                redact_id(existing_reply_id),
            )
            return True
        if parse_mode is not None:
            # HTML edit rejected -> let the caller retry as plain.
            raise
        LOGGER.info(
            "channel edit skipped: reply not updatable "
            "incoming_message=%s reply_message=%s",
            redact_id(message.message_id),
            redact_id(existing_reply_id),
        )
        return False
    _log_emit(chat_type, message, existing_reply_id, mode, edited=True, text=text)
    return True


def _log_emit(
    chat_type: str,
    message,
    reply_message_id,
    mode: str,
    *,
    edited: bool,
    text: str,
) -> None:
    LOGGER.info(
        "bot_reply chat_type=%s incoming_message=%s reply_message=%s "
        "reply_to_message=%s mode=%s edited=%s text_len=%s",
        chat_type,
        redact_id(message.message_id),
        redact_id(reply_message_id),
        redact_id(message.message_id),
        mode,
        edited,
        safe_text_len(text),
    )
