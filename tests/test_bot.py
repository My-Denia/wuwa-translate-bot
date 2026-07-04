from __future__ import annotations

import asyncio
import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from telegram import Update, User
from telegram.error import BadRequest, TelegramError
from telegram.ext import ChatMemberHandler, CommandHandler, MessageHandler

from wuwaterm.bot import (
    ADMIN_CACHE_KEY,
    CHANNEL_REPLY_INDEX_KEY,
    CHAT_SETTINGS_KEY,
    CONFIG_KEY,
    DEFAULT_GROUP_TR_REJECT_TEXT,
    DEFAULT_PRIVATE_TR_REJECT_TEXT,
    LLM_INPUT_CHAR_LIMIT,
    PUBLIC_DISABLED_NOTICE,
    PUBLIC_ENABLED_NOTICE,
    PUBLIC_ONLY_GROUPS_NOTICE,
    PUBLIC_REJECT_NOTICE,
    SETTINGS_SAVE_FAILED_NOTICE,
    PUBLIC_STATUS_OFF,
    PUBLIC_STATUS_ON,
    PUBLIC_USAGE_NOTICE,
    RATE_LIMITER_KEY,
    REJECT_LIMITER_KEY,
    SENTENCE_USAGE_NOTICE,
    SERVICE_KEY,
    TERM_USAGE_NOTICE,
    TELEGRAM_TEXT_MESSAGE_LIMIT,
    THROTTLE_NOTICE,
    TRANSLATOR_KEY,
    UNAUTHORIZED_GROUP_NOTICE,
    AdminStatusCache,
    BotConfig,
    ChannelReplyIndex,
    PerChatRateLimiter,
    about_command,
    authorize_command,
    create_application,
    my_chat_member_handler,
    public_command,
    revoke_command,
    sentence_command,
    status_command,
    term_command,
    translate_query_async,
)
from wuwaterm.lookup import TermService
from wuwaterm.sentence import (
    BUDGET_EXHAUSTED_NOTICE,
    TRANSLATION_UNAVAILABLE_NOTICE,
    SentenceTranslator,
    _llm_error_from_response,
)
from wuwaterm.settings import ChatSettings
from wuwaterm.telegram_text import telegram_text_units


class FakeMessage:
    def __init__(
        self,
        message_id: int = 101,
        sender_chat_id: int | None = None,
        reply_raises=None,
    ):
        self.message_id = message_id
        self.sender_chat = (
            SimpleNamespace(id=sender_chat_id) if sender_chat_id is not None else None
        )
        self.replies: list[tuple[str, int | None]] = []
        self.reply_kwargs: list[dict] = []
        self._reply_raises = reply_raises

    async def reply_text(self, text: str, **kwargs) -> None:
        if telegram_text_units(text) > TELEGRAM_TEXT_MESSAGE_LIMIT:
            raise TelegramError("Message is too long")
        self.reply_kwargs.append(dict(kwargs))
        if self._reply_raises is not None:
            exc = self._reply_raises(text, kwargs, len(self.reply_kwargs))
            if exc is not None:
                raise exc
        self.replies.append((text, kwargs.get("reply_to_message_id")))
        return SimpleNamespace(message_id=self.message_id + len(self.replies))


class FakeBot:
    def __init__(self, default_status: str = "member", overrides=None):
        self.default_status = default_status
        self.overrides = dict(overrides or {})
        self.member_calls: list[tuple[int, int]] = []
        self.sent_messages: list[tuple[int, str]] = []
        self.left_chats: list[int] = []

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.member_calls.append((chat_id, user_id))
        outcome = self.overrides.get((chat_id, user_id), self.default_status)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(status=outcome)

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent_messages.append((chat_id, text))
        return SimpleNamespace(message_id=999)

    async def leave_chat(self, chat_id: int):
        self.left_chats.append(chat_id)
        return True


def fake_update(
    chat_id: int = 1,
    chat_type: str = "private",
    message_id: int = 101,
    user_id: int | None = 11,
    sender_chat_id: int | None = None,
    reply_to=None,
    reply_raises=None,
):
    message = FakeMessage(
        message_id=message_id,
        sender_chat_id=sender_chat_id,
        reply_raises=reply_raises,
    )
    if reply_to is not None:
        message.reply_to_message = reply_to
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return (
        SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=user),
        message,
    )


def fake_member_update(
    *,
    chat_id: int = -2001,
    chat_type: str = "supergroup",
    old_status: str = "left",
    new_status: str = "member",
    from_id: int | None = 11,
    title: str = "g",
    old_is_member: bool | None = None,
    new_is_member: bool | None = None,
):
    """Build a my_chat_member update (the bot's OWN membership change).

    old_is_member / new_is_member set the ChatMemberRestricted.is_member flag
    when given (None = attribute absent, as for non-restricted statuses)."""
    chat = SimpleNamespace(id=chat_id, type=chat_type, title=title)
    from_user = SimpleNamespace(id=from_id) if from_id is not None else None
    old_member = SimpleNamespace(status=old_status)
    new_member = SimpleNamespace(status=new_status)
    if old_is_member is not None:
        old_member.is_member = old_is_member
    if new_is_member is not None:
        new_member.is_member = new_is_member
    cmu = SimpleNamespace(
        old_chat_member=old_member,
        new_chat_member=new_member,
        from_user=from_user,
        chat=chat,
    )
    return SimpleNamespace(
        my_chat_member=cmu,
        effective_chat=chat,
        effective_message=None,
        effective_user=from_user,
    )


def fake_context(
    sample_db,
    args,
    *,
    limit=10,
    member_status="administrator",
    member_overrides=None,
    config=None,
    chat_settings=None,
    allowlist=(-2001, -2002, -2003),
):
    config = config or BotConfig(rate_limit_per_minute=limit, owner_user_id=11)
    bot = FakeBot(default_status=member_status, overrides=member_overrides)
    if chat_settings is None:
        # Each test gets its own settings file under sample_db's per-test tmp dir.
        # The bot only serves allowlisted groups, so the common test group ids are
        # authorized by default; pass allowlist=() to exercise the unauthorized path.
        chat_settings = ChatSettings(sample_db.parent / "chat_settings.json")
        for _cid in allowlist:
            chat_settings.allow(_cid)
    return SimpleNamespace(
        args=args,
        bot=bot,
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
        calls.append((locked_text, locks))
        return response_factory(locked_text, locks)

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)


def placeholder_for(locks, official):
    for placeholder, _source, en in locks:
        if en == official:
            return placeholder
    raise AssertionError(f"missing official lock {official}")


