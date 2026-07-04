"""Term locking for sentence translation."""

from __future__ import annotations

import os
import secrets
import re
import json
import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .lookup import TermService
from .normalize import normalize_user_text


SPEAKER_PREFIX_RE = re.compile(r"^(?P<speaker>[^:：\n]{1,40})\s*[:：]\s*(?P<body>.*)$")
# Bilingual user-facing notices: Chinese line first, then English (single "\n").
BUDGET_EXHAUSTED_NOTICE = (
    "本月翻译额度已用完,请稍后再试。\n"
    "This month's translation quota is used up. Please try again later."
)
TRANSLATION_UNAVAILABLE_NOTICE = (
    "翻译服务暂时不可用，请稍后再试。\n"
    "Translation service is temporarily unavailable. Please try again later."
)
DEFAULT_LLM_TIMEOUT_SECONDS = 45.0
DEFAULT_LLM_MAX_CONCURRENCY = 4
SYNC_TRANSLATION_RUNNING_LOOP_ERROR = (
    "Synchronous sentence translation cannot call the LLM from a running "
    "event loop; use translate_async() or translate_html_async() instead."
)


class LLMTranslationError(RuntimeError):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class LockedSentence:
    locked_text: str
    locks: tuple[tuple[str, str, str], ...]

    def restore(self, translated: str, *, to_en: bool = True) -> str:
        result = translated
        for placeholder, zh, en in self.locks:
            if result.count(placeholder) != 1:
                raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)
            result = result.replace(placeholder, en if to_en else zh)
        return result


@dataclass(frozen=True)
class _TermSpan:
    start: int
    end: int
    source: str
    official: tuple[str, str]
    order: int


def _require_nonblank_llm_output(content: str) -> str:
    if not isinstance(content, str):
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)
    normalized = content.strip()
    if not normalized:
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)
    return normalized


def _new_placeholder_prefix(source_text: str) -> str:
    while True:
        prefix = f"__WUWA_TERM_{secrets.token_hex(8)}_"
        if prefix not in source_text:
            return prefix


