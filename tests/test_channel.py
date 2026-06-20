from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from telegram.error import BadRequest, TelegramError

from wuwaterm.bot import (
    ADMIN_CACHE_KEY,
    CHANNEL_REPLY_INDEX_KEY,
    CHAT_SETTINGS_KEY,
    CONFIG_KEY,
    RATE_LIMITER_KEY,
    REJECT_LIMITER_KEY,
    SERVICE_KEY,
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    THROTTLE_NOTICE,
    TRANSLATOR_KEY,
    AdminStatusCache,
    BotConfig,
    ChannelReplyIndex,
    PerChatRateLimiter,
    term_command,
)
from wuwaterm.channel import (
    channel_post_handler,
    count_cjk,
    strip_telegram_html,
    validate_telegram_html,
)
from wuwaterm.lookup import TermService
from wuwaterm.sentence import SentenceTranslator, _llm_error_from_response
from wuwaterm.settings import ChatSettings


CN_TEXT = "今汐说声骸很强"
CN_TEXT_HTML = (
    '<b>今汐</b>说<a href="https://example.com">声骸</a>很'
    "<tg-spoiler>强</tg-spoiler>"
)


def telegram_text_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int,
        text: str | None = None,
        caption: str | None = None,
        text_html: str | None = None,
        caption_html: str | None = None,
        sender_chat=None,
        is_automatic_forward: bool = False,
        date: datetime | None = None,
        edit_date: datetime | None = None,
    ):
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.text_html = text_html
        self.caption_html = caption_html
        self.sender_chat = sender_chat
        self.is_automatic_forward = is_automatic_forward
        self.date = date
        self.edit_date = edit_date
        self.replies: list[tuple[str, str | None, int | None]] = []
        self._reply_count = 0

    async def reply_text(self, text: str, **kwargs):
        if telegram_text_units(text) > TELEGRAM_TEXT_MESSAGE_LIMIT:
            raise BadRequest("Message is too long")
        self._reply_count += 1
        self.replies.append(
            (text, kwargs.get("parse_mode"), kwargs.get("reply_to_message_id"))
        )
        base_offset = 1001 if self.edit_date is not None else 1000
        return SimpleNamespace(
            message_id=self.message_id + base_offset + self._reply_count - 1
        )


class FakeBot:
    def __init__(
        self,
        default_status: str = "administrator",
        edit_raises=None,
        delete_raises=None,
    ):
        self.default_status = default_status
        self.member_calls: list[tuple[int, int]] = []
        self.edits: list[tuple[str, str | None, int | None, int | None]] = []
        self.edit_attempts: list[tuple[str, str | None, int | None, int | None]] = []
        self.deleted_messages: list[tuple[int | None, int | None]] = []
        # edit_raises(text, parse_mode, chat_id, message_id) -> Exception | None
        self._edit_raises = edit_raises
        # delete_raises(chat_id, message_id) -> Exception | None
        self._delete_raises = delete_raises

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.member_calls.append((chat_id, user_id))
        return SimpleNamespace(status=self.default_status)

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kwargs):
        parse_mode = kwargs.get("parse_mode")
        self.edit_attempts.append((text, parse_mode, chat_id, message_id))
        if text is not None and telegram_text_units(text) > TELEGRAM_TEXT_MESSAGE_LIMIT:
            raise BadRequest("Message is too long")
        if self._edit_raises is not None:
            exc = self._edit_raises(text, parse_mode, chat_id, message_id)
            if exc is not None:
                raise exc
        self.edits.append((text, parse_mode, chat_id, message_id))
        return SimpleNamespace(message_id=message_id)

    async def delete_message(self, chat_id=None, message_id=None):
        self.deleted_messages.append((chat_id, message_id))
        if self._delete_raises is not None:
            exc = self._delete_raises(chat_id, message_id)
            if exc is not None:
                raise exc
        return True


def channel_update(
    *,
    text: str | None = None,
    caption: str | None = None,
    text_html: str | None = None,
    caption_html: str | None = None,
    chat_id: int = -2001,
    message_id: int = 4001,
    update_id: int | None = None,
    date: datetime | None = None,
    edit_date: datetime | None = None,
):
    message = FakeMessage(
        message_id=message_id,
        text=text,
        caption=caption,
        text_html=text_html if text_html is not None else text,
        caption_html=caption_html if caption_html is not None else caption,
        sender_chat=SimpleNamespace(id=-3001, type="channel"),
        is_automatic_forward=True,
        date=date,
        edit_date=edit_date,
    )
    update = SimpleNamespace(
        update_id=update_id,
        effective_message=message,
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=None,
    )
    return update, message


def command_update(*, chat_id: int = -2001, message_id: int = 4101, user_id: int = 11):
    message = FakeMessage(message_id=message_id)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=chat_id, type="supergroup"),
        effective_user=SimpleNamespace(id=user_id),
    )
    return update, message