def test_term_command_uses_db_first(sample_db):
    update, message = fake_update()
    context = fake_context(sample_db, ["声骸"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", None)]


def test_sentence_command_locks_terms_without_llm(monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    update, message = fake_update()
    context = fake_context(sample_db, ["今汐装备了声骸"])

    asyncio.run(sentence_command(update, context))

    assert message.replies == [("Jinhsi装备了Echo", None)]


def test_tr_non_exact_text_uses_locked_llm(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        return f"{placeholder_for(locks, 'Jinhsi')} says {placeholder_for(locks, 'Echo')} is strong."

    enable_mock_llm(monkeypatch, calls, response)
    update, message = fake_update()
    context = fake_context(sample_db, ["今汐说声骸很强"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Jinhsi says Echo is strong.", None)]
    assert len(calls) == 1
    locked_text, locks = calls[0]
    assert "今汐" not in locked_text
    assert "声骸" not in locked_text
    assert {lock[2] for lock in locks} >= {"Jinhsi", "Echo"}


def test_group_term_replies_to_asking_message(sample_db):
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=555)
    context = fake_context(sample_db, ["声骸"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", 555)]


def test_group_reply_falls_back_when_reply_target_is_missing(sample_db, caplog):
    def fail_initial_reply(_text, kwargs, call_number):
        if call_number == 1:
            assert kwargs == {"reply_to_message_id": 561}
            return BadRequest("Message to be replied not found")
        return None

    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=561,
        reply_raises=fail_initial_reply,
    )
    context = fake_context(sample_db, ["声骸"])

    with caplog.at_level(logging.WARNING, logger="wuwaterm.bot"):
        asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", None)]
    assert message.reply_kwargs == [
        {"reply_to_message_id": 561},
        {"do_quote": False},
    ]
    assert any("reply target missing" in record.getMessage() for record in caplog.records)


def test_group_reply_non_missing_bad_request_still_fails(sample_db):
    def fail_initial_reply(_text, _kwargs, call_number):
        if call_number == 1:
            return BadRequest("Message is too long")
        return None

    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=562,
        reply_raises=fail_initial_reply,
    )
    context = fake_context(sample_db, ["声骸"])

    with pytest.raises(BadRequest, match="Message is too long"):
        asyncio.run(term_command(update, context))

    assert message.replies == []
    assert message.reply_kwargs == [{"reply_to_message_id": 562}]


def test_group_html_reply_missing_target_then_parse_error_falls_back_plain(
    monkeypatch, sample_db
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def fake_call(
        _locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=45.0,
        transport=None,
    ):
        assert html_mode is True
        return f"<b>{placeholder_for(locks, 'Jinhsi')}</b> says hi"

    def fail_then_parse_error(_text, kwargs, call_number):
        if call_number == 1:
            assert kwargs == {"reply_to_message_id": 563, "parse_mode": "HTML"}
            return BadRequest("Message to be replied not found")
        if call_number == 2:
            assert kwargs == {"do_quote": False, "parse_mode": "HTML"}
            return BadRequest("Can't parse entities: bad")
        assert kwargs == {"do_quote": False}
        return None

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    replied = SimpleNamespace(
        text="今汐说你好",
        caption=None,
        text_html="<b>今汐</b>说你好",
        caption_html=None,
        entities=[SimpleNamespace(type="bold")],
    )
    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=563,
        reply_to=replied,
        reply_raises=fail_then_parse_error,
    )
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Jinhsi says hi", None)]
    assert message.reply_kwargs == [
        {"reply_to_message_id": 563, "parse_mode": "HTML"},
        {"do_quote": False, "parse_mode": "HTML"},
        {"do_quote": False},
    ]


def test_group_tr_admin_sentence_uses_llm(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        return f"{placeholder_for(locks, 'Jinhsi')} equips {placeholder_for(locks, 'Echo')}."

    enable_mock_llm(monkeypatch, calls, response)
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=556)
    context = fake_context(sample_db, ["今汐装备了声骸"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Jinhsi equips Echo.", 556)]
    assert len(calls) == 1


def test_short_dictionary_miss_appends_bilingual_flag_without_hash(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "Unlisted term")
    update, message = fake_update()
    context = fake_context(sample_db, ["不存在词条"])

    asyncio.run(term_command(update, context))

    assert message.replies == [
        (
            "Unlisted term\n\n"
            "(词典外,机器直译)\n"
            "(Not in official dictionary; machine-translated)",
            None,
        )
    ]
    # The 40-char commit hash must never reach a user-facing reply.
    assert re.search(r"[0-9a-f]{40}", message.replies[0][0]) is None
    assert len(calls) == 1


def test_llm_path_allows_2000_chars(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    update, message = fake_update()
    context = fake_context(sample_db, ["测" * LLM_INPUT_CHAR_LIMIT])

    asyncio.run(term_command(update, context))

    assert LLM_INPUT_CHAR_LIMIT == 2000
    assert message.replies == [("translated", None)]
    assert len(calls) == 1


def test_llm_output_over_telegram_limit_is_split(monkeypatch, sample_db):
    calls = []
    translated = "A" * (TELEGRAM_TEXT_MESSAGE_LIMIT + 17)
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: translated)
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=559)
    context = fake_context(sample_db, ["测" * LLM_INPUT_CHAR_LIMIT])

    asyncio.run(term_command(update, context))

    assert message.replies == [
        (translated[:TELEGRAM_TEXT_MESSAGE_LIMIT], 559),
        (translated[TELEGRAM_TEXT_MESSAGE_LIMIT:], None),
    ]
    assert message.reply_kwargs == [
        {"reply_to_message_id": 559},
        {"do_quote": False},
    ]
    assert all(len(reply) <= TELEGRAM_TEXT_MESSAGE_LIMIT for reply, _ in message.replies)
    assert "".join(reply for reply, _ in message.replies) == translated
    assert len(calls) == 1


def test_llm_output_split_counts_emoji_as_utf16_units(monkeypatch, sample_db):
    calls = []
    translated = "😀" * (TELEGRAM_TEXT_MESSAGE_LIMIT // 2 + 3)
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: translated)
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=560)
    context = fake_context(sample_db, ["测" * LLM_INPUT_CHAR_LIMIT])

    asyncio.run(term_command(update, context))

    assert [telegram_text_units(reply) for reply, _ in message.replies] == [
        TELEGRAM_TEXT_MESSAGE_LIMIT,
        6,
    ]
    assert message.replies[0][1] == 560
    assert message.replies[1][1] is None
    assert "".join(reply for reply, _ in message.replies) == translated
    assert len(calls) == 1


def test_llm_path_rejects_over_2000_chars_before_call(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update()
    context = fake_context(sample_db, ["测" * (LLM_INPUT_CHAR_LIMIT + 1)])

    asyncio.run(term_command(update, context))

    assert LLM_INPUT_CHAR_LIMIT == 2000
    assert message.replies == [
        (f"Input is too long for translation ({LLM_INPUT_CHAR_LIMIT} character limit).", None)
    ]
    assert calls == []


@pytest.mark.parametrize("llm_output", ["   ", "\n\n"])
def test_term_command_blank_llm_output_returns_unavailable_notice(
    monkeypatch, sample_db, llm_output
):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: llm_output)
    update, message = fake_update()
    context = fake_context(
        sample_db, ["这是一个需要翻译的完整句子，请翻译一下。"]
    )

    asyncio.run(term_command(update, context))

    assert message.replies == [(TRANSLATION_UNAVAILABLE_NOTICE, None)]
    assert len(calls) == 1


def test_term_command_budget_exhaustion_returns_clean_bot_reply(monkeypatch, sample_db):
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
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=558)
    context = fake_context(sample_db, ["这是一个需要翻译的句子。"])

    asyncio.run(term_command(update, context))

    assert message.replies == [(BUDGET_EXHAUSTED_NOTICE, 558)]
    reply = message.replies[0][0]
    assert reply == (
        "本月翻译额度已用完,请稍后再试。\n"
        "This month's translation quota is used up. Please try again later."
    )
    assert "429" not in reply
    assert "max_budget" not in reply
    assert "exceeded" not in reply


def test_screenshot_noise_speaker_prefix_and_quote_bars_are_normalized(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        return (
            f"{placeholder_for(locks, 'Lucilla')}: "
            f"{placeholder_for(locks, 'Cartethyia')} found {placeholder_for(locks, 'Echo')}."
        )

    enable_mock_llm(monkeypatch, calls, response)
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=557)
    context = fake_context(
        sample_db,
        ["(WW 3.4)", "[spolier]", "> 洛瑟菈: Ｃａｒｔｅｔｈｙｉａ发现了声骸"],
    )

    asyncio.run(term_command(update, context))

    assert message.replies == [("Lucilla: Cartethyia found Echo.", 557)]
    locked_text, locks = calls[0]
    assert "WW 3.4" not in locked_text
    assert "spolier" not in locked_text.casefold()
    assert ">" not in locked_text
    assert "洛瑟菈" not in locked_text
    assert {lock[2] for lock in locks} >= {"Lucilla", "Cartethyia", "Echo"}


def test_spoiler_spelling_and_version_tag_are_stripped(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, _locks: locked_text)
    update, message = fake_update()
    context = fake_context(sample_db, ["[spoiler]", "(WW 3.4)", "这是一个测试。"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("这是一个测试。", None)]
    assert calls == [("这是一个测试。", ())]


def test_stylized_unicode_cartethyia_matches_ascii(sample_db):
    update, message = fake_update()
    context = fake_context(sample_db, ["Ｃａｒｔｅｔｈｙｉａ"])

    asyncio.run(term_command(update, context))

    # Stylized fullwidth English normalizes to "Cartethyia" and matches the
    # term's English side; an English query now auto-translates to the
    # official Chinese (EN->ZH direction).
    assert message.replies == [("卡提希娅", None)]


def test_tr_english_term_returns_official_chinese(sample_db):
    update, message = fake_update()
    context = fake_context(sample_db, ["Echo"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("声骸", None)]


def test_tr_english_sentence_translates_to_chinese(monkeypatch, sample_db):
    calls = []

    def response(_locked_text, locks):
        return f"{placeholder_for(locks, 'Jinhsi')}装备了{placeholder_for(locks, 'Echo')}"

    enable_mock_llm(monkeypatch, calls, response)
    update, message = fake_update()
    context = fake_context(sample_db, ["Jinhsi equips Echo"])

    asyncio.run(term_command(update, context))

    assert message.replies == [("今汐装备了声骸", None)]
    assert len(calls) == 1


def test_slow_llm_does_not_block_control_commands_or_other_chat(
    monkeypatch, sample_db
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_call(
            _locked_text,
            locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            started.set()
            await release.wait()
            return f"{placeholder_for(locks, 'Jinhsi')} says {placeholder_for(locks, 'Echo')}"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = fake_context(sample_db, ["今汐说声骸很强"], limit=10)
        slow_update, slow_message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=4300
        )
        slow_task = asyncio.create_task(term_command(slow_update, context))
        await asyncio.wait_for(started.wait(), timeout=0.2)

        context.args = ["off"]
        public_update, public_message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=4301
        )
        await asyncio.wait_for(public_command(public_update, context), timeout=0.05)
        assert public_message.replies == [(PUBLIC_DISABLED_NOTICE, 4301)]

        context.args = ["-2999"]
        authorize_update, authorize_message = fake_update(
            chat_id=1, chat_type="private", message_id=4302, user_id=11
        )
        await asyncio.wait_for(
            authorize_command(authorize_update, context), timeout=0.05
        )
        assert authorize_message.replies == [("已授权 / Authorized chat_id=-2999", None)]

        context.args = ["-2999"]
        revoke_update, revoke_message = fake_update(
            chat_id=1, chat_type="private", message_id=4303, user_id=11
        )
        await asyncio.wait_for(revoke_command(revoke_update, context), timeout=0.05)
        assert revoke_message.replies == [
            ("已撤销并退出 / Revoked and left chat_id=-2999", None)
        ]
        assert context.bot.left_chats == [-2999]

        context.args = ["声骸"]
        other_update, other_message = fake_update(
            chat_id=-2002, chat_type="supergroup", message_id=4304
        )
        await asyncio.wait_for(term_command(other_update, context), timeout=0.05)
        assert other_message.replies == [("Echo", 4304)]
        assert slow_message.replies == []

        release.set()
        await asyncio.wait_for(slow_task, timeout=0.2)
        assert slow_message.replies == [("Jinhsi says Echo", 4300)]

    asyncio.run(run())


def test_public_mode_reply_is_skipped_if_public_closes_during_llm(
    monkeypatch, sample_db
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_call(
            _locked_text,
            locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            started.set()
            await release.wait()
            return f"{placeholder_for(locks, 'Jinhsi')} says {placeholder_for(locks, 'Echo')}"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = fake_context(
            sample_db,
            ["今汐说声骸很强"],
            member_status="administrator",
            member_overrides={(-2001, 22): "member"},
        )
        settings = context.application.bot_data[CHAT_SETTINGS_KEY]
        settings.set_public(-2001, True)
        slow_update, slow_message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=4310, user_id=22
        )
        slow_task = asyncio.create_task(term_command(slow_update, context))
        await asyncio.wait_for(started.wait(), timeout=0.2)

        context.args = ["off"]
        public_update, public_message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=4311, user_id=11
        )
        await asyncio.wait_for(public_command(public_update, context), timeout=0.05)
        assert public_message.replies == [(PUBLIC_DISABLED_NOTICE, 4311)]

        release.set()
        await asyncio.wait_for(slow_task, timeout=0.2)

        assert slow_message.replies == []

    asyncio.run(run())


def test_group_reply_is_skipped_if_chat_revoked_during_llm(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_call(
            _locked_text,
            locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            started.set()
            await release.wait()
            return f"{placeholder_for(locks, 'Jinhsi')} says {placeholder_for(locks, 'Echo')}"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        context = fake_context(sample_db, ["今汐说声骸很强"])
        slow_update, slow_message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=4320, user_id=11
        )
        slow_task = asyncio.create_task(term_command(slow_update, context))
        await asyncio.wait_for(started.wait(), timeout=0.2)

        context.args = ["-2001"]
        revoke_update, revoke_message = fake_update(
            chat_id=1, chat_type="private", message_id=4321, user_id=11
        )
        await asyncio.wait_for(revoke_command(revoke_update, context), timeout=0.05)
        assert revoke_message.replies == [
            ("已撤销并退出 / Revoked and left chat_id=-2001", None)
        ]

        release.set()
        await asyncio.wait_for(slow_task, timeout=0.2)

        assert slow_message.replies == []

    asyncio.run(run())


def test_llm_concurrency_limit_bounds_inflight_calls(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        in_flight = 0
        max_in_flight = 0

        async def fake_call(
            _locked_text,
            locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.02)
                return (
                    f"{placeholder_for(locks, 'Jinhsi')} says "
                    f"{placeholder_for(locks, 'Echo')}"
                )
            finally:
                in_flight -= 1

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        translator = SentenceTranslator(sample_db, llm_max_concurrency=2)
        service = TermService(sample_db)
        tasks = [
            asyncio.create_task(
                translate_query_async(service, translator, "今汐说声骸很强")
            )
            for _ in range(5)
        ]

        results = await asyncio.gather(*tasks)

        assert results == ["Jinhsi says Echo"] * 5
        assert max_in_flight == 2

    asyncio.run(run())


def test_rate_limit_is_per_chat(sample_db):
    context = fake_context(sample_db, ["声骸"], limit=10)
    for idx in range(10):
        update, _message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=600 + idx)
        asyncio.run(term_command(update, context))

    throttled_update, throttled_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=700
    )
    asyncio.run(term_command(throttled_update, context))

    other_update, other_message = fake_update(chat_id=-2002, chat_type="supergroup", message_id=800)
    asyncio.run(term_command(other_update, context))

    assert throttled_message.replies == [(THROTTLE_NOTICE, 700)]
    assert throttled_message.replies[0][0] == (
        "本群消息过于频繁，请一分钟后再试。\n"
        "Rate limit reached for this chat. Try again in a minute."
    )
    assert other_message.replies == [("Echo", 800)]


def test_commandhandler_accepts_bot_username(sample_db):
    app = create_application("123:ABC", sample_db, config=BotConfig())
    app.bot._bot_user = User(id=123, first_name="WuWa", is_bot=True, username="WuWaTermBot")
    handler = next(
        handler
        for group in app.handlers.values()
        for handler in group
        if isinstance(handler, CommandHandler)
    )
    update = Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": -2001, "type": "supergroup", "title": "g"},
                "text": "/tr@WuWaTermBot 声骸",
                "entities": [{"type": "bot_command", "offset": 0, "length": 15}],
            },
        },
        app.bot,
    )

    assert handler.check_update(update)


def test_create_application_wires_llm_timeout(sample_db):
    app = create_application(
        "123:ABC", sample_db, config=BotConfig(llm_timeout_seconds=12.5)
    )

    translator = app.bot_data[TRANSLATOR_KEY]

    assert translator.llm_timeout_seconds == 12.5


def test_handler_set_is_exactly_commands_plus_channel_listener(sample_db):
    """Pin evolution (deliberate): seven command handlers (/tr+/term,
    /sentence+/sent, /about, /status, /public, /authorize, /revoke), exactly one passive
    channel listener (the linked-channel hard boundary), and exactly one
    MY_CHAT_MEMBER handler (the group-authorization gate)."""
    app = create_application(
        "123:ABC",
        sample_db,
        config=BotConfig(),
        chat_settings=ChatSettings(sample_db.parent / "chat_settings.json"),
    )
    handlers = [handler for group in app.handlers.values() for handler in group]
    command_handlers = [h for h in handlers if isinstance(h, CommandHandler)]
    message_handlers = [h for h in handlers if isinstance(h, MessageHandler)]

    chat_member_handlers = [h for h in handlers if isinstance(h, ChatMemberHandler)]
    assert len(handlers) == 9  # 7 command + 1 message + 1 chat-member
    assert len(command_handlers) == 7
    assert len(message_handlers) == 1
    assert len(chat_member_handlers) == 1
    # Repr verified against the installed PTB in this venv.
    assert str(message_handlers[0].filters) == (
        "<filters.IS_AUTOMATIC_FORWARD and filters.SenderChat.CHANNEL>"
    )


def test_data_plane_handlers_are_non_blocking_control_handlers_block(sample_db):
    app = create_application(
        "123:ABC",
        sample_db,
        config=BotConfig(),
        chat_settings=ChatSettings(sample_db.parent / "chat_settings.json"),
    )
    handlers = [handler for group in app.handlers.values() for handler in group]
    command_handlers = [h for h in handlers if isinstance(h, CommandHandler)]
    message_handlers = [h for h in handlers if isinstance(h, MessageHandler)]

    by_commands = {frozenset(handler.commands): handler for handler in command_handlers}

    assert by_commands[frozenset({"tr", "term"})].block is False
    assert by_commands[frozenset({"sentence", "sent"})].block is False
    assert message_handlers[0].block is False
    assert bool(by_commands[frozenset({"public"})].block) is True
    assert bool(by_commands[frozenset({"status"})].block) is True
    assert bool(by_commands[frozenset({"authorize"})].block) is True
    assert bool(by_commands[frozenset({"revoke"})].block) is True


def test_ordinary_member_message_never_triggers_any_handler(sample_db):
    app = create_application("123:ABC", sample_db, config=BotConfig())
    app.bot._bot_user = User(id=123, first_name="WuWa", is_bot=True, username="WuWaTermBot")
    update = Update.de_json(
        {
            "update_id": 2,
            "message": {
                "message_id": 50,
                "date": 1,
                "chat": {"id": -2001, "type": "supergroup", "title": "g"},
                "from": {"id": 42, "is_bot": False, "first_name": "member"},
                "text": "今汐说声骸很强",
            },
        },
        app.bot,
    )

    matched = [
        handler
        for group in app.handlers.values()
        for handler in group
        if handler.check_update(update)
    ]

    assert matched == []


def test_automatic_channel_forward_matches_only_the_message_handler(sample_db):
    app = create_application("123:ABC", sample_db, config=BotConfig())
    app.bot._bot_user = User(id=123, first_name="WuWa", is_bot=True, username="WuWaTermBot")
    update = Update.de_json(
        {
            "update_id": 3,
            "message": {
                "message_id": 51,
                "date": 1,
                "chat": {"id": -2001, "type": "supergroup", "title": "g"},
                "from": {"id": 777000, "is_bot": False, "first_name": "Telegram"},
                "sender_chat": {"id": -1003001, "type": "channel", "title": "c"},
                "is_automatic_forward": True,
                "text": "公告：今汐上线",
            },
        },
        app.bot,
    )

    matched = [
        handler
        for group in app.handlers.values()
        for handler in group
        if handler.check_update(update)
    ]

    assert len(matched) == 1
    assert isinstance(matched[0], MessageHandler)


def test_group_tr_member_gets_one_line_rejection_and_zero_llm(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=900)
    context = fake_context(sample_db, ["今汐说声骸很强"], member_status="member")

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 900)]
    assert message.replies[0][0] == "仅群管理员可用 /tr\nOnly group admins can use /tr"
    assert calls == []
    assert context.bot.member_calls == [(-2001, 11)]


@pytest.mark.parametrize("status", ["restricted", "left", "kicked"])
def test_group_tr_non_admin_statuses_are_rejected(status, sample_db):
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=901)
    context = fake_context(sample_db, ["声骸"], member_status=status)

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 901)]


