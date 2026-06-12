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
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from .lookup import TermService
from .sentence import SentenceTranslator
from .constants import PINNED_WUTHERINGDATA_COMMIT


SERVICE_KEY = "wuwaterm_service"
TRANSLATOR_KEY = "wuwaterm_translator"
CONFIG_KEY = "wuwaterm_config"
RATE_LIMITER_KEY = "wuwaterm_rate_limiter"
LOGGER = logging.getLogger(__name__)

THROTTLE_NOTICE = "Rate limit reached for this chat. Try again in a minute."
LLM_INPUT_CHAR_LIMIT = 1000
SHORT_QUERY_RE = re.compile(r"^[^\s。！？!?，,；;：:\n]{1,32}$")


@dataclass(frozen=True)
class BotConfig:
    rate_limit_per_minute: int = 10

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            rate_limit_per_minute=int(os.getenv("WUWATERM_RATE_LIMIT_PER_MINUTE", "10")),
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


def _consume_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = chat_id_for(update)
    if chat_id is None:
        return True
    limiter: PerChatRateLimiter = context.application.bot_data[RATE_LIMITER_KEY]
    return limiter.allow(chat_id)
