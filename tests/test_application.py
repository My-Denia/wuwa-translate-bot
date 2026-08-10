"""Unit tests for the protocol-neutral application layer.

These exercise the shared pipeline directly, without any adapter: no Telegram
objects, no HTTP. Adapter-specific behavior (bilingual notices, HTML parse
mode) is covered by tests/test_bot.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wuwaterm.application import (
    ERROR_INPUT_TOO_LONG,
    ERROR_LLM_BUDGET_EXHAUSTED,
    ERROR_LLM_UNAVAILABLE,
    KIND_ERROR,
    KIND_EXACT,
    KIND_FUZZY,
    KIND_LLM,
    KIND_NOOP,
    TRANSLATION_ERROR_CODES,
    MarkupTranslation,
    ServiceMetadata,
    SlidingWindowRateLimiter,
    TranslationJob,
    build_term_service,
    build_translator,
    error_code_for_llm_reason,
    input_too_long_message,
    lookup_exact_terms,
    probe_database,
    service_metadata,
    split_plain_text,
    translate_request,
    translate_request_async,
)
from wuwaterm.sentence import (
    BUDGET_EXHAUSTED_NOTICE,
    TRANSLATION_UNAVAILABLE_NOTICE,
    LLMTranslationError,
    SentenceTranslator,
)
from wuwaterm.translation_policy import LLM_INPUT_CHAR_LIMIT


def build_pair(db_path: Path) -> tuple[object, SentenceTranslator]:
    return build_term_service(db_path), build_translator(db_path)


def enable_mock_llm(monkeypatch, calls, response_factory) -> None:
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


# --------------------------------------------------------------------------
# Dictionary-first stages
# --------------------------------------------------------------------------


def test_blank_input_is_a_noop(sample_db):
    service, translator = build_pair(sample_db)

    outcome = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="   "))
    )

    assert outcome.kind == KIND_NOOP
    assert outcome.error_code is None
    assert outcome.text


def test_exact_hit_returns_official_english(sample_db):
    service, translator = build_pair(sample_db)

    outcome = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="声骸"))
    )

    assert outcome.kind == KIND_EXACT
    assert outcome.text == "Echo"
    assert outcome.direction == "en"


def test_forced_direction_overrides_detection(sample_db):
    service, translator = build_pair(sample_db)

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="Echo", forced_to_chinese=True),
        )
    )

    assert outcome.kind == KIND_EXACT
    assert outcome.text == "声骸"
    assert outcome.to_chinese is True
    assert outcome.direction == "zh"


def test_direction_is_auto_detected_from_script(sample_db):
    service, translator = build_pair(sample_db)

    to_english = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="声骸"))
    )
    to_chinese = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="Echo"))
    )

    assert to_english.to_chinese is False
    assert to_chinese.to_chinese is True


def test_fuzzy_pinyin_answer_and_length_gate(sample_db):
    service, translator = build_pair(sample_db)

    # ASCII input auto-detects "translate into Chinese", so the fuzzy pinyin
    # hit answers with the official Chinese string.
    hit = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="shenghai"))
    )
    assert hit.kind == KIND_FUZZY
    assert hit.text == "声骸"

    forced = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="shenghai", forced_to_chinese=False),
        )
    )
    assert forced.kind == KIND_FUZZY
    assert forced.text == "Echo"

    # Short prefix/substring shapes must not hijack ordinary English words.
    calls: list[tuple[str, object]] = []
    miss = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="he"))
    )
    assert miss.kind != KIND_FUZZY
    assert not calls


def test_input_over_limit_returns_stable_error_code(sample_db):
    service, translator = build_pair(sample_db)
    long_text = "中" * (LLM_INPUT_CHAR_LIMIT + 1)

    outcome = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text=long_text))
    )

    assert outcome.kind == KIND_ERROR
    assert outcome.error_code == ERROR_INPUT_TOO_LONG
    assert outcome.error_code in TRANSLATION_ERROR_CODES
    assert outcome.text == input_too_long_message(LLM_INPUT_CHAR_LIMIT)


def test_trusted_input_limit_splits_instead_of_rejecting(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: "chunk")
    long_text = "\n".join("句子" * 100 for _ in range(20))
    assert len(long_text) > LLM_INPUT_CHAR_LIMIT

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text=long_text),
            input_limit=LLM_INPUT_CHAR_LIMIT * 4,
        )
    )

    assert outcome.kind == KIND_LLM
    assert len(calls) > 1
    assert outcome.text == "\n".join(["chunk"] * len(calls))


def test_custom_splitter_is_used_for_long_text(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: locked_text)
    seen: list[int] = []

    def splitter(text: str, limit: int) -> list[str]:
        seen.append(limit)
        return ["alpha", "beta"]

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="拓" * (LLM_INPUT_CHAR_LIMIT + 5)),
            input_limit=LLM_INPUT_CHAR_LIMIT * 4,
            splitter=splitter,
        )
    )

    assert seen == [LLM_INPUT_CHAR_LIMIT]
    assert outcome.kind == KIND_LLM
    assert outcome.text == "alpha\nbeta"


# --------------------------------------------------------------------------
# LLM path and error taxonomy
# --------------------------------------------------------------------------


def test_llm_path_reports_dictionary_miss_without_wording(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: "widget")

    outcome = asyncio.run(
        translate_request_async(service, translator, TranslationJob(text="foobar"))
    )

    assert outcome.kind == KIND_LLM
    # The application reports the fact; the wording belongs to the adapter.
    assert outcome.dictionary_miss is True
    assert outcome.text == "widget"


def test_llm_failure_maps_to_stable_error_codes(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)

    def fail(reason: str, message: str):
        async def raiser(*args, **kwargs):
            raise LLMTranslationError(message, reason=reason)

        return raiser

    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    monkeypatch.setattr(
        "wuwaterm.sentence._call_llm_async",
        fail("upstream", TRANSLATION_UNAVAILABLE_NOTICE),
    )
    unavailable = asyncio.run(
        translate_request_async(
            service, translator, TranslationJob(text="一个需要翻译的句子。")
        )
    )
    assert unavailable.kind == KIND_ERROR
    assert unavailable.error_code == ERROR_LLM_UNAVAILABLE

    monkeypatch.setattr(
        "wuwaterm.sentence._call_llm_async",
        fail("budget", BUDGET_EXHAUSTED_NOTICE),
    )
    budget = asyncio.run(
        translate_request_async(
            service, translator, TranslationJob(text="另一个需要翻译的句子。")
        )
    )
    assert budget.kind == KIND_ERROR
    assert budget.error_code == ERROR_LLM_BUDGET_EXHAUSTED


def test_error_code_for_llm_reason_defaults_to_unavailable():
    assert error_code_for_llm_reason("budget") == ERROR_LLM_BUDGET_EXHAUSTED
    for reason in ("timeout", "connect", "http", "rate_limit", None):
        assert error_code_for_llm_reason(reason) == ERROR_LLM_UNAVAILABLE


def test_sync_pipeline_matches_async_dictionary_stages(sample_db):
    service, translator = build_pair(sample_db)

    outcome = translate_request(service, translator, TranslationJob(text="声骸"))

    assert outcome.kind == KIND_EXACT
    assert outcome.text == "Echo"


# --------------------------------------------------------------------------
# Markup hook injection
# --------------------------------------------------------------------------


def test_markup_hook_result_is_used_when_supplied(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: "plain")
    seen: list[tuple[str, bool]] = []

    async def markup_translator(markup: str, *, to_chinese: bool) -> MarkupTranslation:
        seen.append((markup, to_chinese))
        return MarkupTranslation(text="<b>rich</b>")

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="需要翻译的句子", markup="<b>需要翻译的句子</b>"),
            markup_translator=markup_translator,
        )
    )

    assert seen == [("<b>需要翻译的句子</b>", False)]
    assert outcome.markup_used is True
    assert outcome.text == "<b>rich</b>"
    assert not calls


def test_no_markup_hook_means_plain_only(monkeypatch, sample_db):
    """The HTTP adapter injects no markup hook and must never get markup back."""
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: "plain answer")

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="需要翻译的句子", markup="<b>需要翻译的句子</b>"),
        )
    )

    assert outcome.markup_used is False
    assert outcome.text == "plain answer"
    assert len(calls) == 1


def test_markup_hook_can_request_plain_fallback(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: "plain answer")

    async def markup_translator(markup: str, *, to_chinese: bool) -> MarkupTranslation:
        return MarkupTranslation(fallback_to_plain=True)

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="需要翻译的句子", markup="<b>需要翻译的句子</b>"),
            markup_translator=markup_translator,
        )
    )

    assert outcome.markup_used is False
    assert outcome.kind == KIND_LLM
    assert outcome.text == "plain answer"


def test_markup_hook_hard_failure_stops_the_pipeline(monkeypatch, sample_db):
    service, translator = build_pair(sample_db)
    calls: list[tuple[str, object]] = []
    enable_mock_llm(monkeypatch, calls, lambda locked_text, locks: "plain answer")

    async def markup_translator(markup: str, *, to_chinese: bool) -> MarkupTranslation:
        return MarkupTranslation(
            message="upstream refused", error_code=ERROR_LLM_UNAVAILABLE
        )

    outcome = asyncio.run(
        translate_request_async(
            service,
            translator,
            TranslationJob(text="需要翻译的句子", markup="<b>需要翻译的句子</b>"),
            markup_translator=markup_translator,
        )
    )

    assert outcome.kind == KIND_ERROR
    assert outcome.error_code == ERROR_LLM_UNAVAILABLE
    assert outcome.text == "upstream refused"
    assert not calls


# --------------------------------------------------------------------------
# Splitter, lookup, metadata, budgets
# --------------------------------------------------------------------------


def test_split_plain_text_never_exceeds_limit():
    text = "\n".join(["short line", "x" * 25, "another"])

    chunks = split_plain_text(text, limit=10)

    assert chunks
    assert all(len(chunk) <= 10 for chunk in chunks)
    assert "".join(chunk.replace("\n", "") for chunk in chunks) == text.replace("\n", "")


@pytest.mark.parametrize(
    "text",
    ["\n" * 50, "   ", "single", "a\n\nb", "x" * 40],
)
def test_split_plain_text_is_never_empty_for_non_empty_input(text: str):
    chunks = split_plain_text(text, limit=10)

    assert chunks, text
    assert all(len(chunk) <= 10 for chunk in chunks)


def test_split_plain_text_is_empty_only_for_empty_input():
    assert split_plain_text("", limit=10) == []


def test_sync_pipeline_separates_budget_exhaustion_from_unavailability(
    monkeypatch, sample_db
):
    """The sync entry point must classify failures like the async one."""
    service, translator = build_pair(sample_db)
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    def raise_budget(*args, **kwargs):
        raise LLMTranslationError(BUDGET_EXHAUSTED_NOTICE, reason="budget")

    monkeypatch.setattr("wuwaterm.sentence._call_llm", raise_budget)
    budget = translate_request(
        service, translator, TranslationJob(text="一个需要翻译的句子。")
    )

    def raise_upstream(*args, **kwargs):
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE, reason="upstream")

    monkeypatch.setattr("wuwaterm.sentence._call_llm", raise_upstream)
    unavailable = translate_request(
        service, translator, TranslationJob(text="另一个需要翻译的句子。")
    )

    assert budget.kind == KIND_ERROR
    assert budget.error_code == ERROR_LLM_BUDGET_EXHAUSTED
    assert unavailable.kind == KIND_ERROR
    assert unavailable.error_code == ERROR_LLM_UNAVAILABLE


def test_lookup_exact_terms_returns_official_strings(sample_db):
    service = build_term_service(sample_db)

    matches = lookup_exact_terms(service, "声骸")

    assert matches
    assert matches[0].zh == "声骸"
    assert matches[0].en == "Echo"
    assert matches[0].score == 100.0


def test_lookup_exact_terms_is_empty_for_unknown_query(sample_db):
    service = build_term_service(sample_db)

    assert lookup_exact_terms(service, "not-a-term") == []


def test_service_metadata_exposes_no_paths_or_secrets(sample_db):
    service = build_term_service(sample_db)

    meta = service_metadata(service)

    assert isinstance(meta, ServiceMetadata)
    assert meta.term_count > 0
    assert meta.source_profile == "dimbreath_legacy"
    assert meta.schema_version
    rendered = repr(meta)
    assert str(sample_db) not in rendered
    assert "terms.db" not in rendered


def test_probe_database_reports_unreadable_database(tmp_path: Path, sample_db):
    assert probe_database(build_term_service(sample_db)) is True
    assert probe_database(build_term_service(tmp_path / "missing.db")) is False


def test_sliding_window_rate_limiter_is_per_key_and_time_bounded():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60.0)

    assert limiter.allow("device-a", now=0.0) is True
    assert limiter.allow("device-a", now=1.0) is True
    assert limiter.allow("device-a", now=2.0) is False
    # A different principal has its own window.
    assert limiter.allow("device-b", now=2.0) is True
    # And the window slides.
    assert limiter.allow("device-a", now=62.0) is True


def test_sliding_window_rate_limiter_accepts_integer_keys():
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60.0)

    assert limiter.allow(4242, now=0.0) is True
    assert limiter.allow(4242, now=1.0) is False


def test_build_translator_uses_supplied_budgets(sample_db):
    translator = build_translator(sample_db, timeout=7.5, max_concurrency=3)

    assert translator.llm_timeout_seconds == 7.5
    assert translator.llm_max_concurrency == 3


@pytest.mark.parametrize("code", sorted(TRANSLATION_ERROR_CODES))
def test_error_codes_are_lowercase_snake_case(code: str):
    assert code == code.lower()
    assert code.replace("_", "").isalpha()