@pytest.mark.parametrize("status", ["creator", "administrator"])
def test_group_tr_admin_statuses_translate(status, sample_db):
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=902)
    context = fake_context(sample_db, ["声骸"], member_status=status)

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", 902)]


def test_private_tr_owner_translates_without_member_lookup(sample_db):
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(sample_db, ["声骸"], member_status="member")

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", None)]
    assert context.bot.member_calls == []


def test_group_tr_anonymous_admin_translates_without_member_lookup(sample_db):
    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=903,
        user_id=None,
        sender_chat_id=-2001,
    )
    context = fake_context(sample_db, ["声骸"], member_status="member")

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", 903)]
    assert context.bot.member_calls == []


def test_group_tr_linked_channel_sender_is_rejected(sample_db):
    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=904,
        user_id=None,
        sender_chat_id=-3001,
    )
    context = fake_context(sample_db, ["声骸"], member_status="administrator")

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 904)]
    assert context.bot.member_calls == []


def test_group_tr_member_verdict_is_cached_across_calls(sample_db):
    context = fake_context(sample_db, ["声骸"], member_status="member")
    first_update, first_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=905
    )
    second_update, second_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=906
    )

    asyncio.run(term_command(first_update, context))
    asyncio.run(term_command(second_update, context))

    assert first_message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 905)]
    assert second_message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 906)]
    assert context.bot.member_calls == [(-2001, 11)]


