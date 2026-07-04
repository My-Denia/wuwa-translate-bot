from __future__ import annotations

import asyncio

import httpx
import pytest

from wuwaterm.db import connect, insert_records
from wuwaterm.models import TermRecord
from wuwaterm.sentence import (
    BUDGET_EXHAUSTED_NOTICE,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    TRANSLATION_UNAVAILABLE_NOTICE,
    LLMTranslationError,
    SentenceTranslator,
    _call_llm_async,
    _llm_error_from_response,
)


def add_synthetic_terms(sample_db, records):
    with connect(sample_db) as conn:
        insert_records(conn, records)
        conn.commit()


def enable_llm_env(monkeypatch):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")


def test_sentence_locks_known_terms_without_llm(monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)

    translator = SentenceTranslator(sample_db)

    assert translator.translate("今汐装备了声骸") == "Jinhsi装备了Echo"


def test_sync_translate_llm_runs_outside_event_loop(monkeypatch, sample_db):
    enable_llm_env(monkeypatch)

    async def fake_call(
        locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
        transport=None,
    ):
        return "translated"

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    translator = SentenceTranslator(sample_db)

    assert translator.translate("这是一句普通文本") == "translated"


def test_sync_translate_without_llm_still_works_inside_event_loop(monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)
    translator = SentenceTranslator(sample_db)

    async def run():
        return translator.translate("今汐装备了声骸")

    assert asyncio.run(run()) == "Jinhsi装备了Echo"


def test_sync_translate_with_llm_inside_event_loop_raises_clear_error(
    monkeypatch, sample_db
):
    enable_llm_env(monkeypatch)

    async def fake_call(*args, **kwargs):
        raise AssertionError("sync wrapper should fail before calling the LLM")

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    translator = SentenceTranslator(sample_db)

    async def run():
        with pytest.raises(RuntimeError, match="translate_async"):
            translator.translate("这是一句普通文本")

    asyncio.run(run())


def test_async_translate_with_llm_inside_event_loop_still_works(monkeypatch, sample_db):
    enable_llm_env(monkeypatch)

    async def fake_call(
        locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
        transport=None,
    ):
        return "translated"

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    translator = SentenceTranslator(sample_db)

    async def run():
        return await translator.translate_async("这是一句普通文本")

    assert asyncio.run(run()) == "translated"