class SentenceTranslator:
    def __init__(
        self,
        db_path: str | Path,
        *,
        llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        llm_max_concurrency: int = DEFAULT_LLM_MAX_CONCURRENCY,
    ):
        self.service = TermService(db_path)
        self.llm_timeout_seconds = max(0.1, float(llm_timeout_seconds))
        self.llm_max_concurrency = max(1, int(llm_max_concurrency))
        self._llm_slots: asyncio.Semaphore | None = None
        self._llm_slots_loop: asyncio.AbstractEventLoop | None = None

    def prepare_text(self, text: str) -> str:
        text = normalize_user_text(text)
        if not text:
            return ""
        return "\n".join(self._resolve_speaker_prefix(line) for line in text.splitlines())

    def lock_terms(self, text: str) -> LockedSentence:
        return self._lock_terms(self.prepare_text(text))

    def _lock_terms(self, text: str) -> LockedSentence:
        lockable = [
            (source, official)
            for source, official in self._lockable_sources()
            if len(source) >= 2
            and official[0]
            and official[1]
            and "\n" not in official[0]
            and "\n" not in official[1]
        ]
        spans: list[_TermSpan] = []
        for order, (source, official) in enumerate(lockable):
            start = text.find(source)
            while start != -1:
                spans.append(
                    _TermSpan(
                        start=start,
                        end=start + len(source),
                        source=source,
                        official=official,
                        order=order,
                    )
                )
                start = text.find(source, start + 1)

        # Select global longest non-overlapping official term spans. This
        # intentionally prioritizes the longest single term, not maximum total
        # coverage. Equal-length overlaps follow dictionary iteration order,
        # then start position, then source text.
        selected: list[_TermSpan] = []
        occupied: list[tuple[int, int]] = []
        for span in sorted(
            spans,
            key=lambda item: (
                -(item.end - item.start),
                item.order,
                item.start,
                item.source,
            ),
        ):
            if any(span.start < end and start < span.end for start, end in occupied):
                continue
            selected.append(span)
            occupied.append((span.start, span.end))

        selected.sort(key=lambda item: item.start)
        prefix = _new_placeholder_prefix(text)
        locked_parts: list[str] = []
        locks: list[tuple[str, str, str]] = []
        index = 0
        for span in selected:
            locked_parts.append(text[index : span.start])
            placeholder = f"{prefix}{len(locks):04d}__"
            locked_parts.append(placeholder)
            zh, en = span.official
            locks.append((placeholder, zh, en))
            index = span.end
        locked_parts.append(text[index:])
        return LockedSentence(locked_text="".join(locked_parts), locks=tuple(locks))

    def translate(self, text: str, *, to_chinese: bool = False) -> str:
        """Translate plain text through the synchronous API.

        Async callers should use ``translate_async`` when LLM translation is
        configured; the sync API cannot run an LLM request inside an existing
        event loop.
        """
        prepared = self.prepare_text(text)
        if not prepared:
            return ""
        exact = self.service.lookup(prepared, limit=5)
        if exact.exact and exact.best:
            official = exact.best.entry.zh if to_chinese else exact.best.entry.en
            if official:
                return official
        locked = self._lock_terms(prepared)
        if not _llm_configured():
            return locked.restore(locked.locked_text, to_en=not to_chinese)
        try:
            if to_chinese:
                translated = self._call_llm_sync(
                    locked.locked_text, locked.locks, to_chinese=True
                )
            else:
                translated = self._call_llm_sync(locked.locked_text, locked.locks)
            translated = _require_nonblank_llm_output(translated)
            return locked.restore(translated, to_en=not to_chinese)
        except LLMTranslationError as exc:
            return exc.user_message

    async def translate_async(self, text: str, *, to_chinese: bool = False) -> str:
        prepared = self.prepare_text(text)
        if not prepared:
            return ""
        exact = self.service.lookup(prepared, limit=5)
        if exact.exact and exact.best:
            official = exact.best.entry.zh if to_chinese else exact.best.entry.en
            if official:
                return official
        locked = self._lock_terms(prepared)
        if not _llm_configured():
            return locked.restore(locked.locked_text, to_en=not to_chinese)
        try:
            if to_chinese:
                translated = await self._call_llm_async_limited(
                    locked.locked_text, locked.locks, to_chinese=True
                )
            else:
                translated = await self._call_llm_async_limited(
                    locked.locked_text, locked.locks
                )
            translated = _require_nonblank_llm_output(translated)
            return locked.restore(translated, to_en=not to_chinese)
        except LLMTranslationError as exc:
            return exc.user_message

    def translate_html(self, html_text: str, *, to_chinese: bool = False) -> str:
        """Translate Telegram-HTML text with DB terms locked and tags untouched.

        Skips ``prepare_text``/``normalize_user_text`` on purpose: normalization
        would mangle tags and strip ``>`` quote bars. ``LLMTranslationError``
        propagates to the caller so the passive channel path can stay silent.
        Async callers should use ``translate_html_async``; the sync API cannot
        run an LLM request inside an existing event loop.
        """
        locked = self._lock_terms(html_text)
        if to_chinese:
            translated = self._call_llm_sync(
                locked.locked_text, locked.locks, html_mode=True, to_chinese=True
            )
        else:
            translated = self._call_llm_sync(
                locked.locked_text, locked.locks, html_mode=True
            )
        translated = _require_nonblank_llm_output(translated)
        return locked.restore(translated, to_en=not to_chinese)

    async def translate_html_async(
        self, html_text: str, *, to_chinese: bool = False
    ) -> str:
        """Async version of translate_html; LLMTranslationError propagates."""
        locked = self._lock_terms(html_text)
        if to_chinese:
            translated = await self._call_llm_async_limited(
                locked.locked_text,
                locked.locks,
                html_mode=True,
                to_chinese=True,
            )
        else:
            translated = await self._call_llm_async_limited(
                locked.locked_text, locked.locks, html_mode=True
            )
        translated = _require_nonblank_llm_output(translated)
        return locked.restore(translated, to_en=not to_chinese)

    async def _call_llm_async_limited(
        self,
        locked_text: str,
        locks: tuple[tuple[str, str, str], ...],
        html_mode: bool = False,
        to_chinese: bool = False,
    ) -> str:
        async with self._current_llm_slots():
            return await _call_llm_async(
                locked_text,
                locks,
                html_mode=html_mode,
                to_chinese=to_chinese,
                timeout_seconds=self.llm_timeout_seconds,
            )

    def _call_llm_sync(
        self,
        locked_text: str,
        locks: tuple[tuple[str, str, str], ...],
        html_mode: bool = False,
        to_chinese: bool = False,
    ) -> str:
        kwargs: dict[str, object] = {}
        supported = inspect.signature(_call_llm).parameters
        for name, value in (
            ("html_mode", html_mode),
            ("to_chinese", to_chinese),
            ("timeout_seconds", self.llm_timeout_seconds),
        ):
            if name in supported:
                kwargs[name] = value
        return _call_llm(locked_text, locks, **kwargs)

    def _current_llm_slots(self) -> asyncio.Semaphore:
        # Tests may create a translator once and drive it through multiple
        # asyncio.run loops; bind the semaphore lazily to the active loop.
        loop = asyncio.get_running_loop()
        if self._llm_slots is None or self._llm_slots_loop is not loop:
            self._llm_slots = asyncio.Semaphore(self.llm_max_concurrency)
            self._llm_slots_loop = loop
        return self._llm_slots

    def _resolve_speaker_prefix(self, line: str) -> str:
        match = SPEAKER_PREFIX_RE.match(line)
        if not match:
            return line
        speaker = match.group("speaker").strip()
        body = match.group("body").strip()
        official = self._exact_official(speaker)
        if not official:
            return line
        return f"{official}: {body}" if body else f"{official}:"

    def _exact_official(self, query: str) -> str | None:
        result = self.service.lookup(query, limit=5)
        if not result.exact:
            return None
        official = self.service.term_text(query)
        if not official or "\n" in official:
            return None
        return official

    def _lockable_sources(self) -> tuple[tuple[str, tuple[str, str]], ...]:
        # Each source text (Chinese OR English form) maps to the official
        # (zh, en) pair, so a locked placeholder can be restored in either
        # direction. Only terms whose both forms are single-line are lockable.
        sources: dict[str, tuple[str, str]] = {}
        for entry in self.service.entries():
            if "\n" in entry.zh or "\n" in entry.en:
                continue
            official = (entry.zh, entry.en)
            if entry.zh:
                sources.setdefault(entry.zh, official)
            if entry.en:
                sources.setdefault(entry.en, official)
        return tuple(sources.items())