def test_admin_status_cache_expires_after_ttl():
    cache = AdminStatusCache(ttl_seconds=300.0)
    cache.put(-2001, 11, False, now=0.0)
    cache.put(-2001, 12, True, now=0.0)

    assert cache.get(-2001, 11, now=299.9) is False
    assert cache.get(-2001, 12, now=299.9) is True
    assert cache.get(-2001, 11, now=300.0) is None
    assert cache.get(-2001, 99, now=0.0) is None


def test_group_tr_silent_flag_suppresses_rejection_reply(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=907)
    context = fake_context(
        sample_db,
        ["今汐说声骸很强"],
        member_status="member",
        config=BotConfig(rate_limit_per_minute=10, tr_reject_silent=True, owner_user_id=11),
    )

    asyncio.run(term_command(update, context))

    assert message.replies == []
    assert calls == []


def test_group_tr_rejection_text_is_configurable(sample_db):
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=908)
    context = fake_context(
        sample_db,
        ["声骸"],
        member_status="member",
        config=BotConfig(rate_limit_per_minute=10, group_tr_reject_text="管理员专用。"),
    )

    asyncio.run(term_command(update, context))

    assert message.replies == [("管理员专用。", 908)]


def test_group_tr_reject_replies_are_capped_then_silent(sample_db):
    # Rejections ride their own per-chat budget; beyond it they go silent and
    # never emit THROTTLE_NOTICE (which would leak throttle state to non-admins).
    context = fake_context(sample_db, ["声骸"], limit=2, member_status="member")
    replies = []
    for idx in range(3):
        update, message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=909 + idx
        )
        asyncio.run(term_command(update, context))
        replies.append(message.replies)

    assert replies[0] == [(DEFAULT_GROUP_TR_REJECT_TEXT, 909)]
    assert replies[1] == [(DEFAULT_GROUP_TR_REJECT_TEXT, 910)]
    assert replies[2] == []
    assert context.bot.member_calls == [(-2001, 11)]


def test_group_nonadmin_spam_does_not_starve_admin(sample_db):
    # A non-admin floods rejected /tr past the limit; an admin in the SAME chat
    # must still translate. On the old throttle-before-auth code the admin would
    # have been throttled by the non-admin's rejections draining the one bucket.
    context = fake_context(
        sample_db,
        ["声骸"],
        limit=2,
        member_status="administrator",
        member_overrides={(-2001, 22): "member"},
    )
    spam_replies = []
    for idx in range(3):  # limit + 1: drains the reject budget
        update, message = fake_update(
            chat_id=-2001, chat_type="supergroup", message_id=950 + idx, user_id=22
        )
        asyncio.run(term_command(update, context))
        spam_replies.append(message.replies)

    # Reject budget (=2) exhausted by the 3rd call: silent, never THROTTLE_NOTICE.
    assert spam_replies[2] == []

    admin_update, admin_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=960, user_id=11
    )
    asyncio.run(term_command(admin_update, context))
    assert admin_message.replies == [("Echo", 960)]


def test_group_tr_member_lookup_failure_fails_closed_and_uncached(sample_db):
    context = fake_context(
        sample_db,
        ["声骸"],
        member_overrides={(-2001, 11): TelegramError("temporarily unavailable")},
    )
    first_update, first_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=912
    )
    second_update, second_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=913
    )

    asyncio.run(term_command(first_update, context))
    asyncio.run(term_command(second_update, context))

    assert first_message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 912)]
    assert second_message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 913)]
    assert context.bot.member_calls == [(-2001, 11), (-2001, 11)]


def test_group_sentence_member_gets_one_line_rejection_and_zero_llm(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=914)
    context = fake_context(sample_db, ["今汐说声骸很强"], member_status="member")

    asyncio.run(sentence_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 914)]
    assert calls == []
    assert context.bot.member_calls == [(-2001, 11)]


@pytest.mark.parametrize("status", ["creator", "administrator"])
def test_group_sentence_admin_statuses_translate(status, monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=915)
    context = fake_context(sample_db, ["今汐装备了声骸"], member_status=status)

    asyncio.run(sentence_command(update, context))

    assert message.replies == [("Jinhsi装备了Echo", 915)]


def test_group_sentence_anonymous_admin_translates_without_member_lookup(
    monkeypatch, sample_db
):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=916,
        user_id=None,
        sender_chat_id=-2001,
    )
    context = fake_context(sample_db, ["今汐装备了声骸"], member_status="member")

    asyncio.run(sentence_command(update, context))

    assert message.replies == [("Jinhsi装备了Echo", 916)]
    assert context.bot.member_calls == []