def make_context(sample_db, *, config=None, args=(), bot=None, allowlist=(-2001,)):
    config = config or BotConfig(rate_limit_per_minute=10, owner_user_id=11)
    chat_settings = ChatSettings(sample_db.parent / "chat_settings.json")
    for cid in allowlist:
        chat_settings.allow(cid)
    return SimpleNamespace(
        args=list(args),
        bot=bot or FakeBot(),
        application=SimpleNamespace(
            bot_data={
                SERVICE_KEY: TermService(sample_db),
                TRANSLATOR_KEY: SentenceTranslator(
                    sample_db,
                    llm_timeout_seconds=config.llm_timeout_seconds,
                    llm_max_concurrency=config.llm_max_concurrency,
                ),
                CONFIG_KEY: config,
                RATE_LIMITER_KEY: PerChatRateLimiter(limit=config.rate_limit_per_minute),
                REJECT_LIMITER_KEY: PerChatRateLimiter(limit=config.rate_limit_per_minute),
                ADMIN_CACHE_KEY: AdminStatusCache(),
                CHANNEL_REPLY_INDEX_KEY: ChannelReplyIndex(
                    ttl_seconds=config.channel_max_age_seconds
                ),
                CHAT_SETTINGS_KEY: chat_settings,
            }
        ),
    )


def enable_mock_llm(monkeypatch, calls, response_factory):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def fake_call(
        locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
        transport=None,
    ):
        calls.append((locked_text, locks, html_mode))
        return response_factory(locked_text, locks)

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)


def placeholder_for(locks, official):
    for placeholder, _source, en in locks:
        if en == official:
            return placeholder
    raise AssertionError(f"missing official lock {official}")


