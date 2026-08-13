"""Which surface an error appears on, how loudly, and what it offers to do.

Every error this client can raise has to land somewhere specific. Deciding
that at each call site is how a code ends up handled in one view, swallowed
in another, and forgotten entirely in the third that was written last. The
three tables below make the decision once, for all fifteen codes, in a form a
test can check for completeness - tests/test_error_dispatch.py asserts that
their key sets are exactly the key set of ``errors.MESSAGE_BY_CODE``, so a
code added to the taxonomy without a home here fails the suite instead of
reaching a user as silence.

The tables hold classifications, not text. Message wording stays in
strings.py, reached through ``errors.message_for``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import errors, strings

# -- The vocabularies the tables are drawn from ----------------------------

SEVERITY_DANGER = "danger"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"
SEVERITY_MUTED = "muted"

# The region banner: this request failed, here is what to do about it.
SURFACE_BANNER = "banner"
# The input that caused it: outline turns red, one line underneath says why.
SURFACE_FIELD = "field"
# The window-wide banner: true of every area, not of one request.
SURFACE_GLOBAL = "global"
# The quiet line that the next action overwrites. Confirmations only.
SURFACE_STATUS = "status"

ACTION_RETRY = "retry"
ACTION_OPEN_SETTINGS = "open_settings"
ACTION_ENTER_TOKEN = "enter_token"

VALID_SEVERITIES = frozenset(
    {SEVERITY_DANGER, SEVERITY_WARN, SEVERITY_INFO, SEVERITY_MUTED}
)
VALID_SURFACES = frozenset(
    {SURFACE_BANNER, SURFACE_FIELD, SURFACE_GLOBAL, SURFACE_STATUS}
)
VALID_ACTIONS = frozenset({ACTION_RETRY, ACTION_OPEN_SETTINGS, ACTION_ENTER_TOKEN})

# -- The tables ------------------------------------------------------------

SEVERITY_BY_CODE: dict[str, str] = {
    errors.ERROR_OFFLINE: SEVERITY_DANGER,
    errors.ERROR_TIMEOUT: SEVERITY_DANGER,
    errors.ERROR_INTERNAL: SEVERITY_DANGER,
    errors.ERROR_UNKNOWN: SEVERITY_DANGER,
    errors.ERROR_LLM_UNAVAILABLE: SEVERITY_DANGER,
    errors.ERROR_LLM_BUDGET_EXHAUSTED: SEVERITY_DANGER,
    # Not a failure of the client or the service - a pace the caller has to
    # keep to. Red would say something broke.
    errors.ERROR_RATE_LIMITED: SEVERITY_WARN,
    errors.ERROR_UNAUTHORIZED: SEVERITY_WARN,
    errors.ERROR_FORBIDDEN: SEVERITY_WARN,
    errors.ERROR_NOT_CONFIGURED: SEVERITY_WARN,
    errors.ERROR_INSECURE_ENDPOINT: SEVERITY_WARN,
    errors.ERROR_INVALID_REQUEST: SEVERITY_DANGER,
    errors.ERROR_INPUT_TOO_LONG: SEVERITY_DANGER,
    errors.ERROR_PAYLOAD_TOO_LARGE: SEVERITY_DANGER,
    # The owner asked for this one. Confirmations are never red.
    errors.ERROR_CANCELLED: SEVERITY_MUTED,
}

SURFACE_BY_CODE: dict[str, str] = {
    errors.ERROR_OFFLINE: SURFACE_BANNER,
    errors.ERROR_TIMEOUT: SURFACE_BANNER,
    errors.ERROR_INTERNAL: SURFACE_BANNER,
    errors.ERROR_UNKNOWN: SURFACE_BANNER,
    errors.ERROR_LLM_UNAVAILABLE: SURFACE_BANNER,
    errors.ERROR_LLM_BUDGET_EXHAUSTED: SURFACE_BANNER,
    errors.ERROR_RATE_LIMITED: SURFACE_BANNER,
    errors.ERROR_UNAUTHORIZED: SURFACE_BANNER,
    errors.ERROR_FORBIDDEN: SURFACE_BANNER,
    # These two are true of the whole window and outlive any one request:
    # nothing will be sent from any area until they are fixed.
    errors.ERROR_NOT_CONFIGURED: SURFACE_GLOBAL,
    errors.ERROR_INSECURE_ENDPOINT: SURFACE_GLOBAL,
    # The fault is in the text the owner has in front of them, so the
    # message belongs against that text and not in a box above it.
    errors.ERROR_INVALID_REQUEST: SURFACE_FIELD,
    errors.ERROR_INPUT_TOO_LONG: SURFACE_FIELD,
    errors.ERROR_PAYLOAD_TOO_LARGE: SURFACE_FIELD,
    errors.ERROR_CANCELLED: SURFACE_STATUS,
}

ACTION_BY_CODE: dict[str, str | None] = {
    errors.ERROR_OFFLINE: ACTION_RETRY,
    errors.ERROR_TIMEOUT: ACTION_RETRY,
    errors.ERROR_INTERNAL: ACTION_RETRY,
    errors.ERROR_UNKNOWN: ACTION_RETRY,
    errors.ERROR_LLM_UNAVAILABLE: ACTION_RETRY,
    errors.ERROR_LLM_BUDGET_EXHAUSTED: ACTION_RETRY,
    errors.ERROR_RATE_LIMITED: ACTION_RETRY,
    # Straight to the token field: "open settings" would be one more hop to
    # the same place, and the credential is the only thing to change here.
    errors.ERROR_UNAUTHORIZED: ACTION_ENTER_TOKEN,
    errors.ERROR_FORBIDDEN: ACTION_ENTER_TOKEN,
    errors.ERROR_NOT_CONFIGURED: ACTION_OPEN_SETTINGS,
    errors.ERROR_INSECURE_ENDPOINT: ACTION_OPEN_SETTINGS,
    # Nothing to offer: the fix is to edit the text, and focus goes there.
    errors.ERROR_INVALID_REQUEST: None,
    errors.ERROR_INPUT_TOO_LONG: None,
    errors.ERROR_PAYLOAD_TOO_LARGE: None,
    errors.ERROR_CANCELLED: None,
}

# The button wording for each action, so two views cannot label the same
# action differently. Keyed by action, not by code.
ACTION_LABEL_BY_ACTION: dict[str, str] = {
    ACTION_RETRY: strings.ACTION_RETRY,
    ACTION_OPEN_SETTINGS: strings.ACTION_OPEN_SETTINGS,
    ACTION_ENTER_TOKEN: strings.ACTION_ENTER_TOKEN,
}


@dataclass(frozen=True)
class ErrorPresentation:
    """One row of the three tables, resolved together."""

    code: str
    severity: str
    surface: str
    action: str | None

    @property
    def message(self) -> str:
        """The user-facing text for this code, from strings.py."""
        return errors.message_for(self.code)

    @property
    def action_label(self) -> str | None:
        """The button wording for this row's action, if it has one."""
        if self.action is None:
            return None
        return ACTION_LABEL_BY_ACTION[self.action]


def presentation_for(code: str) -> ErrorPresentation:
    """How to show `code`.

    A code with no row falls back to the row for `unknown` - the same
    fallback `errors.message_for` already makes for the text. A code this
    release has never heard of is still a failure the owner has to see, and
    dropping it because the table has no entry would make the newest error
    the only invisible one.
    """
    known = code in SEVERITY_BY_CODE
    resolved = code if known else errors.ERROR_UNKNOWN
    return ErrorPresentation(
        code=code,
        severity=SEVERITY_BY_CODE[resolved],
        surface=SURFACE_BY_CODE[resolved],
        action=ACTION_BY_CODE[resolved],
    )