def test_private_sentence_owner_translates_without_member_lookup(monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(sample_db, ["今汐装备了声骸"], member_status="member")

    asyncio.run(sentence_command(update, context))

    assert message.replies == [("Jinhsi装备了Echo", None)]
    assert context.bot.member_calls == []


def test_private_sentence_rejects_everyone_when_owner_unset(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(
        sample_db,
        ["今汐说声骸很强"],
        config=BotConfig(rate_limit_per_minute=10, owner_user_id=None),
    )

    asyncio.run(sentence_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert calls == []
    assert context.bot.member_calls == []


@pytest.mark.parametrize("user_id", [22, 11])
def test_channel_sentence_is_rejected_even_for_owner(user_id, sample_db):
    update, message = fake_update(
        chat_id=-5005, chat_type="channel", message_id=917, user_id=user_id
    )
    context = fake_context(sample_db, ["声骸"])

    asyncio.run(sentence_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert context.bot.member_calls == []


def test_private_sentence_reject_replies_are_capped_then_silent(sample_db):
    context = fake_context(
        sample_db,
        ["声骸"],
        config=BotConfig(rate_limit_per_minute=2, owner_user_id=11),
    )
    replies = []
    for idx in range(3):
        update, message = fake_update(
            chat_id=2, chat_type="private", user_id=22, message_id=940 + idx
        )
        asyncio.run(sentence_command(update, context))
        replies.append(message.replies)

    assert replies[0] == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert replies[1] == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert replies[2] == []
    assert context.bot.member_calls == []


def test_private_tr_stranger_gets_one_line_rejection_and_zero_llm(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=2, chat_type="private", user_id=22)
    context = fake_context(sample_db, ["今汐说声骸很强"])

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert message.replies[0][0] == (
        "此 bot 仅限群内由管理员使用\n"
        "This bot can only be used by admins inside a group."
    )
    assert calls == []
    assert context.bot.member_calls == []


def test_private_tr_rejects_everyone_when_owner_unset(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(
        sample_db,
        ["今汐说声骸很强"],
        config=BotConfig(rate_limit_per_minute=10, owner_user_id=None),
    )

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert calls == []
    assert context.bot.member_calls == []


@pytest.mark.parametrize("raw", [None, "", "   ", "not-an-int"])
def test_from_env_owner_missing_or_invalid_warns_once_without_digits(
    raw, monkeypatch, caplog
):
    monkeypatch.delenv("WUWATERM_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.delenv("WUWATERM_GROUP_TR_REJECT_TEXT", raising=False)
    monkeypatch.delenv("WUWATERM_PRIVATE_TR_REJECT_TEXT", raising=False)
    monkeypatch.delenv("WUWATERM_TR_REJECT_SILENT", raising=False)
    if raw is None:
        monkeypatch.delenv("OWNER_USER_ID", raising=False)
    else:
        monkeypatch.setenv("OWNER_USER_ID", raw)

    with caplog.at_level(logging.WARNING, logger="wuwaterm.bot"):
        config = BotConfig.from_env()

    assert config.owner_user_id is None
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "private /tr will reject everyone" in warnings[0].getMessage()
    assert re.search(r"\d", warnings[0].getMessage()) is None


def test_from_env_owner_valid_int_no_warning(monkeypatch, caplog):
    monkeypatch.delenv("WUWATERM_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.setenv("OWNER_USER_ID", " 654321 ")

    with caplog.at_level(logging.WARNING, logger="wuwaterm.bot"):
        config = BotConfig.from_env()

    assert config.owner_user_id == 654321
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_default_llm_timeout_has_gateway_margin(monkeypatch):
    monkeypatch.setenv("OWNER_USER_ID", "11")
    monkeypatch.delenv("WUWATERM_LLM_TIMEOUT_SECONDS", raising=False)

    config = BotConfig.from_env()

    assert BotConfig().llm_timeout_seconds == 45.0
    assert config.llm_timeout_seconds == 45.0


def test_from_env_parses_llm_timeout_and_concurrency(monkeypatch):
    monkeypatch.setenv("OWNER_USER_ID", "11")
    monkeypatch.setenv("WUWATERM_LLM_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("WUWATERM_LLM_MAX_CONCURRENCY", "2")

    config = BotConfig.from_env()

    assert config.llm_timeout_seconds == 7.5
    assert config.llm_max_concurrency == 2


def test_from_env_reject_overrides_win_verbatim_and_are_not_auto_bilingual(monkeypatch):
    monkeypatch.delenv("WUWATERM_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.setenv("WUWATERM_GROUP_TR_REJECT_TEXT", "管理员专用。")
    monkeypatch.setenv("WUWATERM_PRIVATE_TR_REJECT_TEXT", "老板专用。")

    config = BotConfig.from_env()

    # Owner-set override wins exactly as written; the bot never appends a second
    # (English) line to it — owner text stays the owner's responsibility.
    assert config.group_tr_reject_text == "管理员专用。"
    assert config.private_tr_reject_text == "老板专用。"
    assert "\n" not in config.group_tr_reject_text
    assert "\n" not in config.private_tr_reject_text


@pytest.mark.parametrize("user_id", [22, 11])
def test_channel_chat_is_rejected_even_for_owner(user_id, sample_db):
    update, message = fake_update(
        chat_id=-5005, chat_type="channel", message_id=920, user_id=user_id
    )
    context = fake_context(sample_db, ["声骸"])

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert context.bot.member_calls == []


def test_private_tr_silent_flag_suppresses_rejection_reply(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=2, chat_type="private", user_id=22)
    context = fake_context(
        sample_db,
        ["今汐说声骸很强"],
        config=BotConfig(rate_limit_per_minute=10, tr_reject_silent=True, owner_user_id=11),
    )

    asyncio.run(term_command(update, context))

    assert message.replies == []
    assert calls == []


def test_private_tr_rejection_text_is_configurable(sample_db):
    update, message = fake_update(chat_id=2, chat_type="private", user_id=22)
    context = fake_context(
        sample_db,
        ["声骸"],
        config=BotConfig(rate_limit_per_minute=10, owner_user_id=11, private_tr_reject_text="老板专用。"),
    )

    asyncio.run(term_command(update, context))

    assert message.replies == [("老板专用。", None)]


def test_private_tr_reject_replies_are_capped_then_silent(sample_db):
    context = fake_context(
        sample_db,
        ["声骸"],
        config=BotConfig(rate_limit_per_minute=2, owner_user_id=11),
    )
    replies = []
    for idx in range(3):
        update, message = fake_update(chat_id=2, chat_type="private", user_id=22, message_id=930 + idx)
        asyncio.run(term_command(update, context))
        replies.append(message.replies)

    assert replies[0] == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert replies[1] == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert replies[2] == []
    assert context.bot.member_calls == []


def test_private_sentence_stranger_gets_one_line_rejection_and_zero_llm(
    monkeypatch, sample_db
):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=2, chat_type="private", user_id=22)
    context = fake_context(sample_db, ["今汐说声骸很强"])

    asyncio.run(sentence_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert calls == []
    assert context.bot.member_calls == []


def test_member_lookup_failure_log_carries_no_chat_or_user_ids(caplog, sample_db):
    chat_id, user_id = -778899001, 445566
    context = fake_context(
        sample_db,
        ["声骸"],
        member_overrides={(chat_id, user_id): TelegramError("temporarily unavailable")},
        allowlist=(chat_id,),
    )
    update, message = fake_update(
        chat_id=chat_id, chat_type="supergroup", message_id=321, user_id=user_id
    )

    with caplog.at_level(logging.WARNING, logger="wuwaterm.bot"):
        asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 321)]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "get_chat_member failed" in warnings[0].getMessage()
    assert "445566" not in caplog.text
    assert "778899001" not in caplog.text


# --- reply-target translation (reply to a message + a bare command) ---


def test_tr_reply_to_chinese_message_translates_replied_content(sample_db):
    # Bare /tr (no inline text) while replying to a Chinese message translates
    # that replied message's content. (The screenshot scenario.)
    update, message = fake_update(reply_to=SimpleNamespace(text="声骸", caption=None))
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", None)]


def test_tr_reply_to_formatted_message_preserves_html(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")
    calls = []

    async def fake_call(
        _locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=45.0,
        transport=None,
    ):
        calls.append((html_mode, to_chinese))
        jinhsi = placeholder_for(locks, "Jinhsi")
        echo = placeholder_for(locks, "Echo")
        return (
            f'<b>{jinhsi}</b> says '
            f'<a href="https://example.com">{echo}</a> is strong'
        )

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    replied = SimpleNamespace(
        text="今汐说声骸很强",
        caption=None,
        text_html='<b>今汐</b>说<a href="https://example.com">声骸</a>很强',
        caption_html=None,
        entities=[SimpleNamespace(type="bold")],
    )
    update, message = fake_update(reply_to=replied)
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert calls == [(True, False)]
    assert message.replies == [
        (
            '<b>Jinhsi</b> says '
            '<a href="https://example.com">Echo</a> is strong',
            None,
        )
    ]
    assert message.reply_kwargs == [{"do_quote": False, "parse_mode": "HTML"}]


def test_sentence_reply_to_formatted_caption_preserves_html(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def fake_call(
        _locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=45.0,
        transport=None,
    ):
        assert html_mode is True
        assert to_chinese is False
        jinhsi = placeholder_for(locks, "Jinhsi")
        return f"<tg-spoiler>{jinhsi}</tg-spoiler> arrives"

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    replied = SimpleNamespace(
        text=None,
        caption="今汐登场",
        text_html=None,
        caption_html="<tg-spoiler>今汐</tg-spoiler>登场",
        caption_entities=[SimpleNamespace(type="spoiler")],
    )
    update, message = fake_update(reply_to=replied)
    context = fake_context(sample_db, [])

    asyncio.run(sentence_command(update, context))

    assert message.replies == [("<tg-spoiler>Jinhsi</tg-spoiler> arrives", None)]
    assert message.reply_kwargs == [{"do_quote": False, "parse_mode": "HTML"}]


def test_formatted_reply_exact_dictionary_hit_stays_plain(sample_db):
    replied = SimpleNamespace(
        text="声骸",
        caption=None,
        text_html="<b>声骸</b>",
        caption_html=None,
        entities=[SimpleNamespace(type="bold")],
    )
    update, message = fake_update(reply_to=replied)
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", None)]
    assert message.reply_kwargs == [{"do_quote": False}]


def test_formatted_reply_without_llm_uses_plain_fallback(monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    replied = SimpleNamespace(
        text="不存在词条",
        caption=None,
        text_html="<b>不存在词条</b>",
        caption_html=None,
        entities=[SimpleNamespace(type="bold")],
    )
    update, message = fake_update(reply_to=replied)
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [
        (
            "不存在词条\n\n"
            "(词典外,机器直译)\n"
            "(Not in official dictionary; machine-translated)",
            None,
        )
    ]
    assert message.reply_kwargs == [{"do_quote": False}]


def test_formatted_reply_invalid_llm_html_falls_back_to_plain(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def fake_call(
        _locked_text,
        _locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=45.0,
        transport=None,
    ):
        assert html_mode is True
        return "<script>translated</script>"

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    replied = SimpleNamespace(
        text="这是一个需要翻译的句子。",
        caption=None,
        text_html="<b>这是一个需要翻译的句子。</b>",
        caption_html=None,
        entities=[SimpleNamespace(type="bold")],
    )
    update, message = fake_update(reply_to=replied)
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [("translated", None)]
    assert message.reply_kwargs == [{"do_quote": False}]


def test_tr_inline_text_wins_over_replied_content(sample_db):
    # Inline args take precedence; the replied-to content is ignored.
    update, message = fake_update(
        reply_to=SimpleNamespace(
            text="守岸人",
            caption=None,
            text_html="<b>守岸人</b>",
            caption_html=None,
            entities=[SimpleNamespace(type="bold")],
        )
    )
    context = fake_context(sample_db, ["声骸"])

    asyncio.run(term_command(update, context))

    # 声骸 -> Echo (inline), NOT 守岸人 -> Shorekeeper (replied).
    assert message.replies == [("Echo", None)]
    assert message.reply_kwargs == [{"do_quote": False}]


def test_tr_reply_to_image_only_message_falls_through_to_usage(sample_db):
    # Replying to a sticker/image with no text and no caption -> Usage hint.
    update, message = fake_update(reply_to=SimpleNamespace(text=None, caption=None))
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [(TERM_USAGE_NOTICE, None)]


def test_tr_reply_to_caption_media_translates_caption(sample_db):
    # Media message with a caption (no text) -> the caption is the input.
    update, message = fake_update(reply_to=SimpleNamespace(text=None, caption="守岸人"))
    context = fake_context(sample_db, [])

    asyncio.run(term_command(update, context))

    assert message.replies == [("Shorekeeper", None)]


def test_sentence_reply_to_chinese_message_translates_replied_content(monkeypatch, sample_db):
    # The sentence handler (/sentence, /sent) honors the same reply fallback.
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    update, message = fake_update(
        reply_to=SimpleNamespace(text="今汐装备了声骸", caption=None)
    )
    context = fake_context(sample_db, [])

    asyncio.run(sentence_command(update, context))

    assert message.replies == [("Jinhsi装备了Echo", None)]


def test_usage_notices_are_bilingual(sample_db):
    assert TERM_USAGE_NOTICE == (
        "用法：/tr <中文或英文>（自动判向：中→英 / 英→中；或回复一条消息后发 /tr 直接翻译）\n"
        "Usage: /tr <Chinese or English> (direction auto-detected; or reply to a message, then send /tr)"
    )
    assert SENTENCE_USAGE_NOTICE == (
        "用法：/sentence <中文或英文句子>（自动判向：中→英 / 英→中；或回复一条消息后发 /sentence 直接翻译）\n"
        "Usage: /sentence <Chinese or English sentence> (direction auto-detected; or reply to a message, then send /sentence)"
    )
    # Bare command, no inline text and no reply -> the bilingual Usage hint.
    update, message = fake_update()
    context = fake_context(sample_db, [])
    asyncio.run(sentence_command(update, context))
    assert message.replies == [(SENTENCE_USAGE_NOTICE, None)]


# --- /about diagnostics command ---


def test_term_service_metadata_and_count(sample_db):
    service = TermService(sample_db)
    meta = service.metadata()
    assert meta["wutheringdata_commit"] == "e9234ffe094b2d944d16b222d31102e8ab32d954"
    assert meta["source_profile"] == "dimbreath_legacy"
    assert service.term_count() > 0


def test_about_returns_expected_fields(monkeypatch, sample_db):
    update, message = fake_update()
    context = fake_context(sample_db, [])
    service = context.application.bot_data[SERVICE_KEY]
    monkeypatch.setattr(
        service,
        "metadata",
        lambda: {
            "source_profile": "arikatsu",
            "source_repo_url": "https://example.test/repo.git",
            "wutheringdata_commit": "abc123def456",
        },
    )
    monkeypatch.setattr(service, "term_count", lambda: 4242)

    asyncio.run(about_command(update, context))

    assert len(message.replies) == 1
    reply = message.replies[0][0]
    assert "arikatsu" in reply
    assert "https://example.test/repo.git" in reply
    assert "abc123def456" in reply
    assert "4242" in reply
    assert "10/min" in reply  # fake_context default rate limit


def test_about_makes_zero_llm_calls(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update()
    context = fake_context(sample_db, [])

    asyncio.run(about_command(update, context))

    assert len(message.replies) == 1
    assert calls == []


def test_about_has_no_auth_gate(sample_db):
    # A non-owner in private still gets /about (informational, no auth gate).
    update, message = fake_update(chat_id=2, chat_type="private", user_id=22)
    context = fake_context(
        sample_db, [], config=BotConfig(rate_limit_per_minute=10, owner_user_id=11)
    )

    asyncio.run(about_command(update, context))

    assert len(message.replies) == 1
    reply = message.replies[0][0]
    assert reply != DEFAULT_PRIVATE_TR_REJECT_TEXT
    assert "wuwaterm /about" in reply
    assert context.bot.member_calls == []


def test_about_flags_missing_commit_metadata(monkeypatch, sample_db):
    update, message = fake_update()
    context = fake_context(sample_db, [])
    service = context.application.bot_data[SERVICE_KEY]
    monkeypatch.setattr(service, "metadata", lambda: {})
    monkeypatch.setattr(service, "term_count", lambda: 0)

    asyncio.run(about_command(update, context))

    reply = message.replies[0][0]
    assert "unknown" in reply  # the missing commit field flags the gap


def test_about_contains_full_commit_from_db(sample_db):
    # With the real sample DB, /about is the one reply allowed to carry the
    # full pinned commit hash (read from DB metadata).
    update, message = fake_update()
    context = fake_context(sample_db, [])

    asyncio.run(about_command(update, context))

    reply = message.replies[0][0]
    assert "e9234ffe094b2d944d16b222d31102e8ab32d954" in reply
    assert re.search(r"[0-9a-f]{40}", reply) is not None


# --- /status owner diagnostics ---


def test_status_owner_gets_sanitized_counts(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(sample_db, [])
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    settings.set_public(-2001, True)
    reply_index = context.application.bot_data[CHANNEL_REPLY_INDEX_KEY]
    reply_index.remember_many(-2001, 4001, (5001, 5002))

    asyncio.run(status_command(update, context))

    assert len(message.replies) == 1
    reply = message.replies[0][0]
    assert "wuwaterm /status" in reply
    assert "LLM configured: yes" in reply
    assert "Tracked channel posts: 1" in reply
    assert "Channel reply persistence: off" in reply
    assert "Channel reply load failures: 0" in reply
    assert "Channel reply last load: not configured" in reply
    assert "Channel reply save failures: 0" in reply
    assert "Channel reply last save: not configured" in reply
    assert "Authorized chats: 3" in reply
    assert "Public chats: 1" in reply
    assert "LLM input limit: 2000" in reply
    assert "-2001" not in reply
    assert "5001" not in reply
    assert "test-key" not in reply
    assert re.search(r"[0-9a-f]{40}", reply) is None


def test_status_reports_channel_reply_persistence_health_without_paths(
    sample_db, tmp_path
):
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(sample_db, [])
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("x", encoding="utf-8")
    storage_path = blocked_parent / "channel_replies.json"
    reply_index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=storage_path)
    context.application.bot_data[CHANNEL_REPLY_INDEX_KEY] = reply_index

    reply_index.remember_many(-2001, 4001, (5001,))
    asyncio.run(status_command(update, context))

    assert len(message.replies) == 1
    reply = message.replies[0][0]
    assert "Channel reply persistence: on" in reply
    assert "Channel reply load failures: 0" in reply
    assert "Channel reply last load: not attempted" in reply
    assert "Channel reply save failures: 1" in reply
    assert "Channel reply last save: failed" in reply
    assert str(tmp_path) not in reply
    assert "-2001" not in reply
    assert "4001" not in reply
    assert "5001" not in reply


def test_status_reports_healthy_channel_reply_persistence_without_paths(
    sample_db, tmp_path
):
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(sample_db, [])
    storage_path = tmp_path / "channel_replies.json"
    reply_index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=storage_path)
    context.application.bot_data[CHANNEL_REPLY_INDEX_KEY] = reply_index

    reply_index.remember_many(-2001, 4001, (5001,))
    asyncio.run(status_command(update, context))

    assert len(message.replies) == 1
    reply = message.replies[0][0]
    assert "Channel reply persistence: on" in reply
    assert "Channel reply load failures: 0" in reply
    assert "Channel reply last load: not attempted" in reply
    assert "Channel reply save failures: 0" in reply
    assert "Channel reply last save: ok" in reply
    assert str(tmp_path) not in reply
    assert "-2001" not in reply
    assert "4001" not in reply
    assert "5001" not in reply


def test_status_reports_channel_reply_load_failure_without_details(sample_db, tmp_path):
    update, message = fake_update(chat_id=1, chat_type="private", user_id=11)
    context = fake_context(sample_db, [])
    storage_path = tmp_path / "channel_replies.json"
    storage_path.write_text("{not json", encoding="utf-8")
    reply_index = ChannelReplyIndex(ttl_seconds=60.0, storage_path=storage_path)
    context.application.bot_data[CHANNEL_REPLY_INDEX_KEY] = reply_index

    asyncio.run(status_command(update, context))

    assert len(message.replies) == 1
    reply = message.replies[0][0]
    assert "Channel reply persistence: on" in reply
    assert "Channel reply load failures: 1" in reply
    assert "Channel reply last load: failed" in reply
    assert "Channel reply save failures: 0" in reply
    assert "Channel reply last save: not attempted" in reply
    assert str(tmp_path) not in reply
    assert storage_path.name not in reply
    assert "-2001" not in reply
    assert "4001" not in reply
    assert "5001" not in reply
    assert "test-key" not in reply
    assert "JSONDecodeError" not in reply
    assert "Expecting" not in reply


def test_status_non_owner_is_silent(sample_db):
    update, message = fake_update(chat_id=1, chat_type="private", user_id=22)
    context = fake_context(sample_db, [])

    asyncio.run(status_command(update, context))

    assert message.replies == []


# --- /public toggle ---


def test_public_on_enables_and_persists_for_admin(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=600
    )
    context = fake_context(sample_db, ["on"], member_status="administrator")
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    assert settings.is_public(-2001) is False  # default

    asyncio.run(public_command(update, context))

    assert message.replies == [(PUBLIC_ENABLED_NOTICE, 600)]
    assert settings.is_public(-2001) is True


def test_public_off_disables_for_admin(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=601
    )
    context = fake_context(sample_db, ["off"], member_status="administrator")
    context.application.bot_data[CHAT_SETTINGS_KEY].set_public(-2001, True)

    asyncio.run(public_command(update, context))

    assert message.replies == [(PUBLIC_DISABLED_NOTICE, 601)]
    assert (
        context.application.bot_data[CHAT_SETTINGS_KEY].is_public(-2001) is False
    )


def test_public_status_reports_current_state(sample_db):
    update_off, msg_off = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=602
    )
    context = fake_context(sample_db, [], member_status="administrator")
    asyncio.run(public_command(update_off, context))
    assert msg_off.replies == [(PUBLIC_STATUS_OFF, 602)]

    context.application.bot_data[CHAT_SETTINGS_KEY].set_public(-2001, True)
    update_on, msg_on = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=603
    )
    context.args = ["status"]
    asyncio.run(public_command(update_on, context))
    assert msg_on.replies == [(PUBLIC_STATUS_ON, 603)]


def test_public_bad_arg_returns_usage_notice(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=604
    )
    context = fake_context(sample_db, ["maybe"], member_status="administrator")

    asyncio.run(public_command(update, context))

    assert message.replies == [(PUBLIC_USAGE_NOTICE, 604)]
    # Bad arg must not silently flip the state.
    assert (
        context.application.bot_data[CHAT_SETTINGS_KEY].is_public(-2001) is False
    )


def test_public_non_admin_rejected_with_bilingual_notice(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=605, user_id=42
    )
    context = fake_context(sample_db, ["on"], member_status="member")

    asyncio.run(public_command(update, context))

    assert message.replies == [(PUBLIC_REJECT_NOTICE, 605)]
    # Non-admin attempt must not flip the state.
    assert (
        context.application.bot_data[CHAT_SETTINGS_KEY].is_public(-2001) is False
    )


def test_public_non_admin_silent_when_reject_silent(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=606, user_id=42
    )
    context = fake_context(
        sample_db,
        ["on"],
        member_status="member",
        config=BotConfig(
            rate_limit_per_minute=10, owner_user_id=11, tr_reject_silent=True
        ),
    )

    asyncio.run(public_command(update, context))

    assert message.replies == []
    assert (
        context.application.bot_data[CHAT_SETTINGS_KEY].is_public(-2001) is False
    )


def test_public_in_private_chat_returns_groups_only_notice(sample_db):
    update, message = fake_update(chat_id=11, chat_type="private", user_id=11)
    context = fake_context(sample_db, ["on"])

    asyncio.run(public_command(update, context))

    assert message.replies == [(PUBLIC_ONLY_GROUPS_NOTICE, None)]


def test_public_mode_allows_non_admin_translate_commands(sample_db):
    # An admin flips the chat to public.
    admin_update, _admin_msg = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=610
    )
    context = fake_context(sample_db, ["on"], member_status="administrator")
    asyncio.run(public_command(admin_update, context))

    # Now a non-admin's /tr works (still throttled, but not rejected).
    context.args = ["声骸"]
    member_update, member_msg = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=611, user_id=42
    )
    # Override the bot to return 'member' for this user; admin status is cached
    # per (chat, user), so the new user is a fresh lookup and returns 'member'
    # from the default. To make the cache return 'member' for user 42, swap the
    # FakeBot default.
    context.bot.default_status = "member"
    # Clear admin cache so the new user isn't poisoned by the prior admin lookup.
    context.application.bot_data[ADMIN_CACHE_KEY] = AdminStatusCache()

    asyncio.run(term_command(member_update, context))

    assert member_msg.replies == [("Echo", 611)]


def test_public_command_is_admin_only_even_when_public_mode_is_on(sample_db):
    # Public mode must NOT let a non-admin flip the switch back off — otherwise
    # any group member could disable a public chat. This is the central
    # invariant of the /public design.
    context = fake_context(sample_db, ["off"], member_status="member")
    context.application.bot_data[CHAT_SETTINGS_KEY].set_public(-2001, True)

    member_update, member_msg = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=620, user_id=42
    )

    asyncio.run(public_command(member_update, context))

    assert member_msg.replies == [(PUBLIC_REJECT_NOTICE, 620)]
    # The chat must still be public.
    assert (
        context.application.bot_data[CHAT_SETTINGS_KEY].is_public(-2001) is True
    )


def test_public_off_in_one_chat_does_not_affect_another(sample_db):
    context = fake_context(sample_db, ["on"], member_status="administrator")
    chat_a, chat_b = -2001, -2002

    update_a, _ = fake_update(
        chat_id=chat_a, chat_type="supergroup", message_id=700
    )
    asyncio.run(public_command(update_a, context))

    context.args = ["on"]
    update_b, _ = fake_update(
        chat_id=chat_b, chat_type="supergroup", message_id=701
    )
    asyncio.run(public_command(update_b, context))

    # Turning chat A off must leave chat B public.
    context.args = ["off"]
    update_a_off, _ = fake_update(
        chat_id=chat_a, chat_type="supergroup", message_id=702
    )
    asyncio.run(public_command(update_a_off, context))

    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    assert settings.is_public(chat_a) is False
    assert settings.is_public(chat_b) is True


def test_public_off_works_when_translation_limiter_exhausted(sample_db):
    # Finding 1: an admin must be able to close a spammed public chat. Drain the
    # TRANSLATION limiter via /tr (limit=1), then /public off must still flip.
    context = fake_context(sample_db, ["声骸"], limit=1, member_status="administrator")
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    settings.set_public(-2001, True)

    tr_update, _ = fake_update(chat_id=-2001, chat_type="supergroup", message_id=800)
    asyncio.run(term_command(tr_update, context))  # consumes the single slot

    context.args = ["off"]
    off_update, off_msg = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=801
    )
    asyncio.run(public_command(off_update, context))

    assert off_msg.replies == [(PUBLIC_DISABLED_NOTICE, 801)]
    assert settings.is_public(-2001) is False


def test_public_toggle_ignores_stale_admin_cache(sample_db):
    # Finding 2: a just-demoted admin (cached positive, now a plain member) must
    # NOT be able to flip /public — the control-plane toggle checks fresh.
    context = fake_context(sample_db, ["on"], member_status="member")
    cache = context.application.bot_data[ADMIN_CACHE_KEY]
    cache.put(-2001, 11, True)  # stale positive verdict

    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=810, user_id=11
    )
    asyncio.run(public_command(update, context))

    assert message.replies == [(PUBLIC_REJECT_NOTICE, 810)]
    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_public(-2001) is False
    # The stale cache was bypassed: a fresh getChatMember actually ran.
    assert (-2001, 11) in context.bot.member_calls


def test_public_mode_does_not_authorize_foreign_sender_chat(sample_db):
    # Finding 4: public mode opens the door for ordinary members only. A message
    # posted by a foreign sender_chat (a channel identity) is NOT authorized.
    context = fake_context(sample_db, ["声骸"], member_status="member")
    context.application.bot_data[CHAT_SETTINGS_KEY].set_public(-2001, True)

    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=820,
        user_id=None,
        sender_chat_id=-9999,  # a DIFFERENT id than the chat (not anon-admin)
    )
    asyncio.run(term_command(update, context))

    # Rejected, not translated.
    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 820)]


def test_anonymous_admin_still_authorized_in_public_chat(sample_db):
    # Finding 4 (other direction): the anonymous-admin case (sender_chat == chat)
    # must STILL be authorized — no regression from the sender_chat restriction.
    context = fake_context(sample_db, ["声骸"], member_status="member")
    context.application.bot_data[CHAT_SETTINGS_KEY].set_public(-2001, True)

    update, message = fake_update(
        chat_id=-2001,
        chat_type="supergroup",
        message_id=821,
        user_id=None,
        sender_chat_id=-2001,  # posts AS the group itself
    )
    asyncio.run(term_command(update, context))

    assert message.replies == [("Echo", 821)]


def test_public_on_replies_notice_when_save_fails(monkeypatch, sample_db):
    # Finding 3 (command layer): a settings save failure replies a notice and
    # does not raise an unhandled exception.
    context = fake_context(sample_db, ["on"], member_status="administrator")
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]

    def boom(chat_id, value):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "set_public", boom)
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=830
    )
    asyncio.run(public_command(update, context))

    assert message.replies == [(SETTINGS_SAVE_FAILED_NOTICE, 830)]


