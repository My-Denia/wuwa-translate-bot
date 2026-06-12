from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telegram import Update, User
from telegram.ext import CommandHandler

from wuwaterm.bot import (
    CONFIG_KEY,
    LLM_INPUT_CHAR_LIMIT,
    RATE_LIMITER_KEY,
    SERVICE_KEY,
    THROTTLE_NOTICE,
    TRANSLATOR_KEY,
    BotConfig,
    PerChatRateLimiter,
    create_application,
    sentence_command,
    term_command,
)
from wuwaterm.constants import PINNED_WUTHERINGDATA_COMMIT
from wuwaterm.lookup import TermService
from wuwaterm.sentence import SentenceTranslator


class FakeMessage:
    def __init__(self, message_id: int = 101):
        self.message_id = message_id
        self.replies: list[tuple[str, int | None]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append((text, kwargs.get("reply_to_message_id")))


def fake_update(chat_id: int = 1, chat_type: str = "private", message_id: int = 101):
    message = FakeMessage(message_id=message_id)
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    return SimpleNamespace(effective_message=message, effective_chat=chat), message


def fake_context(sample_db, args, *, limit=10):
    config = BotConfig(rate_limit_per_minute=limit)
    return SimpleNamespace(
        args=args,
        application=SimpleNamespace(
            bot_data={
                SERVICE_KEY: TermService(sample_db),
                TRANSLATOR_KEY: SentenceTranslator(sample_db),
                CONFIG_KEY: config,
                RATE_LIMITER_KEY: PerChatRateLimiter(limit=config.rate_limit_per_minute),
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


def test_group_tr_sentence_uses_llm_without_chat_gate(monkeypatch, sample_db):
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
