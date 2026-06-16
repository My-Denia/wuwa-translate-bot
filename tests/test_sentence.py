from __future__ import annotations

import httpx

from wuwaterm.sentence import (
    BUDGET_EXHAUSTED_NOTICE,
    TRANSLATION_UNAVAILABLE_NOTICE,
    LLMTranslationError,
    SentenceTranslator,
    _llm_error_from_response,
)


def test_sentence_locks_known_terms_without_llm(monkeypatch, sample_db):
    monkeypatch.delenv("WUWATERM_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("WUWATERM_OPENAI_MODEL", raising=False)

    translator = SentenceTranslator(sample_db)

    assert translator.translate("今汐装备了声骸") == "Jinhsi装备了Echo"


def test_lock_placeholders_restore_official_terms(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("今汐和共鸣者")

    assert "__TERM_0__" in locked.locked_text
    restored = locked.restore("Use __TERM_0__ plus __TERM_1__")
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

    assert "__TERM_" in locked.locked_text
    restored = locked.restore(locked.locked_text)
    assert restored == "Cartethyia和Echo"


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


def test_translate_english_exact_term_returns_official_chinese(sample_db):
    translator = SentenceTranslator(sample_db)

    # Exact English term, dictionary-first, no LLM: returns the official zh.
    assert translator.translate("Echo", to_chinese=True) == "声骸"


def test_restore_to_chinese_swaps_in_zh_official(sample_db):
    translator = SentenceTranslator(sample_db)
    locked = translator.lock_terms("Use Jinhsi plus Resonator")

    restored = locked.restore("Use __TERM_0__ plus __TERM_1__", to_en=False)
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
