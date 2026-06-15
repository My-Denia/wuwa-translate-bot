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
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .lookup import TermService
from .sentence import SentenceTranslator
from .constants import PINNED_WUTHERINGDATA_COMMIT


SERVICE_KEY = "wuwaterm_service"
TRANSLATOR_KEY = "wuwaterm_translator"
CONFIG_KEY = "wuwaterm_config"
RATE_LIMITER_KEY = "wuwaterm_rate_limiter"
ADMIN_CACHE_KEY = "wuwaterm_admin_cache"
LOGGER = logging.getLogger(__name__)

# User-facing operational notices are bilingual: Chinese line first, then an
# English line (single "\n"), so non-Chinese readers in the group can read them.
# Each notice is a single source-of-truth constant. Owner-set env overrides
# (WUWATERM_GROUP_TR_REJECT_TEXT / WUWATERM_PRIVATE_TR_REJECT_TEXT) still win
# verbatim and are deliberately NOT auto-bilingual.
THROTTLE_NOTICE = (
    "本群消息过于频繁，请一分钟后再试。\n"
    "Rate limit reached for this chat. Try again in a minute."
)
DEFAULT_GROUP_TR_REJECT_TEXT = "仅群管理员可用 /tr\nOnly group admins can use /tr"
DEFAULT_PRIVATE_TR_REJECT_TEXT = (
    "此 bot 仅限群内由管理员使用\n"
    "This bot can only be used by admins inside a group."
)
LLM_INPUT_CHAR_LIMIT = 1000
SHORT_QUERY_RE = re.compile(r"^[^\s。！？!?，,；;：:\n]{1,32}$")
ADMIN_ALLOWED_STATUSES = frozenset({"creator", "administrator"})
ADMIN_STATUS_CACHE_TTL_SECONDS = 300.0
ADMIN_CACHE_PRUNE_THRESHOLD = 1024
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class BotConfig:
    rate_limit_per_minute: int = 10
    group_tr_reject_text: str = DEFAULT_GROUP_TR_REJECT_TEXT
    private_tr_reject_text: str = DEFAULT_PRIVATE_TR_REJECT_TEXT
    tr_reject_silent: bool = False
    owner_user_id: int | None = None
    channel_autotranslate: bool = True
    channel_min_cjk: int = 1
    channel_text_limit: int = 4096
    channel_caption_limit: int = 1024
    channel_max_age_seconds: int = 300

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            rate_limit_per_minute=int(os.getenv("WUWATERM_RATE_LIMIT_PER_MINUTE", "10")),
            group_tr_reject_text=os.getenv(
                "WUWATERM_GROUP_TR_REJECT_TEXT", DEFAULT_GROUP_TR_REJECT_TEXT
            ),
            private_tr_reject_text=os.getenv(
                "WUWATERM_PRIVATE_TR_REJECT_TEXT", DEFAULT_PRIVATE_TR_REJECT_TEXT
            ),
            tr_reject_silent=(
                os.getenv("WUWATERM_TR_REJECT_SILENT", "").strip().lower()
                in _TRUTHY_ENV_VALUES
            ),
            owner_user_id=_owner_user_id_from_env(),
            channel_autotranslate=(
                os.getenv("WUWATERM_CHANNEL_AUTOTRANSLATE", "").strip().lower()
                not in _FALSY_ENV_VALUES
            ),
            channel_min_cjk=int(os.getenv("WUWATERM_CHANNEL_MIN_CJK", "1")),
            channel_text_limit=int(os.getenv("WUWATERM_CHANNEL_TEXT_LIMIT", "4096")),
            channel_caption_limit=int(
                os.getenv("WUWATERM_CHANNEL_CAPTION_LIMIT", "1024")
            ),
            channel_max_age_seconds=int(
                os.getenv("WUWATERM_CHANNEL_MAX_AGE_SECONDS", "300")
            ),
        )


def _owner_user_id_from_env() -> int | None:
    raw = os.getenv("OWNER_USER_ID", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            # Never echo the raw value: the owner id is quasi-sensitive.
            LOGGER.warning(
                "OWNER_USER_ID is set but not a valid integer; "
                "private /tr will reject everyone"
            )
            return None
    LOGGER.warning(
        "OWNER_USER_ID is not configured; private /tr will reject everyone"
    )
    return None


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
    # Imported here because channel.py imports the shared bot_data keys and
    # the rate-limit helper from this module (circular at import time).
    from .channel import channel_post_handler

    config = config or BotConfig.from_env()
    app = ApplicationBuilder().token(token).build()
    app.bot_data[SERVICE_KEY] = TermService(db_path)
    app.bot_data[TRANSLATOR_KEY] = SentenceTranslator(db_path)
    app.bot_data[CONFIG_KEY] = config
    app.bot_data[RATE_LIMITER_KEY] = PerChatRateLimiter(config.rate_limit_per_minute)
    app.bot_data[ADMIN_CACHE_KEY] = AdminStatusCache()
    app.add_handler(CommandHandler(["tr", "term"], term_command))
    app.add_handler(CommandHandler(["sentence", "sent"], sentence_command))
    app.add_handler(
        MessageHandler(
            filters.IS_AUTOMATIC_FORWARD & filters.SenderChat.CHANNEL,
            channel_post_handler,
        )
    )
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
    if not await _passes_authorization(update, context):
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
    if not await _passes_authorization(update, context):
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


async def _passes_authorization(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """One shared authorization wrapper for every translate command."""
    if await _is_authorized_sender(update, context):
        return True
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    if not config.tr_reject_silent:
        reject_text = (
            config.group_tr_reject_text
            if is_group_chat(update)
            else config.private_tr_reject_text
        )
        await reply_to_user(update, reject_text)
    return False


async def _is_authorized_sender(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    if is_group_chat(update):
        return await _is_authorized_group_sender(update, context)
    chat = update.effective_chat
    if chat is None or chat.type != "private":
        # Channels and any other chat type are outside the service surface.
        return False
    user = update.effective_user
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    return (
        user is not None
        and config.owner_user_id is not None
        and user.id == config.owner_user_id
    )


async def _is_authorized_group_sender(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
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
        # Fail closed without caching; ids stay out of logs (quasi-sensitive).
        LOGGER.warning("get_chat_member failed error=%r", exc)
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