def test_cn_formatted_post_gets_html_reply_with_tags_and_locks(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        jinhsi = placeholder_for(locks, "Jinhsi")
        echo = placeholder_for(locks, "Echo")
        return (
            f'<b>{jinhsi}</b> says <a href="https://example.com">{echo}</a>'
            f" is <tg-spoiler>strong</tg-spoiler>"
        )

    enable_mock_llm(monkeypatch, calls, response)
    update, message = channel_update(text=CN_TEXT, text_html=CN_TEXT_HTML, message_id=4001)
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    assert len(calls) == 1
    locked_text, locks, html_mode = calls[0]
    assert html_mode is True
    assert "今汐" not in locked_text
    assert "声骸" not in locked_text
    assert {lock[2] for lock in locks} >= {"Jinhsi", "Echo"}
    assert message.replies == [
        (
            '<b>Jinhsi</b> says <a href="https://example.com">Echo</a>'
            " is <tg-spoiler>strong</tg-spoiler>",
            "HTML",
            4001,
        )
    ]


def test_non_translatable_post_is_silent_and_consumes_no_throttle(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db, config=BotConfig(rate_limit_per_minute=1, owner_user_id=11)
    )
    # No Chinese and no Latin letters -> nothing worth translating -> silent,
    # and the gate returns before any throttle slot is consumed.
    noise_update, noise_message = channel_update(
        text="🔥🔥🔥 123 !!!", message_id=4010
    )

    asyncio.run(channel_post_handler(noise_update, context))

    assert noise_message.replies == []
    assert calls == []

    cn_update, cn_message = channel_update(text=CN_TEXT, message_id=4011)
    asyncio.run(channel_post_handler(cn_update, context))

    assert cn_message.replies == [("translated", "HTML", 4011)]
    assert len(calls) == 1


def test_english_post_translates_to_chinese_html(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        jinhsi = placeholder_for(locks, "Jinhsi")
        echo = placeholder_for(locks, "Echo")
        return f"<b>{jinhsi}</b>装备了<tg-spoiler>{echo}</tg-spoiler>"

    enable_mock_llm(monkeypatch, calls, response)
    update, message = channel_update(
        text="Jinhsi equips Echo",
        text_html="<b>Jinhsi</b> equips <tg-spoiler>Echo</tg-spoiler>",
        message_id=4012,
    )
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    assert message.replies == [
        ("<b>今汐</b>装备了<tg-spoiler>声骸</tg-spoiler>", "HTML", 4012)
    ]
    assert len(calls) == 1
    assert calls[0][2] is True  # HTML pipeline used


def test_english_exact_term_post_emits_official_chinese(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = channel_update(text="Echo", text_html="Echo", message_id=4013)
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    # Dictionary-first, zero LLM: English term -> official Chinese, plain.
    assert message.replies == [("声骸", None, 4013)]
    assert calls == []


def test_invalid_llm_html_falls_back_to_plain_reply(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        jinhsi = placeholder_for(locks, "Jinhsi")
        echo = placeholder_for(locks, "Echo")
        return f"<div><b>{jinhsi} says {echo} is strong</div>"

    enable_mock_llm(monkeypatch, calls, response)
    update, message = channel_update(text=CN_TEXT, text_html=CN_TEXT_HTML, message_id=4020)
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    assert message.replies == [("Jinhsi says Echo is strong", None, 4020)]
    assert "<" not in message.replies[0][0]


def test_caption_post_uses_same_html_pipeline(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        jinhsi = placeholder_for(locks, "Jinhsi")
        return f"<b>{jinhsi}</b> arrives"

    enable_mock_llm(monkeypatch, calls, response)
    update, message = channel_update(
        caption="今汐登场", caption_html="<b>今汐</b>登场", message_id=4030
    )
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    assert len(calls) == 1
    assert calls[0][2] is True
    assert message.replies == [("<b>Jinhsi</b> arrives", "HTML", 4030)]


def test_throttle_is_shared_with_commands_and_skips_silently(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db,
        config=BotConfig(rate_limit_per_minute=2, owner_user_id=11),
        args=["声骸"],
    )

    messages = []
    for idx in range(3):
        update, message = channel_update(text=CN_TEXT, message_id=4040 + idx)
        asyncio.run(channel_post_handler(update, context))
        messages.append(message)

    assert len(messages[0].replies) == 1
    assert len(messages[1].replies) == 1
    assert messages[2].replies == []
    assert len(calls) == 2

    tr_update, tr_message = command_update(message_id=4050)
    asyncio.run(term_command(tr_update, context))

    assert tr_message.replies == [(THROTTLE_NOTICE, None, 4050)]


def test_channel_post_skipped_when_chat_not_allowlisted(monkeypatch, sample_db):
    # Finding B: the linked-channel auto-translate path is also gated on the
    # allowlist, so an unauthorized or revoked group (e.g. one where leave_chat
    # failed) cannot keep getting posts translated and burning LLM budget.
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(sample_db, allowlist=())  # -2001 NOT allowlisted
    update, message = channel_update(text=CN_TEXT, message_id=4600)

    asyncio.run(channel_post_handler(update, context))

    assert message.replies == []  # nothing translated/posted
    assert calls == []  # zero LLM calls


def test_channel_reply_is_skipped_if_chat_revoked_during_llm(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fake_call(
            _locked_text,
            _locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "translated"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = make_context(sample_db)
        update, message = channel_update(text=CN_TEXT, message_id=4601)
        task = asyncio.create_task(channel_post_handler(update, context))
        await asyncio.wait_for(started.wait(), timeout=0.2)

        settings = context.application.bot_data[CHAT_SETTINGS_KEY]
        settings.disallow(-2001)
        release.set()
        await asyncio.wait_for(task, timeout=0.2)

        assert calls == 1
        assert message.replies == []

    asyncio.run(run())


def test_budget_exhaustion_is_silent_with_one_clean_warning(
    monkeypatch, sample_db, caplog
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def fake_call(
        _locked_text,
        _locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
        transport=None,
    ):
        raise _llm_error_from_response(
            httpx.Response(429, text='{"error":"max_budget exceeded"}')
        )

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    update, message = channel_update(text=CN_TEXT, message_id=4060)
    context = make_context(sample_db)

    with caplog.at_level(logging.WARNING, logger="wuwaterm.channel"):
        asyncio.run(channel_post_handler(update, context))

    assert message.replies == []
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and record.name == "wuwaterm.channel"
    ]
    assert len(warnings) == 1
    warning_text = warnings[0].getMessage()
    assert "max_budget" not in warning_text
    assert "429" not in warning_text
    assert re.search(r"\d", warning_text) is None


def test_kill_switch_disables_listener_without_llm(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = channel_update(text=CN_TEXT, message_id=4070)
    context = make_context(
        sample_db,
        config=BotConfig(
            rate_limit_per_minute=10, owner_user_id=11, channel_autotranslate=False
        ),
    )

    asyncio.run(channel_post_handler(update, context))

    assert message.replies == []
    assert calls == []


def test_min_cjk_threshold_gates_short_posts(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db,
        config=BotConfig(rate_limit_per_minute=10, owner_user_id=11, channel_min_cjk=2),
    )

    one_update, one_message = channel_update(text="强! New patch", message_id=4080)
    asyncio.run(channel_post_handler(one_update, context))

    assert one_message.replies == []
    assert calls == []

    two_update, two_message = channel_update(text="很强! New patch", message_id=4081)
    asyncio.run(channel_post_handler(two_update, context))

    assert two_message.replies == [("translated", "HTML", 4081)]
    assert len(calls) == 1


def test_exact_dictionary_hit_replies_official_plain_with_zero_llm(
    monkeypatch, sample_db
):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = channel_update(text="声骸", message_id=4090)
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    assert message.replies == [("Echo", None, 4090)]
    assert calls == []


def test_stale_post_is_silent_with_zero_llm_and_zero_throttle(monkeypatch, sample_db):
    """Replayed history (restart backlog, admin-promotion replay) must never
    be translated: a post older than channel_max_age_seconds is skipped
    before the throttle, so a follow-up fresh post still translates."""
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db, config=BotConfig(rate_limit_per_minute=1, owner_user_id=11)
    )
    stale_update, stale_message = channel_update(
        text=CN_TEXT,
        message_id=4100,
        date=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    asyncio.run(channel_post_handler(stale_update, context))

    assert stale_message.replies == []
    assert calls == []

    fresh_update, fresh_message = channel_update(
        text=CN_TEXT, message_id=4101, date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(fresh_update, context))

    assert fresh_message.replies == [("translated", "HTML", 4101)]
    assert len(calls) == 1


def test_post_age_boundary_respects_configured_max_age(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db,
        config=BotConfig(
            rate_limit_per_minute=10, owner_user_id=11, channel_max_age_seconds=60
        ),
    )

    old_update, old_message = channel_update(
        text=CN_TEXT,
        message_id=4110,
        date=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    asyncio.run(channel_post_handler(old_update, context))

    assert old_message.replies == []
    assert calls == []

    recent_update, recent_message = channel_update(
        text=CN_TEXT,
        message_id=4111,
        date=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    asyncio.run(channel_post_handler(recent_update, context))

    assert recent_message.replies == [("translated", "HTML", 4111)]
    assert len(calls) == 1


def test_missing_date_is_treated_as_fresh(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    update, message = channel_update(text=CN_TEXT, message_id=4120, date=None)
    context = make_context(sample_db)

    asyncio.run(channel_post_handler(update, context))

    assert message.replies == [("translated", "HTML", 4120)]
    assert len(calls) == 1


def test_from_env_parses_channel_max_age_seconds(monkeypatch):
    monkeypatch.setenv("OWNER_USER_ID", "11")
    monkeypatch.setenv("WUWATERM_CHANNEL_MAX_AGE_SECONDS", "60")

    config = BotConfig.from_env()

    assert config.channel_max_age_seconds == 60


def test_count_cjk_counts_ideographs_only():
    assert count_cjk("今汐说声骸很强") == 7
    assert count_cjk("Patch notes are live now!") == 0
    assert count_cjk("强! New patch") == 1


@pytest.mark.parametrize(
    "snippet",
    [
        "<b>x</b>",
        "<strong>x</strong>",
        "<i>x</i>",
        "<em>x</em>",
        "<u>x</u>",
        "<ins>x</ins>",
        "<s>x</s>",
        "<strike>x</strike>",
        "<del>x</del>",
        '<a href="https://example.com">x</a>',
        "<code>x</code>",
        '<code class="language-python">x</code>',
        "<pre>x</pre>",
        "<blockquote>x</blockquote>",
        "<blockquote expandable>x</blockquote>",
        '<span class="tg-spoiler">x</span>',
        "<tg-spoiler>x</tg-spoiler>",
        '<tg-emoji emoji-id="5368324170671202286">x</tg-emoji>',
        "plain text with &amp; entity",
        "<b>nested <i>tags</i></b>",
    ],
)
def test_validator_accepts_telegram_subset(snippet):
    assert validate_telegram_html(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "<div>x</div>",  # disallowed tag
        '<b class="x">y</b>',  # attribute not allowed on b
        "<a>x</a>",  # missing required href
        '<a href="">x</a>',  # empty href
        '<a href="https://example.com" onclick="x">y</a>',  # extra attribute
        '<span class="other">x</span>',  # wrong span class
        "<tg-emoji>x</tg-emoji>",  # missing emoji-id
        '<code class="python">x</code>',  # class without language- prefix
        "<b><i>x</b></i>",  # mismatched nesting
        "<b>x",  # unclosed tag
        "x</b>",  # stray close tag
        "<b/>",  # self-closing (no void tags in the subset)
        "<!-- c -->",  # comment
    ],
)
def test_validator_rejects_invalid_html(snippet):
    assert not validate_telegram_html(snippet)


def test_strip_telegram_html_unescapes_and_never_raises():
    assert strip_telegram_html("<b>Jinhsi</b> &amp; <i>Echo</i>") == "Jinhsi & Echo"
    assert strip_telegram_html("&lt;b&gt;") == "<b>"
    for nasty in ("<b>x", "<<<>>>", "<a href='x", "", "<![CDATA[x]]>", "&#x4e00;"):
        assert isinstance(strip_telegram_html(nasty), str)


# --- channel-post edit dedup (Option B: edit-in-place, update-only) ---
#
# FakeMessage.reply_text returns message_id + 1000, so the bot's reply to a
# post with id N is tracked as reply id N + 1000.


def test_edit_updates_existing_reply_in_place(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(sample_db)

    # New post -> normal in-thread HTML reply, remembered.
    new_update, new_message = channel_update(text=CN_TEXT, message_id=4200)
    asyncio.run(channel_post_handler(new_update, context))
    assert new_message.replies == [("translated", "HTML", 4200)]

    # Same post edited (same message_id, edit_date set) arrives as its own
    # update -> the tracked reply (5200) is edited in place; no new reply.
    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4200, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert context.bot.edits == [("translated", "HTML", -2001, 5200)]
    assert len(calls) == 2  # the edit re-translated


def test_edit_waits_for_original_inflight_reply(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def fake_call(
            _locked_text,
            _locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await release_first.wait()
                return "original translation"
            return "edited translation"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = make_context(sample_db)
        new_update, new_message = channel_update(text=CN_TEXT, message_id=4205)
        new_task = asyncio.create_task(channel_post_handler(new_update, context))
        edit_update, edit_message = channel_update(
            text=CN_TEXT, message_id=4205, edit_date=datetime.now(timezone.utc)
        )
        edit_task = asyncio.create_task(channel_post_handler(edit_update, context))
        await asyncio.wait_for(first_started.wait(), timeout=0.2)
        await asyncio.sleep(0)
        assert not edit_task.done()
        assert edit_message.replies == []

        release_first.set()
        await asyncio.wait_for(new_task, timeout=0.2)
        await asyncio.wait_for(edit_task, timeout=0.2)

        assert new_message.replies == [("original translation", "HTML", 4205)]
        assert edit_message.replies == []
        assert context.bot.edits == [("edited translation", "HTML", -2001, 5205)]
        assert calls == 2

    asyncio.run(run())


def test_concurrent_edits_do_not_overwrite_newer_translation(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        first_edit_started = asyncio.Event()
        release_first_edit = asyncio.Event()
        calls = 0

        async def fake_call(
            _locked_text,
            _locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            nonlocal calls
            calls += 1
            if calls == 1:
                return "original translation"
            if calls == 2:
                first_edit_started.set()
                await release_first_edit.wait()
                return "older edit translation"
            return "newer edit translation"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = make_context(sample_db)
        new_update, new_message = channel_update(
            text=CN_TEXT, message_id=4215, update_id=100
        )
        await channel_post_handler(new_update, context)
        assert new_message.replies == [("original translation", "HTML", 4215)]

        older_update, _ = channel_update(
            text=f"{CN_TEXT}旧",
            message_id=4215,
            update_id=101,
            edit_date=datetime.now(timezone.utc),
        )
        newer_update, _ = channel_update(
            text=f"{CN_TEXT}新",
            message_id=4215,
            update_id=102,
            edit_date=datetime.now(timezone.utc),
        )
        older_task = asyncio.create_task(channel_post_handler(older_update, context))
        await asyncio.wait_for(first_edit_started.wait(), timeout=0.2)

        newer_task = asyncio.create_task(channel_post_handler(newer_update, context))
        await asyncio.wait_for(newer_task, timeout=0.2)
        assert context.bot.edits == [
            ("newer edit translation", "HTML", -2001, 5215)
        ]

        release_first_edit.set()
        await asyncio.wait_for(older_task, timeout=0.2)

        assert context.bot.edits == [
            ("newer edit translation", "HTML", -2001, 5215)
        ]
        assert calls == 3

    asyncio.run(run())


def test_edit_with_no_tracked_reply_is_silent(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(sample_db)

    # An edit with nothing remembered (e.g. after a restart dropped the map):
    # never translate, never reply, never edit.
    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4210, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert context.bot.edits == []
    assert calls == []


def test_edit_re_translates_and_consumes_a_throttle_slot(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    # limit=2: the new post (slot 1) and its edit (slot 2) exhaust the budget,
    # so a second distinct post is throttled -> the edit DID consume a slot.
    context = make_context(
        sample_db, config=BotConfig(rate_limit_per_minute=2, owner_user_id=11)
    )

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4220)
    asyncio.run(channel_post_handler(new_update, context))
    assert new_message.replies == [("translated", "HTML", 4220)]

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4220, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))
    assert context.bot.edits == [("translated", "HTML", -2001, 5220)]
    assert len(calls) == 2

    third_update, third_message = channel_update(text=CN_TEXT, message_id=4221)
    asyncio.run(channel_post_handler(third_update, context))
    assert third_message.replies == []  # budget drained by post + edit
    assert len(calls) == 2


def test_edit_dictionary_hit_updates_reply_in_place(monkeypatch, sample_db):
    # An exact-dictionary post replies with the official term and is remembered;
    # editing it updates that same reply in place — still zero LLM. (The
    # dictionary short-circuit sits after the LLM-configured gate, so the
    # endpoint must be configured even though it is never called.)
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    context = make_context(sample_db)

    new_update, new_message = channel_update(text="声骸", message_id=4270)
    asyncio.run(channel_post_handler(new_update, context))
    assert new_message.replies == [("Echo", None, 4270)]

    edit_update, edit_message = channel_update(
        text="声骸", message_id=4270, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert context.bot.edits == [("Echo", None, -2001, 5270)]
    assert calls == []


def test_long_channel_html_reply_is_split_to_plain_chunks(monkeypatch, sample_db):
    calls = []
    long_visible_text = "A" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 17)
    enable_mock_llm(
        monkeypatch,
        calls,
        lambda _locked_text, _locks: f"<b>{long_visible_text}</b>",
    )
    context = make_context(sample_db)
    update, message = channel_update(text=CN_TEXT, message_id=4280)

    asyncio.run(channel_post_handler(update, context))

    assert len(message.replies) == 2
    assert all(len(text) <= TELEGRAM_TEXT_MESSAGE_LIMIT for text, _, _ in message.replies)
    assert [parse_mode for _text, parse_mode, _reply_to in message.replies] == [
        None,
        None,
    ]
    assert [reply_to for _text, _parse_mode, reply_to in message.replies] == [
        4280,
        4280,
    ]
    assert "".join(text for text, _parse_mode, _reply_to in message.replies) == (
        long_visible_text
    )
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4280) == (5280, 5281)
    assert len(calls) == 1


def test_long_channel_emoji_reply_is_split_by_telegram_utf16_units(
    monkeypatch, sample_db
):
    calls = []
    long_visible_text = "😀" * (TELEGRAM_TEXT_MESSAGE_LIMIT // 2 + 1)
    enable_mock_llm(
        monkeypatch,
        calls,
        lambda _locked_text, _locks: f"<b>{long_visible_text}</b>",
    )
    context = make_context(sample_db)
    update, message = channel_update(text=CN_TEXT, message_id=4285)

    asyncio.run(channel_post_handler(update, context))

    assert len(message.replies) == 2
    assert all(
        telegram_text_units(text) <= TELEGRAM_TEXT_MESSAGE_LIMIT
        for text, _parse_mode, _reply_to in message.replies
    )
    assert [parse_mode for _text, parse_mode, _reply_to in message.replies] == [
        None,
        None,
    ]
    assert "".join(text for text, _parse_mode, _reply_to in message.replies) == (
        long_visible_text
    )
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4285) == (5285, 5286)
    assert len(calls) == 1


def test_long_channel_reply_remembers_sent_chunks_after_mid_send_failure(
    monkeypatch, sample_db
):
    calls = []
    long_visible_text = "E" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 12)
    enable_mock_llm(
        monkeypatch,
        calls,
        lambda _locked_text, _locks: f"<div>{long_visible_text}</div>",
    )
    context = make_context(sample_db)
    update, message = channel_update(text=CN_TEXT, message_id=4288)

    original_reply_text = message.reply_text
    send_attempts = []

    async def flaky_reply_text(text: str, **kwargs):
        send_attempts.append(text)
        if len(send_attempts) == 2:
            raise BadRequest("temporary send failed")
        return await original_reply_text(text, **kwargs)

    message.reply_text = flaky_reply_text

    with pytest.raises(BadRequest, match="temporary send failed"):
        asyncio.run(channel_post_handler(update, context))

    assert len(message.replies) == 1
    assert send_attempts == ["E" * TELEGRAM_TEXT_MESSAGE_LIMIT, "E" * 12]
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4288) == (5288,)
    assert len(calls) == 1


def test_long_channel_edit_adds_tracked_continuation_chunks(monkeypatch, sample_db):
    calls = []
    responses = iter(["short translation", "B" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 9)])
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    context = make_context(sample_db)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4290)
    asyncio.run(channel_post_handler(new_update, context))
    assert new_message.replies == [("short translation", "HTML", 4290)]

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4290, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert context.bot.edits == [
        ("B" * TELEGRAM_TEXT_MESSAGE_LIMIT, None, -2001, 5290)
    ]
    assert edit_message.replies == [("B" * 9, None, 4290)]
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4290) == (5290, 5291)
    assert len(calls) == 2


def test_long_channel_edit_remembers_continuations_after_mid_send_failure(
    monkeypatch, sample_db
):
    calls = []
    long_visible_text = "F" * (TELEGRAM_TEXT_MESSAGE_LIMIT * 2 + 7)
    responses = iter(["short translation", f"<div>{long_visible_text}</div>"])
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    context = make_context(sample_db)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4291)
    asyncio.run(channel_post_handler(new_update, context))
    assert new_message.replies == [("short translation", "HTML", 4291)]

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4291, edit_date=datetime.now(timezone.utc)
    )
    original_reply_text = edit_message.reply_text

    async def flaky_reply_text(text: str, **kwargs):
        if len(edit_message.replies) == 1:
            raise BadRequest("temporary continuation send failed")
        return await original_reply_text(text, **kwargs)

    edit_message.reply_text = flaky_reply_text

    with pytest.raises(BadRequest, match="temporary continuation send failed"):
        asyncio.run(channel_post_handler(edit_update, context))

    assert context.bot.edits == [
        ("F" * TELEGRAM_TEXT_MESSAGE_LIMIT, None, -2001, 5291)
    ]
    assert edit_message.replies == [("F" * TELEGRAM_TEXT_MESSAGE_LIMIT, None, 4291)]
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4291) == (5291, 5292)
    assert len(calls) == 2


def test_channel_edit_reloads_tracked_chunks_after_waiting_for_delivery_lock(
    monkeypatch, sample_db
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        responses = iter(
            [
                "original translation",
                "A" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 5),
                "newer short translation",
            ]
        )

        async def fake_call(
            _locked_text,
            _locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            return next(responses)

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = make_context(sample_db)

        new_update, new_message = channel_update(
            text=CN_TEXT, message_id=4292, update_id=100
        )
        await channel_post_handler(new_update, context)
        assert new_message.replies == [("original translation", "HTML", 4292)]

        original_edit_message_text = context.bot.edit_message_text
        first_edit_started = asyncio.Event()
        release_first_edit = asyncio.Event()
        edit_calls = 0

        async def blocking_edit_message_text(*args, **kwargs):
            nonlocal edit_calls
            edit_calls += 1
            if edit_calls == 1:
                first_edit_started.set()
                await release_first_edit.wait()
            return await original_edit_message_text(*args, **kwargs)

        context.bot.edit_message_text = blocking_edit_message_text

        older_update, older_message = channel_update(
            text=f"{CN_TEXT}旧",
            message_id=4292,
            update_id=101,
            edit_date=datetime.now(timezone.utc),
        )
        newer_update, _ = channel_update(
            text=f"{CN_TEXT}新",
            message_id=4292,
            update_id=102,
            edit_date=datetime.now(timezone.utc),
        )
        older_task = asyncio.create_task(channel_post_handler(older_update, context))
        await asyncio.wait_for(first_edit_started.wait(), timeout=0.2)

        newer_task = asyncio.create_task(channel_post_handler(newer_update, context))
        await asyncio.sleep(0.05)

        release_first_edit.set()
        await asyncio.wait_for(older_task, timeout=0.2)
        await asyncio.wait_for(newer_task, timeout=0.2)

        assert older_message.replies == [("A" * 5, None, 4292)]
        assert context.bot.deleted_messages == [(-2001, 5293)]
        reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
        assert reply_index.get_many(-2001, 4292) == (5292,)

    asyncio.run(run())


def test_channel_edit_deletes_stale_extra_chunks_when_translation_shrinks(
    monkeypatch, sample_db
):
    calls = []
    responses = iter(["C" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 11), "shorter translation"])
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    context = make_context(sample_db)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4295)
    asyncio.run(channel_post_handler(new_update, context))
    assert len(new_message.replies) == 2

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4295, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert context.bot.edits == [("shorter translation", "HTML", -2001, 5295)]
    assert context.bot.deleted_messages == [(-2001, 5296)]
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4295) == (5295,)
    assert len(calls) == 2


def test_long_channel_edit_reply_gone_does_not_send_continuation_chunks(
    monkeypatch, sample_db
):
    calls = []
    responses = iter(["short translation", "D" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 13)])
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    bot = FakeBot(edit_raises=lambda *_: BadRequest("Message to edit not found"))
    context = make_context(sample_db, bot=bot)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4298)
    asyncio.run(channel_post_handler(new_update, context))
    assert new_message.replies == [("short translation", "HTML", 4298)]

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4298, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert bot.edits == []
    assert len(bot.edit_attempts) == 1
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4298) == ()
    assert len(calls) == 2


