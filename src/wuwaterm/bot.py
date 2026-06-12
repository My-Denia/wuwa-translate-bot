"""Telegram bot handlers."""

from __future__ import annotations

import os
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from .lookup import TermService
from .sentence import SentenceTranslator
from .constants import PINNED_WUTHERINGDATA_COMMIT


SERVICE_KEY = "wuwaterm_service"
TRANSLATOR_KEY = "wuwaterm_translator"
CONFIG_KEY = "wuwaterm_config"
RATE_LIMITER_KEY = "wuwaterm_rate_limiter"
ADMIN_CACHE_KEY = "wuwaterm_admin_cache"
LOGGER = logging.getLogger(__name__)

THROTTLE_NOTICE = "Rate limit reached for this chat. Try again in a minute."
DEFAULT_GROUP_TR_REJECT_TEXT = "仅群管理员可用 /tr"
LLM_INPUT_CHAR_LIMIT = 1000
SHORT_QUERY_RE = re.compile(r"^[^\s。！？!?，,；;：:\n]{1,32}$")
ADMIN_ALLOWED_STATUSES = frozenset({"creator", "administrator"})
ADMIN_STATUS_CACHE_TTL_SECONDS = 300.0
ADMIN_CACHE_PRUNE_THRESHOLD = 1024
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class BotConfig:
    rate_limit_per_minute: int = 10
    group_tr_reject_text: str = DEFAULT_GROUP_TR_REJECT_TEXT
    group_tr_reject_silent: bool = False

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            rate_limit_per_minute=int(os.getenv("WUWATERM_RATE_LIMIT_PER_MINUTE", "10")),
            group_tr_reject_text=os.getenv(
                "WUWATERM_GROUP_TR_REJECT_TEXT", DEFAULT_GROUP_TR_REJECT_TEXT
            ),
            group_tr_reject_silent=(
                os.getenv("WUWATERM_GROUP_TR_REJECT_SILENT", "").strip().lower()
                in _TRUTHY_ENV_VALUES
            ),
        )