# --- group authorization gate: my_chat_member ---


def test_owner_added_group_is_authorized_and_kept(sample_db):
    # owner_user_id defaults to 11 in fake_context; the owner adds the bot.
    update = fake_member_update(chat_id=-2001, from_id=11)
    context = fake_context(sample_db, [])

    asyncio.run(my_chat_member_handler(update, context))

    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    assert settings.is_allowed(-2001) is True
    assert context.bot.left_chats == []
    assert context.bot.sent_messages == []


def test_nonowner_added_unauthorized_group_gets_notice_then_leaves(sample_db):
    update = fake_member_update(chat_id=-2001, from_id=42)  # not the owner
    context = fake_context(sample_db, [], allowlist=())

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.sent_messages == [(-2001, UNAUTHORIZED_GROUP_NOTICE)]
    assert context.bot.left_chats == [-2001]
    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2001) is False


def test_added_to_preauthorized_group_stays(sample_db):
    context = fake_context(sample_db, [])
    context.application.bot_data[CHAT_SETTINGS_KEY].allow(-2001)
    update = fake_member_update(chat_id=-2001, from_id=42)  # non-owner, but allowed

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == []
    assert context.bot.sent_messages == []


def test_promotion_in_unauthorized_joined_chat_does_not_leave(sample_db):
    # The live-group protector: a promotion (member->administrator) is NOT an
    # "added" edge, so the gate must not fire even when the chat is unallowed.
    update = fake_member_update(
        chat_id=-2001, from_id=42, old_status="member", new_status="administrator"
    )
    context = fake_context(sample_db, [])

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == []
    assert context.bot.sent_messages == []