def test_channel_edit_reply_gone_prunes_tracked_continuation_chunks(
    monkeypatch, sample_db
):
    calls = []
    responses = iter(["H" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 6), "shorter translation"])
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    bot = FakeBot(edit_raises=lambda *_: BadRequest("Message to edit not found"))
    context = make_context(sample_db, bot=bot)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4299)
    asyncio.run(channel_post_handler(new_update, context))
    assert len(new_message.replies) == 2

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4299, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert bot.edits == []
    assert bot.deleted_messages == [(-2001, 5300)]
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4299) == ()
    assert len(calls) == 2


def test_channel_edit_missing_continuation_chunk_replaces_and_drops_bad_id(
    monkeypatch, sample_db
):
    calls = []
    responses = iter(
        [
            "I" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 4),
            "J" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 6),
        ]
    )
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    bot = FakeBot(
        edit_raises=lambda _text, _parse_mode, _chat_id, message_id: (
            BadRequest("Message to edit not found") if message_id == 5302 else None
        )
    )
    context = make_context(sample_db, bot=bot)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4301)
    asyncio.run(channel_post_handler(new_update, context))
    assert len(new_message.replies) == 2

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4301, edit_date=datetime.now(timezone.utc)
    )

    async def replacement_reply_text(text: str, **kwargs):
        edit_message.replies.append(
            (text, kwargs.get("parse_mode"), kwargs.get("reply_to_message_id"))
        )
        return SimpleNamespace(message_id=6302)

    edit_message.reply_text = replacement_reply_text
    asyncio.run(channel_post_handler(edit_update, context))

    assert bot.edits == [("J" * TELEGRAM_TEXT_MESSAGE_LIMIT, None, -2001, 5301)]
    assert bot.edit_attempts == [
        ("J" * TELEGRAM_TEXT_MESSAGE_LIMIT, None, -2001, 5301),
        ("J" * 6, None, -2001, 5302),
    ]
    assert edit_message.replies == [("J" * 6, None, 4301)]
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert reply_index.get_many(-2001, 4301) == (5301, 6302)
    assert len(calls) == 2