def _llm_configured() -> bool:
    return bool(
        (os.getenv("WUWATERM_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL"))
        and (os.getenv("WUWATERM_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))
        and os.getenv("WUWATERM_OPENAI_MODEL")
    )


_HTML_MODE_INSTRUCTION = (
    "The input contains Telegram HTML tags (<b>, <i>, <u>, <s>, <a href>, "
    "<code>, <pre>, <blockquote>, <tg-spoiler>, <tg-emoji>); copy every tag "
    "and attribute through exactly unchanged, translate only the "
    "human-readable text between tags, and return English only with the "
    "same tags.\n"
)
_HTML_MODE_INSTRUCTION_ZH = (
    "The input contains Telegram HTML tags (<b>, <i>, <u>, <s>, <a href>, "
    "<code>, <pre>, <blockquote>, <tg-spoiler>, <tg-emoji>); copy every tag "
    "and attribute through exactly unchanged, translate only the "
    "human-readable text between tags, and return Chinese only with the "
    "same tags.\n"
)


def _ensure_sync_llm_outside_running_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(SYNC_TRANSLATION_RUNNING_LOOP_ERROR)


def _call_llm(
    locked_text: str,
    locks: tuple[tuple[str, str, str], ...],
    html_mode: bool = False,
    to_chinese: bool = False,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
) -> str:
    _ensure_sync_llm_outside_running_loop()
    return asyncio.run(
        _call_llm_async(
            locked_text,
            locks,
            html_mode=html_mode,
            to_chinese=to_chinese,
            timeout_seconds=timeout_seconds,
        )
    )


async def _call_llm_async(
    locked_text: str,
    locks: tuple[tuple[str, str, str], ...],
    html_mode: bool = False,
    to_chinese: bool = False,
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    base_url = (os.getenv("WUWATERM_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    api_key = os.getenv("WUWATERM_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    model = os.getenv("WUWATERM_OPENAI_MODEL") or ""
    # locks are (placeholder, zh_official, en_official); show the model the
    # official term in the TARGET language so the locked placeholder maps to
    # the right side on restore.
    official_index = 1 if to_chinese else 2
    lock_lines = "\n".join(f"{lock[0]} = {lock[official_index]}" for lock in locks)
    if to_chinese:
        system_content = (
            "Translate English Wuthering Waves text into Simplified Chinese. "
            "Keep all placeholders exactly unchanged. "
            "Do not paraphrase locked official terms. Return Chinese only.\n"
        )
        if html_mode:
            system_content += _HTML_MODE_INSTRUCTION_ZH
    else:
        system_content = (
            "Translate Chinese Wuthering Waves text into English. "
            "Keep all placeholders exactly unchanged. "
            "Do not paraphrase locked official terms. Return English only.\n"
        )
        if html_mode:
            system_content += _HTML_MODE_INSTRUCTION
    system_content += f"Locked terms:\n{lock_lines}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": locked_text},
        ],
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds), transport=transport
        ) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _llm_error_from_response(exc.response) from exc
        data = response.json()
        return _extract_llm_content(data)
    except LLMTranslationError:
        raise
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE) from exc
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE) from exc


def _extract_llm_content(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("LLM response is not an object")
    choices = data["choices"]
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("LLM choice is not an object")
    message = first["message"]
    if not isinstance(message, dict):
        raise ValueError("LLM message is not an object")
    content = message["content"]
    if not isinstance(content, str):
        raise ValueError("LLM content is not text")
    return _require_nonblank_llm_output(content)


def _llm_error_from_response(response: httpx.Response) -> LLMTranslationError:
    if response.status_code >= 500:
        return LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)
    if response.status_code == 429 or _has_structured_budget_signal(response):
        return LLMTranslationError(BUDGET_EXHAUSTED_NOTICE)
    return LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)


def _has_structured_budget_signal(response: httpx.Response) -> bool:
    text = _structured_llm_error_text(response)
    if not text:
        return False
    # Do not match generic "exceeded": context-length errors commonly use that
    # word but are ordinary translation failures, not quota exhaustion.
    return any(
        marker in text
        for marker in (
            "budget",
            "max_budget",
            "quota",
            "insufficient_quota",
            "billing",
            "rate limit",
            "rate_limit",
            "rate-limit",
        )
    )


def _structured_llm_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for key in ("code", "type", "message", "detail", "error", "param"):
                if key in value:
                    collect(value[key])
        elif isinstance(value, list):
            for item in value:
                collect(item)

    if isinstance(data, dict) and "error" in data:
        collect(data["error"])
    else:
        collect(data)
    return " ".join(parts).casefold()
