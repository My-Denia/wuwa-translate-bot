"""Client-side error-code -> user-facing message mapping.

Message text lives in strings.py; this module only maps codes to it.

The first nine codes below mirror the server's stable error taxonomy
(docs/api/openapi.json, ErrorDetailBody.code) so the client can branch on the
same enumerated values the API returns; matching an external wire contract's
constant names is not translation logic, it is just naming this client's
side of the same envelope. The last five codes are produced entirely locally
by this client (transport failures, user-initiated cancellation, and a
refusal to send the credential through an address or a transport this client
will not use) and are never sent by the server.
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
# Raised before any request is sent, in three situations, of which only the
# first is worded into the message below and only the first is reachable by an
# owner:
#   1. the configured address would carry the device token to another machine
#      without transport protection;
#   2. the address is protected in transit but is not a usable base address at
#      all - embedded credentials, a query, a fragment, an unparseable port.
#      The settings dialog refuses these first with its own precise message,
#      and ClientConfig.load falls back to the default, so this arm exists to
#      keep the transport from being the most permissive layer, not to be
#      seen;
#   3. a caller-injected transport is not one this client can reason about, or
#      does not verify server certificates. Unreachable from the UI, from
#      configuration and from the packaged application.
ERROR_INSECURE_ENDPOINT = "insecure_endpoint"
# No server address is configured at all: the configuration file is missing,
# unreadable, malformed, or the address in it is not one this client will use.
# Distinct from the code above on purpose - "the address you set is unsafe"
# and "you have not set an address" send the owner to different places - and
# distinct from `offline`, which is what the client used to report when it
# silently substituted a development address for a missing setting.
ERROR_NOT_CONFIGURED = "not_configured"

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
    ERROR_INSECURE_ENDPOINT: strings.ERROR_MSG_INSECURE_ENDPOINT,
    ERROR_NOT_CONFIGURED: strings.ERROR_MSG_NOT_CONFIGURED,
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


def error_status(exc: "ClientError") -> str:
    """One line for a status bar: the mapped message, plus the server's own
    request id when there is one.

    The id is the only handle the owner has when asking what happened on the
    other side; dropping it turns a traceable failure into an anecdote.
    """
    message = message_for(exc.code)
    if exc.request_id:
        return f"{message} | {strings.REQUEST_ID_LABEL.format(request_id=exc.request_id)}"
    return message