def test_channel_edit_keeps_failed_delete_tracked_for_retry(
    monkeypatch, sample_db
):
    calls = []
    responses = iter(
        ["K" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 8), "short one", "short two"]
    )
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: next(responses))
    failed_once = True

    def delete_raises(_chat_id, message_id):
        nonlocal failed_once
        if message_id == 5304 and failed_once:
            failed_once = False
            return TelegramError("temporary delete failed")
        return None

    bot = FakeBot(delete_raises=delete_raises)
    context = make_context(sample_db, bot=bot)

    new_update, new_message = channel_update(text=CN_TEXT, message_id=4303)
    asyncio.run(channel_post_handler(new_update, context))
    assert len(new_message.replies) == 2

    first_edit_update, _ = channel_update(
        text=CN_TEXT, message_id=4303, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(first_edit_update, context))

    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    assert bot.deleted_messages == [(-2001, 5304)]
    assert reply_index.get_many(-2001, 4303) == (5303, 5304)

    second_edit_update, _ = channel_update(
        text=CN_TEXT, message_id=4303, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(second_edit_update, context))

    assert bot.deleted_messages == [(-2001, 5304), (-2001, 5304)]
    assert reply_index.get_many(-2001, 4303) == (5303,)
    assert len(calls) == 3


def test_edit_waits_for_long_original_delivery_after_first_chunk_remembered(
    monkeypatch, sample_db
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        calls = []

        async def fake_call(
            _locked_text,
            _locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return "G" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 5)
            return "edited short translation"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = make_context(sample_db)
        new_update, new_message = channel_update(text=CN_TEXT, message_id=4300)

        second_chunk_started = asyncio.Event()
        release_second_chunk = asyncio.Event()
        original_reply_text = new_message.reply_text

        async def blocking_reply_text(text: str, **kwargs):
            if len(new_message.replies) == 1:
                second_chunk_started.set()
                await release_second_chunk.wait()
            return await original_reply_text(text, **kwargs)

        new_message.reply_text = blocking_reply_text
        new_task = asyncio.create_task(channel_post_handler(new_update, context))
        await asyncio.wait_for(second_chunk_started.wait(), timeout=0.2)

        reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
        assert reply_index.get_many(-2001, 4300) == (5300,)

        edit_update, edit_message = channel_update(
            text=f"{CN_TEXT}新",
            message_id=4300,
            update_id=102,
            edit_date=datetime.now(timezone.utc),
        )
        edit_task = asyncio.create_task(channel_post_handler(edit_update, context))
        await asyncio.sleep(0.05)

        assert not edit_task.done()
        assert calls == [1]

        release_second_chunk.set()
        await asyncio.wait_for(new_task, timeout=0.2)
        await asyncio.wait_for(edit_task, timeout=0.2)

        assert edit_message.replies == []
        assert context.bot.edits == [
            ("edited short translation", "HTML", -2001, 5300)
        ]
        assert context.bot.deleted_messages == [(-2001, 5301)]
        assert reply_index.get_many(-2001, 4300) == (5300,)
        assert calls == [1, 2]

    asyncio.run(run())


def test_edit_identical_translation_is_silent_noop(monkeypatch, sample_db):
    # Telegram rejects an unchanged edit with "message is not modified"; that is
    # the dedup ideal and must be a silent no-op, never a crash.
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    bot = FakeBot(edit_raises=lambda *_: BadRequest("Message is not modified"))
    context = make_context(sample_db, bot=bot)

    new_update, _ = channel_update(text=CN_TEXT, message_id=4230)
    asyncio.run(channel_post_handler(new_update, context))

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4230, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))  # must not raise

    assert edit_message.replies == []
    assert len(bot.edit_attempts) == 1  # attempted then swallowed
    assert bot.edits == []  # nothing recorded as applied