def test_lock_placeholders_restore_official_terms(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("今汐和共鸣者")

    by_en = {en: placeholder for placeholder, _zh, en in locked.locks}
    restored = locked.restore(f"Use {by_en['Jinhsi']} plus {by_en['Resonator']}")
    assert set(restored.removeprefix("Use ").split(" plus ")) == {"Jinhsi", "Resonator"}


def test_sentence_locking_reuses_lookup_exact_decision(sample_db):
    translator = SentenceTranslator(sample_db)

    assert translator.translate("巧手烹调") == "Skillful Cooking"


def test_prepare_text_strips_screenshot_noise_and_resolves_speaker(sample_db):
    translator = SentenceTranslator(sample_db)

    assert translator.prepare_text("(WW 3.4)\n[spoiler]\n> 洛瑟菈：声骸") == "Lucilla: 声骸"


def test_sentence_locks_english_official_terms_after_nfkc(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("Ｃａｒｔｅｔｈｙｉａ和声骸")

    assert locked.locks
    restored = locked.restore(locked.locked_text)
    assert restored == "Cartethyia和Echo"



def test_literal_legacy_placeholder_text_is_preserved(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    def fake_call(locked_text, locks, html_mode=False, to_chinese=False):
        assert "__TERM_0__" in locked_text
        assert len(locks) == 1
        return f"literal __TERM_0__ plus {locks[0][0]}"

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    translator = SentenceTranslator(sample_db)

    assert translator.translate("__TERM_0__ 声骸") == "literal __TERM_0__ plus Echo"


def test_literal_new_placeholder_like_text_is_preserved(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")
    literal = "__WUWA_TERM_user_0000__"

    def fake_call(locked_text, locks, html_mode=False, to_chinese=False):
        assert literal in locked_text
        assert len(locks) == 1
        return f"{literal} plus {locks[0][0]}"

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    translator = SentenceTranslator(sample_db)

    assert translator.translate(f"{literal} 声骸") == f"{literal} plus Echo"


def test_html_literal_legacy_placeholder_text_is_preserved(monkeypatch, sample_db):
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
        assert html_mode is True
        assert "__TERM_0__" in locked_text
        assert len(locks) == 1
        return f"<b>__TERM_0__</b>{locks[0][0]}"

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    translator = SentenceTranslator(sample_db)

    async def run():
        return await translator.translate_html_async("<b>__TERM_0__</b>声骸")

    assert asyncio.run(run()) == "<b>__TERM_0__</b>Echo"


def test_repeated_terms_get_distinct_placeholders(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("声骸 声骸")

    placeholders = [placeholder for placeholder, _zh, en in locked.locks if en == "Echo"]
    assert len(placeholders) == 2
    assert len(set(placeholders)) == 2
    assert locked.restore(locked.locked_text) == "Echo Echo"


def test_restore_rejects_missing_duplicate_or_modified_placeholders(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("今汐说声骸")
    placeholders = [placeholder for placeholder, _zh, _en in locked.locks]
    assert len(placeholders) == 2

    with pytest.raises(LLMTranslationError) as all_missing:
        locked.restore("translated without locked terms")
    assert all_missing.value.user_message == TRANSLATION_UNAVAILABLE_NOTICE

    with pytest.raises(LLMTranslationError) as missing:
        locked.restore(placeholders[0])
    assert missing.value.user_message == TRANSLATION_UNAVAILABLE_NOTICE

    with pytest.raises(LLMTranslationError) as duplicate:
        locked.restore(f"{placeholders[0]} {placeholders[0]} {placeholders[1]}")
    assert duplicate.value.user_message == TRANSLATION_UNAVAILABLE_NOTICE

    with pytest.raises(LLMTranslationError) as modified:
        locked.restore(f"{placeholders[0].lower()} {placeholders[1]}")
    assert modified.value.user_message == TRANSLATION_UNAVAILABLE_NOTICE


def test_equal_length_overlapping_terms_use_stable_order(sample_db):
    add_synthetic_terms(
        sample_db,
        [
            TermRecord("term", "Synthetic.json", "1", "Synthetic_1", "甲乙", "Left"),
            TermRecord("term", "Synthetic.json", "2", "Synthetic_2", "乙丙", "Right"),
        ],
    )
    translator = SentenceTranslator(sample_db)
    outputs = []
    for _ in range(5):
        locked = translator.lock_terms("甲乙丙")
        outputs.append(locked.restore(locked.locked_text))

    assert outputs == ["甲Right"] * 5


def test_longer_overlapping_term_wins(sample_db):
    add_synthetic_terms(
        sample_db,
        [
            TermRecord("term", "Synthetic.json", "1", "Synthetic_1", "甲乙", "Short"),
            TermRecord("term", "Synthetic.json", "2", "Synthetic_2", "甲乙丙", "Long"),
        ],
    )
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("甲乙丙")

    assert locked.restore(locked.locked_text) == "Long"


def test_later_starting_longer_overlapping_term_wins(sample_db):
    add_synthetic_terms(
        sample_db,
        [
            TermRecord("term", "Synthetic.json", "1", "Synthetic_1", "甲乙", "Short"),
            TermRecord("term", "Synthetic.json", "2", "Synthetic_2", "乙丙丁", "Long"),
        ],
    )
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("甲乙丙丁")

    assert locked.restore(locked.locked_text) == "甲Long"


def test_equal_length_overlapping_english_terms_use_stable_order(sample_db):
    add_synthetic_terms(
        sample_db,
        [
            TermRecord("term", "Synthetic.json", "1", "Synthetic_1", "左项", "AB"),
            TermRecord("term", "Synthetic.json", "2", "Synthetic_2", "右项", "BC"),
        ],
    )
    translator = SentenceTranslator(sample_db)
    outputs = []
    for _ in range(5):
        locked = translator.lock_terms("ABC")
        outputs.append(locked.restore(locked.locked_text, to_en=False))

    assert outputs == ["A右项"] * 5


def test_mixed_chinese_and_english_terms_restore(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("今汐 uses Echo")

    assert locked.restore(locked.locked_text) == "Jinhsi uses Echo"


def test_html_path_overlapping_terms_selects_longer_later_span(sample_db):
    add_synthetic_terms(
        sample_db,
        [
            TermRecord("term", "Synthetic.json", "1", "Synthetic_1", "甲乙", "Short"),
            TermRecord("term", "Synthetic.json", "2", "Synthetic_2", "乙丙丁", "Long"),
        ],
    )
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("<b>甲乙丙丁</b>")

    assert locked.restore(locked.locked_text) == "<b>甲Long</b>"


def test_budget_exhaustion_returns_clean_user_notice(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    error = _llm_error_from_response(
        httpx.Response(429, text='{"error":"max_budget exceeded"}')
    )
    assert error.user_message == BUDGET_EXHAUSTED_NOTICE

    def fake_call(_locked_text, _locks):
        raise LLMTranslationError(BUDGET_EXHAUSTED_NOTICE)

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    translator = SentenceTranslator(sample_db)

    assert translator.translate("这是一个需要翻译的句子。") == BUDGET_EXHAUSTED_NOTICE


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            400,
            json={
                "error": {
                    "code": "context_length_exceeded",
                    "message": "context length exceeded",
                }
            },
        ),
        httpx.Response(
            400,
            json={"error": {"message": "maximum context length exceeded"}},
        ),
        httpx.Response(400, json={"error": {"message": "input too long"}}),
        httpx.Response(400, text="context length exceeded"),
        httpx.Response(400, text="exceeded"),
        httpx.Response(400, json={"detail": "maximum context length exceeded"}),
        httpx.Response(
            500,
            json={"error": {"code": "insufficient_quota", "message": "quota exceeded"}},
        ),
    ],
)
def test_llm_context_and_server_errors_do_not_report_budget(response):
    error = _llm_error_from_response(response)

    assert error.user_message == TRANSLATION_UNAVAILABLE_NOTICE


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            429,
            json={
                "error": {
                    "code": "insufficient_quota",
                    "message": "You exceeded your current quota.",
                }
            },
        ),
        httpx.Response(429, json={"error": {"message": "quota exceeded"}}),
        httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}),
        httpx.Response(400, json={"error": {"code": "billing_hard_limit_reached"}}),
        httpx.Response(
            429,
            json={
                "detail": "Authentication Error, ExceededTokenBudget: Max Budget reached"
            },
        ),
        httpx.Response(429, text="Too Many Requests"),
        httpx.Response(429),
        httpx.Response(429, text='{"error":"max_budget exceeded"}'),
    ],
)
def test_llm_budget_and_quota_errors_keep_budget_notice(response):
    error = _llm_error_from_response(response)

    assert error.user_message == BUDGET_EXHAUSTED_NOTICE


