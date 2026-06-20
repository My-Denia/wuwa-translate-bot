"""Telegram bot handlers."""

from __future__ import annotations

import os
import asyncio
import json
import logging
import re
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque

from telegram import ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .lookup import TermService
from .normalize import has_cjk
from .sentence import (
    DEFAULT_LLM_MAX_CONCURRENCY,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    SentenceTranslator,
    _llm_configured,
)
from .settings import ChatSettings
from .telegram_text import (
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    split_telegram_text,
)


SERVICE_KEY = "wuwaterm_service"
TRANSLATOR_KEY = "wuwaterm_translator"
CONFIG_KEY = "wuwaterm_config"
RATE_LIMITER_KEY = "wuwaterm_rate_limiter"
REJECT_LIMITER_KEY = "wuwaterm_reject_limiter"
ADMIN_CACHE_KEY = "wuwaterm_admin_cache"
CHANNEL_REPLY_INDEX_KEY = "wuwaterm_channel_reply_index"
CHAT_SETTINGS_KEY = "wuwaterm_chat_settings"
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
# Usage hints (bilingual; also point out the reply-to-translate shortcut).
TERM_USAGE_NOTICE = (
    "用法：/tr <中文或英文>（自动判向：中→英 / 英→中；或回复一条消息后发 /tr 直接翻译）\n"
    "Usage: /tr <Chinese or English> (direction auto-detected; or reply to a message, then send /tr)"
)
SENTENCE_USAGE_NOTICE = (
    "用法：/sentence <中文或英文句子>（自动判向：中→英 / 英→中；或回复一条消息后发 /sentence 直接翻译）\n"
    "Usage: /sentence <Chinese or English sentence> (direction auto-detected; or reply to a message, then send /sentence)"
)
# Appended to a short query that misses the dictionary. The data version
# (pinned commit) lives in /about, never in this user-facing line.
DICT_MISS_FLAG = (
    "(词典外,机器直译)\n"
    "(Not in official dictionary; machine-translated)"
)
# /public toggles whether non-admin members of a group can use the translate
# commands. Default is admin-only. The /public command itself is always
# admin-only (never bypassed by public mode), so a public group cannot have
# its switch flipped by a non-admin.
PUBLIC_USAGE_NOTICE = (
    "用法：/public on | off | status（仅群管理员）\n"
    "Usage: /public on | off | status (group admins only)"
)
PUBLIC_ENABLED_NOTICE = (
    "已对所有群成员开放翻译命令（/tr 等）\n"
    "Translate commands (/tr etc.) are now open to all members in this chat."
)
PUBLIC_DISABLED_NOTICE = (
    "翻译命令已恢复为仅群管理员可用\n"
    "Translate commands restricted to group admins again."
)
PUBLIC_STATUS_ON = (
    "当前状态：公开（所有群成员可用 /tr 等）\n"
    "Current state: public (all members may use /tr etc.)."
)
PUBLIC_STATUS_OFF = (
    "当前状态：仅群管理员可用\n"
    "Current state: admins-only."
)
PUBLIC_ONLY_GROUPS_NOTICE = (
    "/public 仅在群里有效\n"
    "/public only works in groups."
)
PUBLIC_REJECT_NOTICE = (
    "仅群管理员可用 /public\n"
    "Only group admins can use /public"
)
SETTINGS_SAVE_FAILED_NOTICE = (
    "设置保存失败，请稍后再试（状态未更改）。\n"
    "Could not save the setting, please try again later (state unchanged)."
)
# Group authorization gate: shown once before the bot leaves a chat it was
# added to that is not authorized.
UNAUTHORIZED_GROUP_NOTICE = (
    "本 bot 未获授权在此群使用，需由 bot 主人授权；现在自动退出本群。\n"
    "This bot is not authorized for this chat. It needs the owner's "
    "authorization to stay, so it is leaving now."
)
AUTHORIZE_USAGE = (
    "用法（仅 bot 主人）：群内发 /authorize 授权本群；私聊发 /authorize <chat_id> 按 id 授权；/authorize list 查看名单。\n"
    "Usage (owner only): /authorize in a group to allow it; /authorize <chat_id> "
    "in private to allow by id; /authorize list to view."
)
REVOKE_USAGE = (
    "用法（仅 bot 主人）：群内发 /revoke 撤销本群授权；私聊发 /revoke <chat_id> 按 id 撤销。\n"
    "Usage (owner only): /revoke in a group to remove it; /revoke <chat_id> in "
    "private to remove by id."
)
LLM_INPUT_CHAR_LIMIT = 2000
SHORT_QUERY_RE = re.compile(r"^[^\s。！？!?，,；;：:\n]{1,32}$")
ADMIN_ALLOWED_STATUSES = frozenset({"creator", "administrator"})
ADMIN_STATUS_CACHE_TTL_SECONDS = 300.0
ADMIN_CACHE_PRUNE_THRESHOLD = 1024
CHANNEL_REPLY_INDEX_PRUNE_THRESHOLD = 1024
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
    channel_min_latin: int = 2
    channel_text_limit: int = 4096
    channel_caption_limit: int = 1024
    channel_max_age_seconds: int = 300
    channel_reply_index_path: str | None = None
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_concurrency: int = DEFAULT_LLM_MAX_CONCURRENCY

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
            channel_min_latin=int(os.getenv("WUWATERM_CHANNEL_MIN_LATIN", "2")),
            channel_text_limit=int(os.getenv("WUWATERM_CHANNEL_TEXT_LIMIT", "4096")),
            channel_caption_limit=int(
                os.getenv("WUWATERM_CHANNEL_CAPTION_LIMIT", "1024")
            ),
            channel_max_age_seconds=int(
                os.getenv("WUWATERM_CHANNEL_MAX_AGE_SECONDS", "300")
            ),
            channel_reply_index_path=(
                os.getenv("WUWATERM_CHANNEL_REPLY_INDEX_PATH", "").strip() or None
            ),
            llm_timeout_seconds=float(
                os.getenv(
                    "WUWATERM_LLM_TIMEOUT_SECONDS",
                    str(DEFAULT_LLM_TIMEOUT_SECONDS),
                )
            ),
            llm_max_concurrency=max(
                1,
                int(
                    os.getenv(
                        "WUWATERM_LLM_MAX_CONCURRENCY",
                        str(DEFAULT_LLM_MAX_CONCURRENCY),
                    )
                ),
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


class ChannelReplyIndex:
    """Maps a forwarded channel post to the bot's translation reply IDs.

    When the linked channel edits a post, Telegram edits the auto-forwarded
    copy in the group in place (same message_id) and the listener fires
    again. This index lets that edit update existing reply chunks instead of
    adding untracked duplicates. Entries are bounded by age (the channel
    freshness window) and can be persisted; if an edit finds no entry, it is
    skipped, degrading to "no update", never to a duplicate reply.
    """

    def __init__(
        self,
        ttl_seconds: float = 300.0,
        storage_path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.ttl_seconds = ttl_seconds
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self._clock = clock
        self._entries: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {}
        self._in_flight: dict[tuple[int, int], asyncio.Event] = {}
        self._latest_edit_tokens: dict[tuple[int, int], int] = {}
        self._edit_delivery_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._next_edit_token = 0
        self._load_failures = 0
        self._last_load_ok: bool | None = None
        self._save_failures = 0
        self._last_save_ok: bool | None = None
        self._load()

    def remember(
        self,
        chat_id: int,
        message_id: int,
        reply_message_id: int,
        now: float | None = None,
    ) -> None:
        self.remember_many(chat_id, message_id, (reply_message_id,), now=now)

    def remember_many(
        self,
        chat_id: int,
        message_id: int,
        reply_message_ids: tuple[int, ...],
        now: float | None = None,
    ) -> None:
        if not reply_message_ids:
            return
        now = self._clock() if now is None else now
        if len(self._entries) >= CHANNEL_REPLY_INDEX_PRUNE_THRESHOLD:
            self._entries = {
                key: entry for key, entry in self._entries.items() if entry[0] > now
            }
            self._prune_edit_state()
        self._entries[(chat_id, message_id)] = (
            now + self.ttl_seconds,
            tuple(reply_message_ids),
        )
        self._save_best_effort()

    def get(
        self, chat_id: int, message_id: int, now: float | None = None
    ) -> int | None:
        now = self._clock() if now is None else now
        key = (chat_id, message_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, reply_message_ids = entry
        if now >= expires_at:
            self._forget_key(key, persist=False)
            return None
        return reply_message_ids[0] if reply_message_ids else None

    def get_many(
        self, chat_id: int, message_id: int, now: float | None = None
    ) -> tuple[int, ...]:
        now = self._clock() if now is None else now
        key = (chat_id, message_id)
        entry = self._entries.get(key)
        if entry is None:
            return ()
        expires_at, reply_message_ids = entry
        if now >= expires_at:
            self._forget_key(key, persist=False)
            return ()
        return reply_message_ids

    def forget(self, chat_id: int, message_id: int) -> None:
        self._forget_key((chat_id, message_id), persist=True)

    def entry_count(self, now: float | None = None) -> int:
        self.prune(now=now)
        return len(self._entries)

    def prune(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        before = len(self._entries)
        self._entries = {
            key: entry for key, entry in self._entries.items() if entry[0] > now
        }
        self._prune_edit_state()
        if len(self._entries) != before:
            self._save_best_effort()

    def _forget_key(self, key: tuple[int, int], *, persist: bool) -> None:
        self._entries.pop(key, None)
        self._latest_edit_tokens.pop(key, None)
        lock = self._edit_delivery_locks.get(key)
        if lock is not None and not lock.locked():
            self._edit_delivery_locks.pop(key, None)
        if persist:
            self._save_best_effort()

    def begin_edit(
        self, chat_id: int, message_id: int, update_id: int | None = None
    ) -> int:
        key = (chat_id, message_id)
        latest = self._latest_edit_tokens.get(key)
        if update_id is None:
            floor = latest if latest is not None else 0
            self._next_edit_token = max(self._next_edit_token, floor) + 1
            token = self._next_edit_token
        else:
            token = update_id
            self._next_edit_token = max(self._next_edit_token, token)
        if latest is None or token > latest:
            self._latest_edit_tokens[key] = token
        return token

    def is_latest_edit(self, chat_id: int, message_id: int, token: int) -> bool:
        return self._latest_edit_tokens.get((chat_id, message_id)) == token

    def edit_delivery_lock(self, chat_id: int, message_id: int) -> asyncio.Lock:
        return self._edit_delivery_locks.setdefault(
            (chat_id, message_id), asyncio.Lock()
        )

    def _prune_edit_state(self) -> None:
        live_keys = set(self._entries)
        self._latest_edit_tokens = {
            key: token
            for key, token in self._latest_edit_tokens.items()
            if key in live_keys
        }
        self._edit_delivery_locks = {
            key: lock
            for key, lock in self._edit_delivery_locks.items()
            if key in live_keys or lock.locked()
        }

    def mark_in_flight(self, chat_id: int, message_id: int) -> None:
        self._in_flight.setdefault((chat_id, message_id), asyncio.Event())

    async def wait_in_flight(self, chat_id: int, message_id: int) -> bool:
        event = self._in_flight.get((chat_id, message_id))
        if event is None:
            return False
        await event.wait()
        return True

    def finish_in_flight(self, chat_id: int, message_id: int) -> None:
        event = self._in_flight.pop((chat_id, message_id), None)
        if event is not None:
            event.set()

    def persistence_enabled(self) -> bool:
        return self.storage_path is not None

    def load_failure_count(self) -> int:
        return self._load_failures

    def last_load_succeeded(self) -> bool | None:
        return self._last_load_ok

    def save_failure_count(self) -> int:
        return self._save_failures

    def last_save_succeeded(self) -> bool | None:
        return self._last_save_ok

    def _record_load_failure(self) -> None:
        self._load_failures += 1
        self._last_load_ok = False
        LOGGER.warning("channel reply index unreadable, starting empty")

    def _load(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            with self.storage_path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._record_load_failure()
            return
        if not isinstance(payload, dict):
            self._record_load_failure()
            return
        rows = payload.get("entries")
        if not isinstance(rows, list):
            self._record_load_failure()
            return
        self._last_load_ok = True
        now = self._clock()
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                chat_id = int(row["chat_id"])
                message_id = int(row["message_id"])
                expires_at = float(row["expires_at"])
                reply_ids = tuple(int(item) for item in row["reply_message_ids"])
            except (KeyError, TypeError, ValueError):
                continue
            if expires_at <= now or not reply_ids:
                continue
            self._entries[(chat_id, message_id)] = (expires_at, reply_ids)

    def _save_best_effort(self) -> None:
        if self.storage_path is None:
            return
        try:
            self._save()
        except OSError:
            self._save_failures += 1
            self._last_save_ok = False
            LOGGER.warning("channel reply index save failed")
        else:
            self._last_save_ok = True

    def _save(self) -> None:
        assert self.storage_path is not None
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for (chat_id, message_id), (expires_at, reply_ids) in sorted(
            self._entries.items()
        ):
            rows.append(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "expires_at": expires_at,
                    "reply_message_ids": list(reply_ids),
                }
            )
        payload = {"version": 1, "entries": rows}
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.storage_path.name}.", dir=self.storage_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.storage_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in {"group", "supergroup"})


def chat_id_for(update: Update) -> int | None:
    chat = update.effective_chat
    return int(chat.id) if chat else None


def _replied_translatable_text(update: Update) -> str:
    """Content of the message a command replies to: text, or caption for media.

    Lets an authorized caller translate a message by replying to it with a bare
    command. Returns "" when there is no reply, or it carries nothing to
    translate (e.g. a sticker or an image without a caption).
    """
    message = update.effective_message
    if message is None:
        return ""
    replied = getattr(message, "reply_to_message", None)
    if replied is None:
        return ""
    content = getattr(replied, "text", None) or getattr(replied, "caption", None)
    return content.strip() if content else ""


async def reply_to_user(update: Update, text: str) -> None:
    message = update.effective_message
    if not message:
        return
    chat = update.effective_chat
    chat_type = chat.type if chat else "unknown"
    chunks = _telegram_text_chunks(text)
    first_reply_to_message_id = message.message_id if is_group_chat(update) else None
    for index, chunk in enumerate(chunks):
        reply_to_message_id = first_reply_to_message_id if index == 0 else None
        if reply_to_message_id is None:
            sent_message = await message.reply_text(chunk)
        else:
            sent_message = await message.reply_text(
                chunk, reply_to_message_id=reply_to_message_id
            )
        LOGGER.info(
            "bot_reply chat_type=%s incoming_message_id=%s reply_message_id=%s "
            "reply_to_message_id=%s chunk=%s/%s text=%r",
            chat_type,
            message.message_id,
            getattr(sent_message, "message_id", None),
            reply_to_message_id,
            index + 1,
            len(chunks),
            chunk,
        )


def _telegram_text_chunks(
    text: str, limit: int = TELEGRAM_TEXT_MESSAGE_LIMIT
) -> list[str]:
    """Split plain Telegram replies before Bot API rejects messages >4096 chars."""
    return split_telegram_text(text, limit=limit)


def create_application(
    token: str,
    db_path: str | Path,
    config: BotConfig | None = None,
    chat_settings: ChatSettings | None = None,
) -> Application:
    # Imported here because channel.py imports the shared bot_data keys and
    # the rate-limit helper from this module (circular at import time).
    from .channel import channel_post_handler

    config = config or BotConfig.from_env()
    if chat_settings is None:
        chat_settings = ChatSettings(_chat_settings_path_from_env(db_path))
    app = ApplicationBuilder().token(token).build()
    app.bot_data[SERVICE_KEY] = TermService(db_path)
    app.bot_data[TRANSLATOR_KEY] = SentenceTranslator(
        db_path,
        llm_timeout_seconds=config.llm_timeout_seconds,
        llm_max_concurrency=config.llm_max_concurrency,
    )
    app.bot_data[CONFIG_KEY] = config
    app.bot_data[RATE_LIMITER_KEY] = PerChatRateLimiter(config.rate_limit_per_minute)
    app.bot_data[REJECT_LIMITER_KEY] = PerChatRateLimiter(config.rate_limit_per_minute)
    app.bot_data[ADMIN_CACHE_KEY] = AdminStatusCache()
    app.bot_data[CHANNEL_REPLY_INDEX_KEY] = ChannelReplyIndex(
        ttl_seconds=config.channel_max_age_seconds,
        storage_path=_channel_reply_index_path(config, db_path),
    )
    app.bot_data[CHAT_SETTINGS_KEY] = chat_settings
    app.add_handler(CommandHandler(["tr", "term"], term_command, block=False))
    app.add_handler(CommandHandler(["sentence", "sent"], sentence_command, block=False))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("public", public_command))
    app.add_handler(CommandHandler("authorize", authorize_command))
    app.add_handler(CommandHandler("revoke", revoke_command))
    app.add_handler(
        MessageHandler(
            filters.IS_AUTOMATIC_FORWARD & filters.SenderChat.CHANNEL,
            channel_post_handler,
            block=False,
        )
    )
    app.add_handler(
        ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    return app


def _chat_settings_path_from_env(db_path: str | Path) -> Path:
    """Settings file lives alongside the DB by default so the container's
    bind-mounted data/ volume preserves it across image rebuilds."""
    explicit = os.getenv("WUWATERM_SETTINGS_PATH", "").strip()
    if explicit:
        return Path(explicit)
    return Path(db_path).resolve().parent / "chat_settings.json"


def _channel_reply_index_path(config: BotConfig, db_path: str | Path) -> Path:
    if config.channel_reply_index_path:
        return Path(config.channel_reply_index_path)
    return Path(db_path).resolve().parent / "channel_replies.json"


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
    message = update.effective_message
    if not message:
        return
    # Inline text wins; otherwise fall back to the replied-to message's content.
    query = " ".join(context.args).strip() or _replied_translatable_text(update)
    if not await _passes_authorization(update, context):
        return
    if not _consume_rate_limit(update, context):
        await reply_to_user(update, THROTTLE_NOTICE)
        return
    if not query:
        await reply_to_user(update, TERM_USAGE_NOTICE)
        return
    service: TermService = context.application.bot_data[SERVICE_KEY]
    translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
    translated = await translate_query_async(service, translator, query)
    if not await _passes_delivery_gate(update, context):
        LOGGER.info("translation reply skipped: authorization changed before delivery")
        return
    await reply_to_user(update, translated)


async def sentence_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    # Inline text wins; otherwise fall back to the replied-to message's content.
    text = " ".join(context.args).strip() or _replied_translatable_text(update)
    if not await _passes_authorization(update, context):
        return
    if not _consume_rate_limit(update, context):
        await reply_to_user(update, THROTTLE_NOTICE)
        return
    if not text:
        await reply_to_user(update, SENTENCE_USAGE_NOTICE)
        return
    service: TermService = context.application.bot_data[SERVICE_KEY]
    translator: SentenceTranslator = context.application.bot_data[TRANSLATOR_KEY]
    translated = await translate_query_async(service, translator, text)
    if not await _passes_delivery_gate(update, context):
        LOGGER.info("translation reply skipped: authorization changed before delivery")
        return
    await reply_to_user(update, translated)


async def public_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only switch: open translate commands to all group members, or
    restrict them back to admins. Works in groups only.

    Subcommands: on | off | status (empty = status). The /public command
    itself ALWAYS requires admin — public mode does not unlock it, so a
    non-admin can never flip it back off.
    """
    message = update.effective_message
    if not message:
        return
    if not is_group_chat(update):
        await reply_to_user(update, PUBLIC_ONLY_GROUPS_NOTICE)
        return
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    # Fresh admin check (no cache): a control-plane toggle must not honor a
    # stale positive verdict left by a just-demoted/removed admin.
    if not await _is_group_admin(update, context, use_cache=False):
        # Re-use the reject limiter so non-admin /public spam can't flood the
        # chat or starve translation budget; respect the silent override.
        if not config.tr_reject_silent and _consume_reject_limit(update, context):
            await reply_to_user(update, PUBLIC_REJECT_NOTICE)
        return
    # Deliberately NOT gated by the translation rate limiter: an admin must
    # always be able to close a busy/spammed public chat even when the per-chat
    # translation budget is exhausted.
    chat = update.effective_chat
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    arg = context.args[0].strip().lower() if context.args else ""
    if arg in {"on", "open", "enable"}:
        if await _persist_public(update, settings, chat.id, True):
            await reply_to_user(update, PUBLIC_ENABLED_NOTICE)
    elif arg in {"off", "close", "disable"}:
        if await _persist_public(update, settings, chat.id, False):
            await reply_to_user(update, PUBLIC_DISABLED_NOTICE)
    elif arg in {"", "status"}:
        await reply_to_user(
            update,
            PUBLIC_STATUS_ON if settings.is_public(chat.id) else PUBLIC_STATUS_OFF,
        )
    else:
        await reply_to_user(update, PUBLIC_USAGE_NOTICE)


async def _persist_public(
    update: Update, settings: ChatSettings, chat_id: int, value: bool
) -> bool:
    """Persist a public-mode change. On a settings-file write failure, reply a
    notice and return False; ChatSettings has already rolled back its in-memory
    state, so memory never diverges from disk."""
    try:
        settings.set_public(chat_id, value)
        return True
    except OSError as exc:
        LOGGER.warning("settings save failed on /public: %r", exc)
        await reply_to_user(update, SETTINGS_SAVE_FAILED_NOTICE)
        return False


def _chat_member_is_in(member) -> bool:
    """Whether a ChatMember object means the bot is actually IN the chat.

    RESTRICTED can be is_member True (in the chat) or False (not in it), so it
    must be classified by is_member rather than status alone; the other "in"
    statuses imply membership and left/kicked imply non-membership.
    """
    status = getattr(member, "status", None)
    if status in {ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER}:
        return True
    if status == ChatMember.RESTRICTED:
        return bool(getattr(member, "is_member", False))
    return False


def _is_owner(update: Update, config: BotConfig) -> bool:
    """True iff the sender is the configured bot owner (fail-closed when unset)."""
    user = update.effective_user
    return (
        user is not None
        and config.owner_user_id is not None
        and user.id == config.owner_user_id
    )


def _bot_added_to_chat(update: Update) -> bool:
    """True only on the genuine 'added' edge: the bot went from not-a-member to
    a-member.

    Membership is computed with is_member (a RESTRICTED member can be a
    non-member), so a restricted-non-member -> member transition counts as an
    add and a left -> restricted-non-member transition does not. Promotions,
    demotions, and removals inside a chat the bot already belongs to do NOT
    count — this protects an already-joined authorized group from being
    auto-left on an unrelated status change.
    """
    cmu = getattr(update, "my_chat_member", None)
    if cmu is None:
        return False
    return not _chat_member_is_in(cmu.old_chat_member) and _chat_member_is_in(
        cmu.new_chat_member
    )


async def _leave_chat_quietly(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    """leave_chat that never raises — the bot may already be absent from the
    target chat (e.g. /revoke <id> for a chat it is not currently in)."""
    try:
        await context.bot.leave_chat(chat_id)
    except TelegramError as exc:
        LOGGER.warning("leave_chat failed chat_id=%s: %r", chat_id, exc)


def _try_persist(action, chat_id: int) -> bool:
    """Run a settings mutation that may raise on a write failure; return whether
    it persisted. ChatSettings rolls back its in-memory state on failure, so a
    False result means nothing changed and the caller should surface it."""
    try:
        action(chat_id)
        return True
    except OSError as exc:
        LOGGER.warning("settings write failed chat_id=%s: %r", chat_id, exc)
        return False


async def my_chat_member_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Group authorization gate. When the bot is added to a chat that is not on
    the allowlist and was not added by the owner, send a bilingual notice then
    leave. Owner-added chats auto-authorize and stay; already-authorized chats
    stay silently."""
    if not _bot_added_to_chat(update):
        return
    chat = update.effective_chat
    if chat is None or chat.type not in {"group", "supergroup", "channel"}:
        return
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    adder = getattr(update.my_chat_member, "from_user", None)
    if (
        config.owner_user_id is not None
        and adder is not None
        and adder.id == config.owner_user_id
    ):
        if not _try_persist(settings.allow, chat.id):
            # Could not persist the authorization (disk full / read-only). Fail
            # closed: leave rather than stay in a chat we cannot remember
            # authorizing. The owner can re-add once the file is writable.
            LOGGER.warning(
                "owner-add authorization could not be persisted, leaving chat_id=%s",
                chat.id,
            )
            await _leave_chat_quietly(context, chat.id)
            return
        LOGGER.info(
            "added by owner; chat authorized chat_id=%s type=%s", chat.id, chat.type
        )
        return
    if settings.is_allowed(chat.id):
        LOGGER.info("added to authorized chat chat_id=%s type=%s", chat.id, chat.type)
        return
    LOGGER.info(
        "added to unauthorized chat, leaving chat_id=%s type=%s title=%r",
        chat.id,
        chat.type,
        getattr(chat, "title", None),
    )
    try:
        await context.bot.send_message(chat.id, UNAUTHORIZED_GROUP_NOTICE)
    except TelegramError as exc:
        LOGGER.warning("unauthorized-leave notice failed: %r", exc)
    await _leave_chat_quietly(context, chat.id)


async def authorize_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Owner-only: add a chat to the group authorization allowlist. In a group,
    /authorize allows the current chat; in private, /authorize <chat_id> allows
    by id and /authorize (or 'list') shows the list. Non-owners get no reply."""
    message = update.effective_message
    if not message:
        return
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    if not _is_owner(update, config):
        return  # owner-only; does not advertise itself to others
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    arg = context.args[0].strip().lower() if context.args else ""
    if is_group_chat(update) and not arg:
        cid = update.effective_chat.id
        if not _try_persist(settings.allow, cid):
            await reply_to_user(update, SETTINGS_SAVE_FAILED_NOTICE)
            return
        await reply_to_user(
            update,
            f"已授权本群（chat_id={cid}）\nThis chat is authorized (chat_id={cid}).",
        )
        return
    if arg in {"", "list"}:
        listing = settings.allowed_chats()
        body = ", ".join(str(c) for c in listing) if listing else "（空 / empty）"
        await reply_to_user(update, f"授权名单 / Allowlist: {body}\n\n{AUTHORIZE_USAGE}")
        return
    try:
        target = int(arg)
    except ValueError:
        await reply_to_user(update, AUTHORIZE_USAGE)
        return
    if not _try_persist(settings.allow, target):
        await reply_to_user(update, SETTINGS_SAVE_FAILED_NOTICE)
        return
    await reply_to_user(update, f"已授权 / Authorized chat_id={target}")


async def revoke_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Owner-only: remove a chat from the allowlist AND make the bot leave it,
    so revoking actually stops service (the allowlist otherwise only gates
    joining). In a group, /revoke targets the current chat; in private,
    /revoke <chat_id> targets by id. Non-owners get no reply."""
    message = update.effective_message
    if not message:
        return
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    if not _is_owner(update, config):
        return
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    arg = context.args[0].strip().lower() if context.args else ""
    if is_group_chat(update) and not arg:
        cid = update.effective_chat.id
        persisted = _try_persist(settings.disallow, cid)
        note = (
            f"已撤销本群授权并退出本群（chat_id={cid}）\nRevoked; leaving this chat (chat_id={cid})."
            if persisted
            else f"未能保存撤销，仍退出本群（chat_id={cid}）；请稍后重试 /revoke。\nCouldn't persist the de-authorization; leaving anyway (chat_id={cid}) — please re-run /revoke later."
        )
        # Best-effort reply BEFORE leaving (the bot cannot post once it leaves);
        # the leave MUST still run even if the reply fails.
        try:
            await reply_to_user(update, note)
        except TelegramError as exc:
            LOGGER.warning("revoke confirmation reply failed chat_id=%s: %r", cid, exc)
        await _leave_chat_quietly(context, cid)
        return
    try:
        target = int(arg)
    except ValueError:
        await reply_to_user(update, REVOKE_USAGE)
        return
    persisted = _try_persist(settings.disallow, target)
    await _leave_chat_quietly(context, target)
    note = (
        f"已撤销并退出 / Revoked and left chat_id={target}"
        if persisted
        else f"已退出但未能保存撤销，请稍后重试 / Left, but couldn't persist; re-run /revoke later (chat_id={target})."
    )
    await reply_to_user(update, note)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read-only diagnostics: data source, pinned commit, term count, limits.

    No auth gate (anyone may ask), no throttle, zero LLM calls.
    """
    message = update.effective_message
    if not message:
        return
    service: TermService = context.application.bot_data[SERVICE_KEY]
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    await reply_to_user(update, _about_text(service, config))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only runtime status. Counts only; no chat ids or secrets."""
    message = update.effective_message
    if not message:
        return
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    if not _is_owner(update, config):
        return
    service: TermService = context.application.bot_data[SERVICE_KEY]
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    reply_index: ChannelReplyIndex = context.application.bot_data[
        CHANNEL_REPLY_INDEX_KEY
    ]
    await reply_to_user(update, _status_text(service, config, settings, reply_index))


def _about_text(service: TermService, config: BotConfig) -> str:
    meta = service.metadata()
    profile = meta.get("source_profile") or "unknown"
    repo = meta.get("source_repo_url")
    commit = meta.get("wutheringdata_commit") or "unknown (not recorded in DB)"
    source = f"{profile} ({repo})" if repo else profile
    llm = "configured" if _llm_configured() else "not configured"
    return (
        "wuwaterm /about\n"
        f"Data source: {source}\n"
        f"Pinned commit: {commit}\n"
        f"Dictionary terms: {service.term_count()}\n"
        f"Rate limit: {config.rate_limit_per_minute}/min per chat\n"
        f"LLM: {llm}"
    )


def _status_text(
    service: TermService,
    config: BotConfig,
    settings: ChatSettings,
    reply_index: ChannelReplyIndex,
) -> str:
    meta = service.metadata()
    commit = meta.get("wutheringdata_commit") or "unknown"
    short_commit = commit[:12] if re.fullmatch(r"[0-9a-f]{40}", commit) else commit
    profile = meta.get("source_profile") or "unknown"
    llm = "yes" if _llm_configured() else "no"
    channel_auto = "on" if config.channel_autotranslate else "off"
    # Counting prunes expired entries; if that triggers a persistence write,
    # the health fields below report the refreshed state.
    tracked_channel_posts = reply_index.entry_count()
    reply_index_persistence = "on" if reply_index.persistence_enabled() else "off"
    return (
        "wuwaterm /status\n"
        f"Dictionary terms: {service.term_count()}\n"
        f"Data profile: {profile}\n"
        f"Data commit: {short_commit}\n"
        f"LLM configured: {llm}\n"
        f"Channel autotranslate: {channel_auto}\n"
        f"Tracked channel posts: {tracked_channel_posts}\n"
        f"Channel reply persistence: {reply_index_persistence}\n"
        f"Channel reply load failures: {reply_index.load_failure_count()}\n"
        f"Channel reply last load: {_reply_index_last_load_status(reply_index)}\n"
        f"Channel reply save failures: {reply_index.save_failure_count()}\n"
        f"Channel reply last save: {_reply_index_last_save_status(reply_index)}\n"
        f"Authorized chats: {settings.allowed_count()}\n"
        f"Public chats: {settings.public_count()}\n"
        f"Rate limit: {config.rate_limit_per_minute}/min per chat\n"
        f"LLM input limit: {LLM_INPUT_CHAR_LIMIT}\n"
        f"Telegram reply limit: {TELEGRAM_TEXT_MESSAGE_LIMIT}"
    )


def _reply_index_last_load_status(reply_index: ChannelReplyIndex) -> str:
    if not reply_index.persistence_enabled():
        return "not configured"
    last_load = reply_index.last_load_succeeded()
    if last_load is None:
        return "not attempted"
    return "ok" if last_load else "failed"


def _reply_index_last_save_status(reply_index: ChannelReplyIndex) -> str:
    if not reply_index.persistence_enabled():
        return "not configured"
    last_save = reply_index.last_save_succeeded()
    if last_save is None:
        return "not attempted"
    return "ok" if last_save else "failed"


def format_term_reply(service: TermService, query: str) -> str:
    translator = SentenceTranslator(service.db_path)
    return translate_query(service, translator, query)


def translate_query(service: TermService, translator: SentenceTranslator, query: str) -> str:
    prepared = translator.prepare_text(query)
    if not prepared:
        return "Nothing to translate after removing metadata."
    # Direction is auto-detected: a Chinese source -> English (default), an
    # all-Latin/English source -> Chinese. Both dictionary and LLM honor it.
    to_chinese = not has_cjk(prepared)
    result = service.lookup(prepared, limit=5)
    if result.exact and result.best:
        official = result.best.entry.zh if to_chinese else result.best.entry.en
        if official:
            return official
    if _is_ascii_fuzzy_query(prepared) and result.best and result.best.score >= 80.0:
        return result.best.entry.zh if to_chinese else result.best.entry.en
    if len(prepared) > LLM_INPUT_CHAR_LIMIT:
        return f"Input is too long for translation ({LLM_INPUT_CHAR_LIMIT} character limit)."
    translated = translator.translate(prepared, to_chinese=to_chinese)
    if _is_short_query(prepared) and not _has_locked_terms(translator, prepared):
        translated = f"{translated}\n\n{DICT_MISS_FLAG}"
    return translated


async def translate_query_async(
    service: TermService, translator: SentenceTranslator, query: str
) -> str:
    prepared = translator.prepare_text(query)
    if not prepared:
        return "Nothing to translate after removing metadata."
    # Direction is auto-detected: a Chinese source -> English (default), an
    # all-Latin/English source -> Chinese. Both dictionary and LLM honor it.
    to_chinese = not has_cjk(prepared)
    result = service.lookup(prepared, limit=5)
    if result.exact and result.best:
        official = result.best.entry.zh if to_chinese else result.best.entry.en
        if official:
            return official
    if _is_ascii_fuzzy_query(prepared) and result.best and result.best.score >= 80.0:
        return result.best.entry.zh if to_chinese else result.best.entry.en
    if len(prepared) > LLM_INPUT_CHAR_LIMIT:
        return f"Input is too long for translation ({LLM_INPUT_CHAR_LIMIT} character limit)."
    translated = await translator.translate_async(prepared, to_chinese=to_chinese)
    if _is_short_query(prepared) and not _has_locked_terms(translator, prepared):
        translated = f"{translated}\n\n{DICT_MISS_FLAG}"
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
    # Rejection replies ride a SEPARATE per-chat budget: non-admin /tr spam can
    # neither starve the translation budget authorized callers depend on, nor
    # flood the chat. Within budget -> reply (respecting tr_reject_silent);
    # beyond budget -> silent. The translation limiter is never touched here.
    if not config.tr_reject_silent and _consume_reject_limit(update, context):
        reject_text = (
            config.group_tr_reject_text
            if is_group_chat(update)
            else config.private_tr_reject_text
        )
        await reply_to_user(update, reject_text)
    return False


async def _passes_delivery_gate(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Silent pre-send auth check for non-blocking translation tasks.

    A slow LLM call can finish after /public off or /revoke has already changed
    the chat policy. Initial rejection remains user-visible; this late gate is
    deliberately silent and consumes no reject or translation budget.
    """
    if is_group_chat(update):
        chat = update.effective_chat
        if chat is None:
            return False
        settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
        if not settings.is_allowed(chat.id):
            return False
        if await _is_group_admin(update, context, use_cache=False):
            return True
        message = update.effective_message
        if (
            update.effective_user is None
            or getattr(message, "sender_chat", None) is not None
        ):
            return False
        return settings.is_public(chat.id)
    chat = update.effective_chat
    if chat is None or chat.type != "private":
        return False
    config: BotConfig = context.application.bot_data[CONFIG_KEY]
    return _is_owner(update, config)


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


async def _is_group_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    use_cache: bool = True,
) -> bool:
    """Pure admin check (no public-mode bypass).

    Used by /public itself — public mode must never let a non-admin flip
    the switch back off. Also the base layer of _is_authorized_group_sender.

    Control-plane callers (the /public toggle) pass ``use_cache=False`` so a
    just-demoted admin cannot keep changing chat-wide access on a stale
    5-minute cache verdict. The data plane (/tr) keeps the cache for cost.
    """
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
    if use_cache:
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


async def _is_authorized_group_sender(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Authorization for translate commands in a group.

    The chat must be on the allowlist at all: the bot only serves groups it is
    authorized for, so any chat it should have left (an unauthorized add, a
    /revoke, or a leave that failed) stops being served even before the bot is
    actually removed. Within an allowlisted chat: admins always; non-admins only
    when an admin has flipped /public on.
    """
    chat = update.effective_chat
    if chat is None:
        return False
    settings: ChatSettings = context.application.bot_data[CHAT_SETTINGS_KEY]
    if not settings.is_allowed(chat.id):
        return False
    if await _is_group_admin(update, context):
        return True
    # Public mode opens the door only for ORDINARY member messages — never for
    # a foreign sender_chat identity (a channel posting as itself, or a
    # linked-channel auto-forward whose text happens to start with /tr). The
    # anonymous group-admin case (sender_chat == chat.id) is allowed above.
    message = update.effective_message
    if (
        update.effective_user is None
        or getattr(message, "sender_chat", None) is not None
    ):
        return False
    return settings.is_public(chat.id)


def _consume_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = chat_id_for(update)
    if chat_id is None:
        return True
    limiter: PerChatRateLimiter = context.application.bot_data[RATE_LIMITER_KEY]
    return limiter.allow(chat_id)


def _consume_reject_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Per-chat budget for unauthorized-rejection replies, separate from the
    translation budget so reject spam cannot starve authorized translations."""
    chat_id = chat_id_for(update)
    if chat_id is None:
        return True
    limiter: PerChatRateLimiter = context.application.bot_data[REJECT_LIMITER_KEY]
    return limiter.allow(chat_id)
