from __future__ import annotations

import httpx

from wuwaterm.sentence import (
    BUDGET_EXHAUSTED_NOTICE,
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