def test_removal_event_does_not_leave_or_notify(sample_db):
    update = fake_member_update(
        chat_id=-2001, from_id=42, old_status="member", new_status="left"
    )
    context = fake_context(sample_db, [])

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == []
    assert context.bot.sent_messages == []


def test_owner_unset_leaves_unauthorized_group(sample_db):
    # Fail-closed: with no owner configured, owner-add can never match, so a
    # newly-added unauthorized group is left.
    context = fake_context(
        sample_db,
        [],
        config=BotConfig(rate_limit_per_minute=10, owner_user_id=None),
        allowlist=(),
    )
    update = fake_member_update(chat_id=-2001, from_id=11)

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == [-2001]
    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2001) is False


def test_left_to_restricted_nonmember_is_not_added(sample_db):
    # left -> restricted(is_member=False): the bot is NOT actually in the chat,
    # so this is not an "added" edge and the gate must not fire.
    context = fake_context(sample_db, [])
    update = fake_member_update(
        chat_id=-2001,
        from_id=42,
        old_status="left",
        new_status="restricted",
        new_is_member=False,
    )

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == []
    assert context.bot.sent_messages == []


def test_restricted_nonmember_to_member_is_added_and_gated(sample_db):
    # restricted(is_member=False) -> member: the bot just BECAME a member, so a
    # non-owner cannot use this transition to sneak the bot past the gate.
    context = fake_context(sample_db, [], allowlist=())
    update = fake_member_update(
        chat_id=-2001,
        from_id=42,
        old_status="restricted",
        old_is_member=False,
        new_status="member",
    )

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == [-2001]