def test_generic_llm_failure_returns_bilingual_unavailable_notice(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    # A non-budget failure (e.g. 500) routes to the unavailable notice, bilingual.
    error = _llm_error_from_response(httpx.Response(500, text="internal server error"))
    assert error.user_message == TRANSLATION_UNAVAILABLE_NOTICE
    assert TRANSLATION_UNAVAILABLE_NOTICE == (
        "翻译服务暂时不可用，请稍后再试。\n"
        "Translation service is temporarily unavailable. Please try again later."
    )

    def fake_call(_locked_text, _locks):
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    translator = SentenceTranslator(sample_db)

    assert translator.translate("这是一个需要翻译的句子。") == TRANSLATION_UNAVAILABLE_NOTICE


@pytest.mark.parametrize(
    "transport",
    [
        httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                httpx.ConnectError("connect failed", request=request)
            )
        ),
        httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                httpx.ReadTimeout("read timed out", request=request)
            )
        ),
        httpx.MockTransport(lambda _request: httpx.Response(200, text="{not-json")),
        httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
        httpx.MockTransport(lambda _request: httpx.Response(200, json={"choices": []})),
        httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"choices": [{"message": {}}]})
        ),
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"choices": [{"message": {"content": "   "}}]}
            )
        ),
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"choices": [{"message": {"content": "\n\n"}}]}
            )
        ),
    ],
)
def test_async_llm_failures_have_one_unavailable_error(monkeypatch, transport):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        with pytest.raises(LLMTranslationError) as exc_info:
            await _call_llm_async("hello", (), transport=transport)
        assert exc_info.value.user_message == TRANSLATION_UNAVAILABLE_NOTICE

    asyncio.run(run())


