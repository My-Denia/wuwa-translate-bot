"""Term locking for sentence translation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

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
            result = result.replace(placeholder, en if to_en else zh)
        return result


class SentenceTranslator:
    def __init__(self, db_path: str | Path):
        self.service = TermService(db_path)

    def prepare_text(self, text: str) -> str:
        text = normalize_user_text(text)
        if not text:
            return ""
        return "\n".join(self._resolve_speaker_prefix(line) for line in text.splitlines())

    def lock_terms(self, text: str) -> LockedSentence:
        return self._lock_terms(self.prepare_text(text))

    def _lock_terms(self, text: str) -> LockedSentence:
        entries = sorted(self._lockable_sources(), key=lambda item: len(item[0]), reverse=True)
        locked = text
        locks: list[tuple[str, str, str]] = []
        used: set[str] = set()
        for source, (zh, en) in entries:
            if len(source) < 2 or source in used or source not in locked:
                continue
            if not zh or not en or "\n" in zh or "\n" in en:
                continue
            placeholder = f"__TERM_{len(locks)}__"
            locked = locked.replace(source, placeholder)
            locks.append((placeholder, zh, en))
            used.add(source)
        return LockedSentence(locked_text=locked, locks=tuple(locks))

    def translate(self, text: str, *, to_chinese: bool = False) -> str:
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
                translated = _call_llm(locked.locked_text, locked.locks, to_chinese=True)
            else:
                translated = _call_llm(locked.locked_text, locked.locks)
        except LLMTranslationError as exc:
            return exc.user_message
        return locked.restore(translated, to_en=not to_chinese)

    def translate_html(self, html_text: str, *, to_chinese: bool = False) -> str:
        """Translate Telegram-HTML text with DB terms locked and tags untouched.

        Skips ``prepare_text``/``normalize_user_text`` on purpose: normalization
        would mangle tags and strip ``>`` quote bars. ``LLMTranslationError``
        propagates to the caller so the passive channel path can stay silent.
        """
        locked = self._lock_terms(html_text)
        if to_chinese:
            translated = _call_llm(
                locked.locked_text, locked.locks, html_mode=True, to_chinese=True
            )
        else:
            translated = _call_llm(locked.locked_text, locked.locks, html_mode=True)
        return locked.restore(translated, to_en=not to_chinese)

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

    def _lockable_sources(self) -> set[tuple[str, tuple[str, str]]]:
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
        return set(sources.items())


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


def _call_llm(
    locked_text: str,
    locks: tuple[tuple[str, str, str], ...],
    html_mode: bool = False,
    to_chinese: bool = False,
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
            "Keep placeholders like __TERM_0__ exactly unchanged. "
            "Do not paraphrase locked official terms. Return Chinese only.\n"
        )
        if html_mode:
            system_content += _HTML_MODE_INSTRUCTION_ZH
    else:
        system_content = (
            "Translate Chinese Wuthering Waves text into English. "
            "Keep placeholders like __TERM_0__ exactly unchanged. "
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
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _llm_error_from_response(exc.response) from exc
        data = response.json()
    return str(data["choices"][0]["message"]["content"]).strip()


def _llm_error_from_response(response: httpx.Response) -> LLMTranslationError:
    body = response.text.casefold()
    if response.status_code == 429 or any(
        marker in body for marker in ("budget", "max_budget", "quota", "exceeded")
    ):
        return LLMTranslationError(BUDGET_EXHAUSTED_NOTICE)
    return LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE)
