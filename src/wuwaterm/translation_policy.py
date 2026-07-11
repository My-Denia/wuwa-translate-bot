"""Shared policy constants for manual and linked-channel translation."""

from .sentence import BUDGET_EXHAUSTED_NOTICE, TRANSLATION_UNAVAILABLE_NOTICE


LLM_INPUT_CHAR_LIMIT = 2000
LLM_FAILURE_NOTICES = frozenset(
    (BUDGET_EXHAUSTED_NOTICE, TRANSLATION_UNAVAILABLE_NOTICE)
)