class PerChatRateLimiter:
    def __init__(self, limit: int = 10, window_seconds: float = 60.0):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[int, Deque[float]] = defaultdict(deque)

    def allow(self, chat_id: int, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        events = self._events[chat_id]
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True


class AdminStatusCache:
    """Short-TTL cache of getChatMember admin verdicts per (chat, user)."""

    def __init__(self, ttl_seconds: float = ADMIN_STATUS_CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[tuple[int, int], tuple[float, bool]] = {}

    def get(self, chat_id: int, user_id: int, now: float | None = None) -> bool | None:
        now = time.monotonic() if now is None else now
        key = (chat_id, user_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, verdict = entry
        if now >= expires_at:
            del self._entries[key]
            return None
        return verdict

    def put(self, chat_id: int, user_id: int, verdict: bool, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if len(self._entries) >= ADMIN_CACHE_PRUNE_THRESHOLD:
            self._entries = {
                key: entry for key, entry in self._entries.items() if entry[0] > now
            }
        self._entries[(chat_id, user_id)] = (now + self.ttl_seconds, verdict)


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in {"group", "supergroup"})


def chat_id_for(update: Update) -> int | None:
    chat = update.effective_chat
    return int(chat.id) if chat else None


async def reply_to_user(update: Update, text: str) -> None:
    message = update.effective_message
    if not message:
        return
    chat = update.effective_chat
    chat_type = chat.type if chat else "unknown"
    if is_group_chat(update):
        sent_message = await message.reply_text(text, reply_to_message_id=message.message_id)
        reply_to_message_id = message.message_id
    else:
        sent_message = await message.reply_text(text)
        reply_to_message_id = None
    LOGGER.info(
        "bot_reply chat_type=%s incoming_message_id=%s reply_message_id=%s "
        "reply_to_message_id=%s text=%r",
        chat_type,
        message.message_id,
        getattr(sent_message, "message_id", None),
        reply_to_message_id,
        text,
    )


def create_application(
    token: str,
    db_path: str | Path,
    config: BotConfig | None = None,
) -> Application:
    config = config or BotConfig.from_env()
    app = ApplicationBuilder().token(token).build()
    app.bot_data[SERVICE_KEY] = TermService(db_path)
    app.bot_data[TRANSLATOR_KEY] = SentenceTranslator(db_path)
    app.bot_data[CONFIG_KEY] = config
    app.bot_data[RATE_LIMITER_KEY] = PerChatRateLimiter(config.rate_limit_per_minute)
    app.bot_data[ADMIN_CACHE_KEY] = AdminStatusCache()
    app.add_handler(CommandHandler(["tr", "term"], term_command))
    app.add_handler(CommandHandler(["sentence", "sent"], sentence_command))
    return app


def run_bot(db_path: str | Path, token: str | None = None) -> None:
    logging.basicConfig(
        level=os.getenv("WUWATERM_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for noisy_logger in ("httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    app = create_application(token, db_path)
    app.run_polling()


async def term_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    message = update.effective_message
    if not message:
        return
    if not _consume_rate_limit(update, context):
        await reply_to_user(update, THROTTLE_NOTICE)
        return
    if not await _is_authorized_group_sender(update, context):
        config: BotConfig = context.application.bot_data[CONFIG_KEY]
        if not config.group_tr_reject_silent:
            await reply_to_user(update, config.group_tr_reject_text)
        return
    if not query:
        await reply_to_user(update, "Usage: /tr <Chinese text>")
        return
    service: TermService = context.application.bot_data[SERVICE_KEY]
    translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
    await reply_to_user(update, translate_query(service, translator, query))


async def sentence_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    message = update.effective_message
    if not message:
        return
    if not _consume_rate_limit(update, context):
        await reply_to_user(update, THROTTLE_NOTICE)
        return
    if not text:
        await reply_to_user(update, "Usage: /sentence <Chinese sentence>")
        return
    service: TermService = context.application.bot_data[SERVICE_KEY]
    translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
    await reply_to_user(update, translate_query(service, translator, text))


def format_term_reply(service: TermService, query: str) -> str:
    translator = SentenceTranslator(service.db_path)
    return translate_query(service, translator, query)


def translate_query(service: TermService, translator: SentenceTranslator, query: str) -> str:
    prepared = translator.prepare_text(query)
    if not prepared:
        return "Nothing to translate after removing metadata."
    result = service.lookup(prepared, limit=5)
    if result.exact:
        official = service.term_text(prepared)
        if official:
            return official
    if _is_ascii_fuzzy_query(prepared) and result.best and result.best.score >= 80.0:
        return result.best.entry.en
    if len(prepared) > LLM_INPUT_CHAR_LIMIT:
        return f"Input is too long for translation ({LLM_INPUT_CHAR_LIMIT} character limit)."
    translated = translator.translate(prepared)
    if _is_short_query(prepared) and not _has_locked_terms(translator, prepared):
        translated = (
            f"{translated}\n\n"
            f"(not in official data (pinned commit {PINNED_WUTHERINGDATA_COMMIT}))"
        )
    return translated


def _is_ascii_fuzzy_query(text: str) -> bool:
    return bool(text) and text.isascii() and SHORT_QUERY_RE.match(text) is not None


def _is_short_query(text: str) -> bool:
    return SHORT_QUERY_RE.match(text) is not None


def _has_locked_terms(translator: SentenceTranslator, text: str) -> bool:
    return bool(translator.lock_terms(text).locks)


async def _is_authorized_group_sender(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if not is_group_chat(update):
        return True
    chat = update.effective_chat
    message = update.effective_message
    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat is not None and chat is not None and sender_chat.id == chat.id:
        # Anonymous group admins post as the group itself.
        return True
    user = update.effective_user
    if chat is None or user is None:
        return False
    cache: AdminStatusCache = context.application.bot_data[ADMIN_CACHE_KEY]
    cached = cache.get(chat.id, user.id)
    if cached is not None:
        return cached
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
    except TelegramError as exc:
        # Fail closed, but do not cache transient API failures.
        LOGGER.warning(
            "get_chat_member failed chat_id=%s user_id=%s error=%r",
            chat.id,
            user.id,
            exc,
        )
        return False
    verdict = getattr(member, "status", None) in ADMIN_ALLOWED_STATUSES
    cache.put(chat.id, user.id, verdict)
    return verdict


def _consume_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = chat_id_for(update)
    if chat_id is None:
        return True
    limiter: PerChatRateLimiter = context.application.bot_data[RATE_LIMITER_KEY]
    return limiter.allow(chat_id)