@pytest.mark.parametrize("llm_output", ["", "   ", "\n\n"])
def test_html_translation_blank_llm_output_is_unavailable(
    monkeypatch, sample_db, llm_output
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
        assert html_mode is True
        return llm_output

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    translator = SentenceTranslator(sample_db)

    async def run():
        with pytest.raises(LLMTranslationError) as exc_info:
            await translator.translate_html_async("<b>今汐</b>说声骸很强")
        assert exc_info.value.user_message == TRANSLATION_UNAVAILABLE_NOTICE

    asyncio.run(run())


def test_async_llm_budget_error_keeps_budget_notice(monkeypatch):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(429, text='{"error":"max_budget exceeded"}')
    )

    async def run():
        with pytest.raises(LLMTranslationError) as exc_info:
            await _call_llm_async("hello", (), transport=transport)
        assert exc_info.value.user_message == BUDGET_EXHAUSTED_NOTICE

    asyncio.run(run())


def test_sync_translator_passes_configured_timeout(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")
    seen: list[float] = []

    def fake_call(
        locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
    ):
        seen.append(timeout_seconds)
        return "translated"

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    translator = SentenceTranslator(sample_db, llm_timeout_seconds=7.5)

    assert translator.translate("这是一个需要翻译的句子。") == "translated"
    assert seen == [7.5]


def test_async_translator_uses_default_timeout(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")
    seen: list[float] = []

    async def fake_call(
        _locked_text,
        _locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
        transport=None,
    ):
        seen.append(timeout_seconds)
        return "translated"

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
    translator = SentenceTranslator(sample_db)

    async def run():
        return await translator.translate_async("这是一个需要翻译的句子。")

    assert asyncio.run(run()) == "translated"
    assert seen == [DEFAULT_LLM_TIMEOUT_SECONDS]
    assert DEFAULT_LLM_TIMEOUT_SECONDS == 45.0


def test_cancelled_async_translation_releases_concurrency_slot(
    monkeypatch, sample_db
):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def run():
        started = asyncio.Event()
        calls = 0

        async def fake_call(
            locked_text,
            locks,
            html_mode=False,
            to_chinese=False,
            timeout_seconds=30.0,
            transport=None,
        ):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await asyncio.Event().wait()
            by_en = {en: placeholder for placeholder, _zh, en in locks}
            return f"{by_en['Jinhsi']} says {by_en['Echo']}"

        monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)
        translator = SentenceTranslator(sample_db, llm_max_concurrency=1)
        first = asyncio.create_task(translator.translate_async("今汐说声骸很强"))
        await asyncio.wait_for(started.wait(), timeout=0.2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        result = await asyncio.wait_for(
            translator.translate_async("今汐说声骸很强"), timeout=0.2
        )
        assert result == "Jinhsi says Echo"
        assert calls == 2

    asyncio.run(run())


def test_translate_english_exact_term_returns_official_chinese(sample_db):
    translator = SentenceTranslator(sample_db)

    # Exact English term, dictionary-first, no LLM: returns the official zh.
    assert translator.translate("Echo", to_chinese=True) == "声骸"


def test_restore_to_chinese_swaps_in_zh_official(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("Use Jinhsi plus Resonator")

    by_en = {en: placeholder for placeholder, _zh, en in locked.locks}
    restored = locked.restore(f"Use {by_en['Jinhsi']} plus {by_en['Resonator']}", to_en=False)
    assert set(restored.removeprefix("Use ").split(" plus ")) == {"今汐", "共鸣者"}


def test_translate_english_sentence_locks_and_restores_chinese(monkeypatch, sample_db):
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    def fake_call(locked_text, locks, html_mode=False, to_chinese=False):
        # English source -> Chinese target: the model keeps placeholders and
        # is shown the zh official; restore swaps placeholders to zh.
        assert to_chinese is True
        assert "Jinhsi" not in locked_text and "Echo" not in locked_text
        by_en = {en: placeholder for placeholder, _zh, en in locks}
        return f"{by_en['Jinhsi']}装备了{by_en['Echo']}"

    monkeypatch.setattr("wuwaterm.sentence._call_llm", fake_call)
    translator = SentenceTranslator(sample_db)

    assert (
        translator.translate("Jinhsi equips Echo", to_chinese=True) == "今汐装备了声骸"
    )