def test_edit_invalid_html_falls_back_to_plain_edit(monkeypatch, sample_db):
    # An HTML edit Telegram rejects re-raises into the plain-edit fallback,
    # mirroring the new-post fallback — formatting never fails the update.
    def response(_locked_text, locks):
        jinhsi = placeholder_for(locks, "Jinhsi")
        echo = placeholder_for(locks, "Echo")
        return f'<b>{jinhsi}</b> says <a href="https://example.com">{echo}</a> is strong'

    calls = []
    enable_mock_llm(monkeypatch, calls, response)

    def raise_on_html(text, parse_mode, chat_id, message_id):
        return BadRequest("Can't parse entities: bad") if parse_mode == "HTML" else None

    bot = FakeBot(edit_raises=raise_on_html)
    context = make_context(sample_db, bot=bot)

    new_update, _ = channel_update(
        text=CN_TEXT, text_html=CN_TEXT_HTML, message_id=4240
    )
    asyncio.run(channel_post_handler(new_update, context))

    edit_update, edit_message = channel_update(
        text=CN_TEXT,
        text_html=CN_TEXT_HTML,
        message_id=4240,
        edit_date=datetime.now(timezone.utc),
    )
    asyncio.run(channel_post_handler(edit_update, context))

    assert edit_message.replies == []
    assert bot.edit_attempts[0][1] == "HTML"  # HTML attempted first
    assert bot.edits == [("Jinhsi says Echo is strong", None, -2001, 5240)]


