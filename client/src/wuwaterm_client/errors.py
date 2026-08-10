"""Client-side error-code -> user-facing message mapping.

Message text lives in strings.py; this module only maps codes to it.

The first nine codes below mirror the server's stable error taxonomy
(docs/api/openapi.json, ErrorDetailBody.code) so the client can branch on the
same enumerated values the API returns; matching an external wire contract's
constant names is not translation logic, it is just naming this client's
side of the same envelope. The last four codes are produced entirely locally
by this client (transport failures and user-initiated cancellation) and are
never sent by the server.
"""

from __future__ import annotations

from . import strings

# -- Codes mirrored from the server's stable error envelope ----------------

ERROR_UNAUTHORIZED = "unauthorized"
ERROR_FORBIDDEN = "forbidden"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_PAYLOAD_TOO_LARGE = "payload_too_large"
ERROR_INVALID_REQUEST = "invalid_request"
ERROR_INPUT_TOO_LONG = "input_too_long"
ERROR_LLM_UNAVAILABLE = "llm_unavailable"
ERROR_LLM_BUDGET_EXHAUSTED = "llm_budget_exhausted"
ERROR_INTERNAL = "internal"

# -- Codes produced only by this client -----------------------------------

ERROR_OFFLINE = "offline"
ERROR_TIMEOUT = "timeout"
ERROR_CANCELLED = "cancelled"
ERROR_UNKNOWN = "unknown"

MESSAGE_BY_CODE: dict[str, str] = {
    ERROR_UNAUTHORIZED: strings.ERROR_MSG_UNAUTHORIZED,
    ERROR_FORBIDDEN: strings.ERROR_MSG_FORBIDDEN,
    ERROR_RATE_LIMITED: strings.ERROR_MSG_RATE_LIMITED,
    ERROR_PAYLOAD_TOO_LARGE: strings.ERROR_MSG_PAYLOAD_TOO_LARGE,
    ERROR_INVALID_REQUEST: strings.ERROR_MSG_INVALID_REQUEST,
    ERROR_INPUT_TOO_LONG: strings.ERROR_MSG_INPUT_TOO_LONG,
    ERROR_LLM_UNAVAILABLE: strings.ERROR_MSG_LLM_UNAVAILABLE,
    ERROR_LLM_BUDGET_EXHAUSTED: strings.ERROR_MSG_LLM_BUDGET_EXHAUSTED,
    ERROR_INTERNAL: strings.ERROR_MSG_INTERNAL,
    ERROR_OFFLINE: strings.ERROR_MSG_OFFLINE,
    ERROR_TIMEOUT: strings.ERROR_MSG_TIMEOUT,
    ERROR_CANCELLED: strings.STATUS_CANCELLED,
    ERROR_UNKNOWN: strings.ERROR_MSG_UNKNOWN,
}


def message_for(code: str) -> str:
    return MESSAGE_BY_CODE.get(code, strings.ERROR_MSG_UNKNOWN)


class ClientError(Exception):
    """Raised for any non-2xx API response or transport failure.

    ``code`` is a stable string a caller can branch on; ``message`` is the
    mapped user-facing text from strings.py. ``request_id`` and
    ``status_code`` are populated when the server produced them (never set
    for the client-only transport/cancellation codes).
    """

    def __init__(
        self,
        code: str,
        *,
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.request_id = request_id
        self.status_code = status_code
        self.message = message_for(code)
        super().__init__(f"{code}: {self.message}")
