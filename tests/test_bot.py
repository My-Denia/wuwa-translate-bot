from __future__ import annotations

import asyncio
import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from telegram import Update, User
from telegram.error import TelegramError
from telegram.ext import CommandHandler

from wuwaterm.bot import (
    ADMIN_CACHE_KEY,
    CONFIG_KEY,
    DEFAULT_GROUP_TR_REJECT_TEXT,
    DEFAULT_PRIVATE_TR_REJECT_TEXT,
    LLM_INPUT_CHAR_LIMIT,
    RATE_LIMITER_KEY,
    SERVICE_KEY,
    THROTTLE_NOTICE,
    TRANSLATOR_KEY,
    AdminStatusCache,
    BotConfig,
    PerChatRateLimiter,
    create_application,
    sentence_command,
    term_command,
)
from wuwaterm.constants import PINNED_WUTHERINGDATA_COMMIT
from wuwaterm.lookup import TermService
from wuwaterm.sentence import (
    BUDGET_EXHAUSTED_NOTICE,
    SentenceTranslator,
    _llm_error_from_response,
)


class FakeMessage:
    def __init__(self, message_id: int = 101, sender_chat_id: int | None = None):
        self.message_id = message_id
        self.sender_chat = (
            SimpleNamespace(id=sender_chat_id) if sender_chat_id is not None else None
        )
        self.replies: list[tuple[str, int | None]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append((text, kwargs.get("reply_to_message_id")))


class FakeBot:
    def __init__(self, default_status: str = "member", overrides=None):
        self.default_status = default_status
        self.overrides = dict(overrides or {})
        self.member_calls: list[tuple[int, int]] = []

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.member_calls.append((chat_id, user_id))
        outcome = self.overrides.get((chat_id, user_id), self.default_status)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(status=outcome)


def fake_update(
    chat_id: int = 1,
    chat_type: str = "private",
    message_id: int = 101,
    user_id: int | None = 11,
    sender_chat_id: int | None = None,
):
    message = FakeMessage(message_id=message_id, sender_chat_id=sender_chat_id)
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return (
        SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=user),
        message,
    )


def fake_context(
    sample_db,
    args,
    *,
    limit=10,
    member_status="administrator",
    member_overrides=None,
    config=None,
):
    config = config or BotConfig(rate_limit_per_minute=limit, owner_user_id=11)
    bot = FakeBot(default_status=member_status, overrides=member_overrides)
    return SimpleNamespace(
        args=args,
        bot=bot,
        application=SimpleNamespace(
            bot_data={
                SERVICE_KEY: TermService(sample_db),
                TRANSLATOR_KEY: SentenceTranslator(sample_db),
                CONFIG_KEY: config,
                RATE_LIMITER_KEY: PerChatRateLimiter(limit=config.rate_limit_per_minute),
                ADMIN_CACHE_KEY: AdminStatusCache(),
            }
        ),
    )


def enable_mock_llm(monkeypatch, calls, response_factory):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    def fake_call(locked_text, locks):
        calls.append((locked_text, locks))
        return response_factory(locked_text, locks)

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)


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


def test_short_dictionary_miss_translates_with_pinned_commit_note(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "Unlisted term")
    update, message = fake_update()
    context = fake_context(sample_db, ["不存在词条"])

    asyncio.run(term_command(update, context))

    assert message.replies == [
        (
            "Unlisted term\n\n"
            f"(not in official data (pinned commit {PINNED_WUTHERINGDATA_COMMIT}))",
            None,
        )
    ]
    assert len(calls) == 1


def test_llm_path_rejects_over_1000_chars_before_call(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update()
    context = fake_context(sample_db, ["测" * (LLM_INPUT_CHAR_LIMIT + 1)])

    asyncio.run(term_command(update, context))

    assert message.replies == [
        (f"Input is too long for translation ({LLM_INPUT_CHAR_LIMIT} character limit).", None)
    ]
    assert calls == []


def test_term_command_budget_exhaustion_returns_clean_bot_reply(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    def fake_call(_locked_text, _locks):
        raise _llm_error_from_response(
            httpx.Response(429, text='{"error":"max_budget exceeded"}')
        )

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=558)
    context = fake_context(sample_db, ["这是一个需要翻译的句子。"])

    asyncio.run(term_command(update, context))

    assert message.replies == [(BUDGET_EXHAUSTED_NOTICE, 558)]
    reply = message.replies[0][0]
    assert reply == "本月翻译额度已用完,请稍后再试。"
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

    assert message.replies == [("Cartethyia", None)]


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


def test_no_free_text_group_listener_or_inline_handler(sample_db):
    app = create_application("123:ABC", sample_db, config=BotConfig())
    handler_types = {type(handler).__name__ for group in app.handlers.values() for handler in group}

    assert handler_types == {"CommandHandler"}


def test_group_tr_member_gets_one_line_rejection_and_zero_llm(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=-2001, chat_type="supergroup", message_id=900)
    context = fake_context(sample_db, ["今汐说声骸很强"], member_status="member")

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_GROUP_TR_REJECT_TEXT, 900)]
    assert message.replies[0][0] == "仅群管理员可用 /tr"
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


def test_group_tr_rejections_consume_throttle_budget(sample_db):
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
    assert replies[2] == [(THROTTLE_NOTICE, 911)]
    assert context.bot.member_calls == [(-2001, 11)]


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


def test_private_sentence_stranger_rejections_consume_throttle_budget(sample_db):
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
    assert replies[2] == [(THROTTLE_NOTICE, None)]
    assert context.bot.member_calls == []


def test_private_tr_stranger_gets_one_line_rejection_and_zero_llm(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "should not run")
    update, message = fake_update(chat_id=2, chat_type="private", user_id=22)
    context = fake_context(sample_db, ["今汐说声骸很强"])

    asyncio.run(term_command(update, context))

    assert message.replies == [(DEFAULT_PRIVATE_TR_REJECT_TEXT, None)]
    assert message.replies[0][0] == "此 bot 仅限群内由管理员使用"
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


def test_private_tr_stranger_rejections_consume_throttle_budget(sample_db):
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
    assert replies[2] == [(THROTTLE_NOTICE, None)]
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
