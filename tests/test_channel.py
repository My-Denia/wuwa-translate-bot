from __future__ import annotations

import asyncio
import logging
import re
from types import SimpleNamespace

import httpx
import pytest

from wuwaterm.bot import (
    ADMIN_CACHE_KEY,
    CONFIG_KEY,
    RATE_LIMITER_KEY,
    SERVICE_KEY,
    THROTTLE_NOTICE,
    TRANSLATOR_KEY,
    AdminStatusCache,
    BotConfig,
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


CN_TEXT = "今汐说声骸很强"
CN_TEXT_HTML = (
    '<b>今汐</b>说<a href="https://example.com">声骸</a>很'
    "<tg-spoiler>强</tg-spoiler>"
)


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
    ):
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.text_html = text_html
        self.caption_html = caption_html
        self.sender_chat = sender_chat
        self.is_automatic_forward = is_automatic_forward
        self.replies: list[tuple[str, str | None, int | None]] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append(
            (text, kwargs.get("parse_mode"), kwargs.get("reply_to_message_id"))
        )
        return SimpleNamespace(message_id=self.message_id + 1000)


class FakeBot:
    def __init__(self, default_status: str = "administrator"):
        self.default_status = default_status
        self.member_calls: list[tuple[int, int]] = []

    async def get_chat_member(self, chat_id: int, user_id: int):
        self.member_calls.append((chat_id, user_id))
        return SimpleNamespace(status=self.default_status)


def channel_update(
    *,
    text: str | None = None,
    caption: str | None = None,
    text_html: str | None = None,
    caption_html: str | None = None,
    chat_id: int = -2001,
    message_id: int = 4001,
):
    message = FakeMessage(
        message_id=message_id,
        text=text,
        caption=caption,
        text_html=text_html if text_html is not None else text,
        caption_html=caption_html if caption_html is not None else caption,
        sender_chat=SimpleNamespace(id=-3001, type="channel"),
        is_automatic_forward=True,
    )
    update = SimpleNamespace(
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


def make_context(sample_db, *, config=None, args=()):
    config = config or BotConfig(rate_limit_per_minute=10, owner_user_id=11)
    return SimpleNamespace(
        args=list(args),
        bot=FakeBot(),
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

    def fake_call(locked_text, locks, html_mode=False):
        calls.append((locked_text, locks, html_mode))
        return response_factory(locked_text, locks)

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)


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


def test_english_post_is_silent_and_consumes_no_throttle(monkeypatch, sample_db):
    calls = []
    enable_mock_llm(monkeypatch, calls, lambda _locked_text, _locks: "translated")
    context = make_context(
        sample_db, config=BotConfig(rate_limit_per_minute=1, owner_user_id=11)
    )
    en_update, en_message = channel_update(
        text="Patch notes are live now!", message_id=4010
    )

    asyncio.run(channel_post_handler(en_update, context))

    assert en_message.replies == []
    assert calls == []

    cn_update, cn_message = channel_update(text=CN_TEXT, message_id=4011)
    asyncio.run(channel_post_handler(cn_update, context))

    assert cn_message.replies == [("translated", "HTML", 4011)]
    assert len(calls) == 1


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


def test_budget_exhaustion_is_silent_with_one_clean_warning(
    monkeypatch, sample_db, caplog
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    def fake_call(_locked_text, _locks, html_mode=False):
        raise _llm_error_from_response(
            httpx.Response(429, text='{"error":"max_budget exceeded"}')
        )

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
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