def test_restricted_member_to_admin_is_not_added(sample_db):
    # restricted(is_member=True) -> administrator: already a member, not an add.
    context = fake_context(sample_db, [])
    update = fake_member_update(
        chat_id=-2001,
        from_id=42,
        old_status="restricted",
        old_is_member=True,
        new_status="administrator",
    )

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == []


def test_owner_add_that_cannot_persist_leaves_fail_closed(monkeypatch, sample_db):
    # If owner-add authorization cannot be written (disk full), fail closed:
    # leave rather than stay in a chat we cannot remember authorizing.
    context = fake_context(sample_db, [])
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]

    def boom(chat_id):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "allow", boom)
    update = fake_member_update(chat_id=-2001, from_id=11)  # owner adds

    asyncio.run(my_chat_member_handler(update, context))

    assert context.bot.left_chats == [-2001]


# --- /authorize, /revoke (owner-only) ---


def test_authorize_in_group_allows_current_chat(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=900, user_id=11
    )
    context = fake_context(sample_db, [])  # no args -> current chat

    asyncio.run(authorize_command(update, context))

    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    assert settings.is_allowed(-2001) is True
    assert message.replies == [
        ("已授权本群（chat_id=-2001）\nThis chat is authorized (chat_id=-2001).", 900)
    ]


def test_authorize_by_id_in_private(sample_db):
    update, message = fake_update(chat_id=11, chat_type="private", user_id=11)
    context = fake_context(sample_db, ["-2002"])

    asyncio.run(authorize_command(update, context))

    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2002) is True
    assert message.replies == [("已授权 / Authorized chat_id=-2002", None)]


def test_authorize_non_owner_is_silent(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=901, user_id=42
    )
    context = fake_context(sample_db, [], allowlist=())

    asyncio.run(authorize_command(update, context))

    assert message.replies == []
    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2001) is False


def test_authorize_owner_unset_is_silent_for_everyone(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=903, user_id=11
    )
    context = fake_context(
        sample_db,
        [],
        config=BotConfig(rate_limit_per_minute=10, owner_user_id=None),
        allowlist=(),
    )

    asyncio.run(authorize_command(update, context))

    assert message.replies == []
    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2001) is False


def test_revoke_in_group_leaves_and_removes_current_chat(sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=902, user_id=11
    )
    context = fake_context(sample_db, [])
    context.application.bot_data[CHAT_SETTINGS_KEY].allow(-2001)

    asyncio.run(revoke_command(update, context))

    settings = context.application.bot_data[CHAT_SETTINGS_KEY]
    assert settings.is_allowed(-2001) is False
    assert context.bot.left_chats == [-2001]  # /revoke actually stops service
    assert message.replies == [
        (
            "已撤销本群授权并退出本群（chat_id=-2001）\nRevoked; leaving this chat (chat_id=-2001).",
            902,
        )
    ]


def test_revoke_by_id_in_private_leaves_target(sample_db):
    update, message = fake_update(chat_id=11, chat_type="private", user_id=11)
    context = fake_context(sample_db, ["-2002"])
    context.application.bot_data[CHAT_SETTINGS_KEY].allow(-2002)

    asyncio.run(revoke_command(update, context))

    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2002) is False
    assert context.bot.left_chats == [-2002]
    assert message.replies == [("已撤销并退出 / Revoked and left chat_id=-2002", None)]


def test_revoke_leaves_even_if_confirmation_reply_fails(sample_db):
    # A TelegramError from the confirmation reply must NOT abort the leave —
    # otherwise a revoked group could keep being served.
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=905, user_id=11
    )

    async def _raise_reply(*args, **kwargs):
        raise TelegramError("send failed")

    message.reply_text = _raise_reply
    context = fake_context(sample_db, [])
    context.application.bot_data[CHAT_SETTINGS_KEY].allow(-2001)

    asyncio.run(revoke_command(update, context))

    assert context.bot.left_chats == [-2001]
    assert context.application.bot_data[CHAT_SETTINGS_KEY].is_allowed(-2001) is False


def test_tr_rejected_in_non_allowlisted_group(sample_db):
    # Option B: the bot serves only allowlisted groups. Even an admin in a chat
    # not on the allowlist is not served — so a revoked/unauthorized chat, or one
    # where leave_chat failed, stops being served even before the bot is removed.
    update, message = fake_update(
        chat_id=-7001, chat_type="supergroup", message_id=950
    )
    context = fake_context(sample_db, ["声骸"], member_status="administrator")  # -7001 not allowlisted

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 950)]


def test_authorize_save_failure_notifies_and_does_not_claim_success(monkeypatch, sample_db):
    update, message = fake_update(
        chat_id=-7001, chat_type="supergroup", message_id=951, user_id=11
    )
    context = fake_context(sample_db, [], allowlist=())
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]

    def boom(chat_id):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "allow", boom)
    asyncio.run(authorize_command(update, context))

    assert message.replies == [(SETTINGS_SAVE_FAILED_NOTICE, 951)]
    assert settings.is_allowed(-7001) is False  # not claimed authorized


def test_revoke_save_failure_notifies_but_still_leaves(monkeypatch, sample_db):
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=952, user_id=11
    )
    context = fake_context(sample_db, [])  # -2001 allowlisted by default
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]

    def boom(chat_id):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "disallow", boom)
    asyncio.run(revoke_command(update, context))

    assert context.bot.left_chats == [-2001]  # leaves regardless of the write failure
    assert "未能保存撤销" in message.replies[0][0]  # surfaces, does not claim success


def test_revoke_save_failure_keeps_chat_denied_and_unserved(monkeypatch, sample_db):
    # End-to-end fail-closed proof (Codex finding: "Fail closed when revoke
    # cannot persist"). When the disk WRITE fails, the real disallow() keeps the
    # chat removed in memory rather than rolling it back, so the chat is denied
    # AND a follow-up admin /tr there is rejected. A continued admin or a re-add
    # cannot keep using the bot after a failed-persist revoke.
    update, message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=953, user_id=11
    )
    context = fake_context(sample_db, [])  # -2001 allowlisted by default
    settings = context.application.bot_data[CHAT_SETTINGS_KEY]

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(settings, "_save", boom)  # the WRITE fails; real disallow runs
    asyncio.run(revoke_command(update, context))

    assert settings.is_allowed(-2001) is False  # deny kept despite the write failure
    assert context.bot.left_chats == [-2001]  # still leaves
    assert "未能保存撤销" in message.replies[0][0]  # surfaces the failure

    # Serving is actually denied now: an admin /tr in the same chat is rejected
    # because the gate consults is_allowed, which the failed revoke left False.
    tr_update, tr_message = fake_update(
        chat_id=-2001, chat_type="supergroup", message_id=954, user_id=11
    )
    tr_context = fake_context(sample_db, ["声骸"], member_status="administrator")
    tr_context.application.bot_data[CHAT_SETTINGS_KEY] = settings  # the now-denied state
    asyncio.run(term_command(tr_update, tr_context))

    assert tr_message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 954)]