def test_edit_reply_gone_is_skipped_silently(monkeypatch, sample_db):
    # If the tracked reply was deleted, the edit fails ("not found"); swallow it
    # — never crash the listener, never post a new reply.
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    bot = FakeBot(edit_raises=lambda *_: BadRequest("Message to edit not found"))
    context = make_context(sample_db, bot=bot)

    new_update, _ = channel_update(text=CN_TEXT, message_id=4250)
    asyncio.run(channel_post_handler(new_update, context))

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4250, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))  # must not raise

    assert edit_message.replies == []
    assert bot.edits == []
    assert len(bot.edit_attempts) >= 1


def test_edit_with_no_tracked_reply_consumes_zero_throttle(monkeypatch, sample_db):
    # The untracked-edit skip happens BEFORE the throttle, so it must not drain
    # the per-chat budget: a following fresh post still translates at limit=1.
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db, config=BotConfig(rate_limit_per_minute=1, owner_user_id=11)
    )

    edit_update, edit_message = channel_update(
        text=CN_TEXT, message_id=4260, edit_date=datetime.now(timezone.utc)
    )
    asyncio.run(channel_post_handler(edit_update, context))
    assert edit_message.replies == []
    assert context.bot.edits == []
    assert calls == []

    fresh_update, fresh_message = channel_update(text=CN_TEXT, message_id=4261)
    asyncio.run(channel_post_handler(fresh_update, context))
    assert fresh_message.replies == [("translated", "HTML", 4261)]
    assert len(calls) == 1


def test_channel_reply_index_remembers_gets_and_expires():
    index = ChannelReplyIndex(ttl_seconds=300.0)
    index.remember(-2001, 4001, 5001, now=0.0)

    assert index.get(-2001, 4001, now=299.9) == 5001
    assert index.get(-2001, 4001, now=300.0) is None  # expired at TTL
    assert index.get(-2001, 9999, now=0.0) is None  # unknown key


def test_channel_reply_index_persists_and_loads_with_wall_clock_ttl(tmp_path):
    now = 1000.0

    def clock():
        return now

    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=path, clock=clock)
    index.remember_many(-2001, 4001, (5001, 5002))

    reloaded = ChannelReplyIndex(ttl_seconds=60.0, storage_path=path, clock=clock)
    assert reloaded.get_many(-2001, 4001) == (5001, 5002)

    now = 1060.0
    assert reloaded.get_many(-2001, 4001) == ()

    expired = ChannelReplyIndex(ttl_seconds=60.0, storage_path=path, clock=clock)
    assert expired.get_many(-2001, 4001) == ()


def test_channel_reply_index_expired_read_does_not_rewrite_storage(tmp_path):
    now = 1000.0

    def clock():
        return now

    path = tmp_path / "channel_replies.json"
    index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=path, clock=clock)
    index.remember_many(-2001, 4001, (5001,))
    persisted = path.read_text(encoding="utf-8")

    now = 1060.0
    assert index.get_many(-2001, 4001) == ()
    assert path.read_text(encoding="utf-8") == persisted


def test_channel_reply_index_ignores_corrupt_persistence_file(tmp_path, caplog):
    path = tmp_path / "channel_replies.json"
    path.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=path)

    assert index.entry_count() == 0
    assert "channel reply index unreadable" in caplog.text
    assert "-2001" not in caplog.text


def test_channel_reply_index_save_failure_does_not_block_memory(tmp_path, caplog):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("x", encoding="utf-8")
    path = blocked_parent / "channel_replies.json"
    index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=path)

    with caplog.at_level(logging.WARNING):
        index.remember_many(-2001, 4001, (5001,))

    assert index.get_many(-2001, 4001) == (5001,)
    assert index.persistence_enabled() is True
    assert index.save_failure_count() == 1
    assert index.last_save_succeeded() is False
    assert "channel reply index save failed" in caplog.text
    assert "-2001" not in caplog.text
