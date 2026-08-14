"""Environment-driven settings for the HTTP adapter.

Every knob is read once at startup and validated with an explicit range, so a
typo fails the process instead of silently disabling a limit. Invalid values
are reported without echoing the raw value (it may contain a secret).
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


class ApiConfigError(ValueError):
    """Raised when the environment cannot produce a usable configuration."""


def validate_loopback_bind(value: str) -> str:
    """Return ``value`` if it is a numeric loopback address, else raise.

    This API is a single-owner surface that must never listen where a remote
    host can reach it. The bind is therefore required to be a numeric loopback
    literal (``127.0.0.1``, any ``127.0.0.0/8`` address, or ``::1``). Refused:

    * ``0.0.0.0`` and ``::`` — every interface, the exact exposure hazard;
    * any routable address — a public or LAN interface;
    * any hostname, including ``localhost`` — it is not numeric and could
      resolve, now or later by DNS the operator does not control, to a
      non-loopback interface. Only an address that is loopback by inspection,
      never by resolution, is accepted.

    The offending value is not echoed: settings never reflect a raw environment
    value back, and the fix is the same regardless of what was set. The returned
    value is the NORMALIZED literal (``str(address)``) so the accepted value is
    the one that actually binds: brackets are URL syntax, not host syntax, and
    ``uvicorn.run(host="[::1]")`` raises at bind time, so ``[::1]`` is returned
    as ``::1`` and surrounding whitespace is dropped.
    """
    candidate = (value or "").strip()
    host = (
        candidate[1:-1]
        if candidate.startswith("[") and candidate.endswith("]")
        else candidate
    )
    if "%" in host:
        # A zone id is accepted by ipaddress (``::1%does-not-exist`` parses AND
        # reports is_loopback), but the scope is not validated here and
        # getaddrinfo fails on a nonexistent one — which would escape as a raw
        # socket error instead of this module's ApiConfigError -> exit 2. The
        # loopback interface needs no scope, so refuse the whole class rather
        # than try to resolve it.
        raise ApiConfigError(
            "the API bind must be a plain numeric loopback address without a "
            "zone id, such as 127.0.0.1 or ::1"
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ApiConfigError(
            "the API bind must be a numeric loopback address such as "
            "127.0.0.1 or ::1"
        ) from None
    if not address.is_loopback:
        raise ApiConfigError(
            "the API bind must be a loopback address such as 127.0.0.1 or ::1; "
            "binding any other interface would expose this single-owner surface"
        )
    return str(address)


LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def validate_log_level(value: str) -> str:
    """Return the normalized level name, else raise ApiConfigError.

    Only the five standard names are accepted. ``uvicorn``'s own ``--log-level``
    additionally understands ``trace``, which is not a level the standard
    library can be configured with, so accepting it here would mean a value
    that starts the server and then fails when the first record is emitted.

    Like every other setting in this module the offending value is not echoed;
    the fix is the same whatever was set, and an environment value can carry
    material that does not belong in a startup error.
    """
    candidate = (value or "").strip().upper()
    if candidate not in LOG_LEVELS:
        raise ApiConfigError(
            "the API log level must be one of " + ", ".join(LOG_LEVELS)
        )
    return candidate


MIN_PORT = 1
MAX_PORT = 65535


def validate_port(value: int) -> int:
    """Return ``value`` if it is a usable TCP port, else raise ApiConfigError.

    ``WUWATERM_API_PORT`` is range-checked by ``_env_int``; the ``--port``
    override used to skip that check entirely and go straight to uvicorn, so
    ``--port 999999`` and ``--port -1`` reached the socket layer and escaped as
    a raw error instead of a config error, and ``--port 0`` was silently
    discarded by an ``or`` that treats it as "unset". The override now goes
    through the same range, so every route into the port setting agrees and
    0 is REFUSED explicitly rather than ignored: an operator who asks for an
    ephemeral port is asking for an address the client cannot be configured
    for, and answering "no" is better than answering 8788.
    """
    if not MIN_PORT <= value <= MAX_PORT:
        raise ApiConfigError(
            f"the API port must be between {MIN_PORT} and {MAX_PORT}"
        )
    return value


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8788
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_DB_PATH = "data/terms.db"
# A sibling of the bot's state directory, never a child of it: the bot
# mounts the whole of state/ read-write.
DEFAULT_STATE_DIR = "state-api"
DEVICE_DB_FILENAME = "devices.db"

# Deliberately smaller than the bot's: the API is a single-owner surface and a
# lower ceiling keeps the documented worst-case LLM concurrency small.
DEFAULT_LLM_MAX_CONCURRENCY = 2
DEFAULT_LLM_CALLS_PER_MINUTE = 30
DEFAULT_LLM_TIMEOUT_SECONDS = 45.0
DEFAULT_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_MAX_BODY_BYTES = 32 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0
# Verifying a presented credential costs a deliberate ~16 MiB scrypt
# derivation, and it happens BEFORE any per-device limit can apply. Bound how
# many of those can run at once so an unauthenticated caller cannot turn the
# credential check itself into the load.
DEFAULT_AUTH_MAX_CONCURRENCY = 2

# The owner-private web presentation layer. Mounted INSIDE this process (see
# docs/adr/0014): a third process would mean a third independent LLM budget,
# because ADR 0009 accounts that budget per process, and the aggregate ceiling
# would rise. The cost of sharing the process is that a defect in the web layer
# can take the API down with it, and the first mitigation is this switch:
# DEFAULT OFF, so the surface does not exist unless an operator asks for it.
DEFAULT_WEB_ENABLED = False
# Mount path. Identical inside the process and on the public site, so the Caddy
# route is a `handle` that strips nothing: with a stripping route the app would
# have to reconstruct the public prefix to emit correct form actions, and the
# one thing a same-origin design must not do is disagree with itself about
# where it lives.
WEB_MOUNT_PATH = "/wuwaterm-web"
DEFAULT_WEB_SESSION_TTL_SECONDS = 12 * 60 * 60
# A ceiling on live browser sessions. This is a single-owner surface, so the
# real number is 1-2; the bound exists so that a loop which somehow reaches
# session creation cannot grow the map without limit.
DEFAULT_WEB_MAX_SESSIONS = 32


_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


def parse_bool(raw: str, default: bool) -> bool:
    """Parse a boolean setting, treating anything unrecognised as the default.

    Deliberately DOES NOT RAISE, and the reason is the same one stated for
    ``bind`` and ``log_level`` below: ``from_env()`` runs for EVERY subcommand,
    including ``device revoke``. An earlier version of this reader raised on an
    unrecognised value, so a typo in a serve-only web setting -
    ``WUWATERM_API_WEB_ENABLED=treu`` - made it impossible to revoke a
    compromised device until the environment was repaired. Gating credential
    revocation on the spelling of a presentation-layer flag is the worst
    available trade, and the precedent against it was already written in this
    file, twenty lines away, when the reader was added.

    The default direction is OFF, so an unreadable value never turns a surface
    on. Being wrong about it is caught loudly on the serve path by
    ``validate_web_enabled``, which is where a serve-time setting belongs.
    """
    value = (raw or "").strip().lower()
    if value in _TRUE_WORDS:
        return True
    if value in _FALSE_WORDS:
        return False
    return default


def validate_web_enabled(raw: str) -> bool:
    """Serve-path validation for the web switch. Raises on a typo.

    The strictness the parser gives up lives here instead, on the one path
    where refusing to start is the right answer and where refusing cannot
    strand an operator who is trying to revoke a credential.
    """
    value = (raw or "").strip()
    if not value:
        return False
    if value.lower() in _TRUE_WORDS:
        return True
    if value.lower() in _FALSE_WORDS:
        return False
    raise ApiConfigError(
        "WUWATERM_API_WEB_ENABLED must be one of 1/0, true/false, yes/no, on/off"
    )


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ApiConfigError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ApiConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ApiConfigError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ApiConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_path(name: str, default: str) -> Path:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return Path(default)
    return Path(raw.strip())


@dataclass(frozen=True)
class ApiSettings:
    db_path: Path
    device_db_path: Path
    # False when an operator named the store explicitly. Only the default
    # layout can have inherited the pre-move path, so only the default layout
    # is checked for one.
    device_db_is_default: bool = True
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_concurrency: int = DEFAULT_LLM_MAX_CONCURRENCY
    llm_calls_per_minute: int = DEFAULT_LLM_CALLS_PER_MINUTE
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    auth_max_concurrency: int = DEFAULT_AUTH_MAX_CONCURRENCY
    log_level: str = DEFAULT_LOG_LEVEL
    web_enabled: bool = DEFAULT_WEB_ENABLED
    # The raw string as the operator wrote it, kept so the serve path can
    # refuse a typo that the lenient parser above deliberately swallowed.
    web_enabled_raw: str = ""
    # The device token the browser session is mapped onto. Held HERE, in the
    # server process, and never sent to the browser: that is what makes the
    # "no credential lands in the browser" property structural rather than a
    # rule the operator has to keep. Carried as a plain str because it is a
    # presented credential, not a stored one - the store keeps only a derived
    # verifier, and this is the thing presented TO it.
    web_device_token: str = ""
    # Shared secret that the edge proxy injects on every proxied request. The
    # app refuses anything arriving without it, which is what makes "rejected
    # before it reaches application logic" true of a request that bypassed the
    # edge entirely by talking straight to the loopback port.
    web_edge_secret: str = ""
    web_session_ttl_seconds: int = DEFAULT_WEB_SESSION_TTL_SECONDS
    web_max_sessions: int = DEFAULT_WEB_MAX_SESSIONS

    @classmethod
    def from_env(cls) -> "ApiSettings":
        state_dir = _env_path("WUWATERM_API_STATE_DIR", DEFAULT_STATE_DIR)
        device_db = os.getenv("WUWATERM_API_DEVICE_DB_PATH")
        return cls(
            db_path=_env_path("WUWATERM_DB_PATH", DEFAULT_DB_PATH),
            device_db_path=(
                Path(device_db.strip())
                if device_db and device_db.strip()
                else state_dir / DEVICE_DB_FILENAME
            ),
            device_db_is_default=not (device_db and device_db.strip()),
            # Deliberately NOT validated here. from_env() is called by EVERY
            # subcommand, including `device revoke` — gating credential
            # revocation on serve-time network configuration would mean a
            # mistyped bind blocks the one operation that must always work. The
            # loopback guard is applied on the serve path, where a socket is
            # actually bound; see validate_loopback_bind and cli._serve.
            bind=(os.getenv("WUWATERM_API_BIND") or DEFAULT_BIND).strip()
            or DEFAULT_BIND,
            port=_env_int(
                "WUWATERM_API_PORT", DEFAULT_PORT, minimum=MIN_PORT, maximum=MAX_PORT
            ),
            llm_timeout_seconds=_env_float(
                "WUWATERM_API_LLM_TIMEOUT_SECONDS",
                DEFAULT_LLM_TIMEOUT_SECONDS,
                minimum=0.1,
                maximum=300.0,
            ),
            llm_max_concurrency=_env_int(
                "WUWATERM_API_LLM_MAX_CONCURRENCY",
                DEFAULT_LLM_MAX_CONCURRENCY,
                minimum=1,
                maximum=64,
            ),
            llm_calls_per_minute=_env_int(
                "WUWATERM_API_LLM_CALLS_PER_MINUTE",
                DEFAULT_LLM_CALLS_PER_MINUTE,
                minimum=1,
                maximum=10000,
            ),
            rate_limit_per_minute=_env_int(
                "WUWATERM_API_RATE_LIMIT_PER_MINUTE",
                DEFAULT_RATE_LIMIT_PER_MINUTE,
                minimum=1,
                maximum=10000,
            ),
            max_body_bytes=_env_int(
                "WUWATERM_API_MAX_BODY_BYTES",
                DEFAULT_MAX_BODY_BYTES,
                minimum=64,
                maximum=1024 * 1024,
            ),
            request_timeout_seconds=_env_float(
                "WUWATERM_API_REQUEST_TIMEOUT_SECONDS",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
                minimum=1.0,
                maximum=600.0,
            ),
            auth_max_concurrency=_env_int(
                "WUWATERM_API_AUTH_MAX_CONCURRENCY",
                DEFAULT_AUTH_MAX_CONCURRENCY,
                minimum=1,
                maximum=64,
            ),
            # Carried raw for the same reason as `bind`, and validated in the
            # same place: from_env() runs for EVERY subcommand, so a typo in a
            # serve-time knob must not be able to block `device revoke`. See
            # validate_log_level and cli._serve.
            log_level=(
                os.getenv("WUWATERM_API_LOG_LEVEL") or DEFAULT_LOG_LEVEL
            ).strip()
            or DEFAULT_LOG_LEVEL,
            # Carried raw AND parsed leniently, for the same reason as `bind`
            # and `log_level`: from_env() runs for every subcommand, so a
            # serve-only typo must not block `device revoke`. The strict
            # check is validate_web_enabled, applied on the serve path.
            web_enabled=parse_bool(
                os.getenv("WUWATERM_API_WEB_ENABLED") or "", DEFAULT_WEB_ENABLED
            ),
            web_enabled_raw=(os.getenv("WUWATERM_API_WEB_ENABLED") or "").strip(),
            # NOT stripped of internal whitespace and NOT validated for shape:
            # the token's format is the credential store's business, and a
            # settings-layer opinion about what a token looks like would be a
            # second, divergent parser. Surrounding whitespace goes because that
            # is an artefact of how environment files are written, not of the
            # credential.
            web_device_token=(os.getenv("WUWATERM_API_WEB_DEVICE_TOKEN") or "").strip(),
            web_edge_secret=(os.getenv("WUWATERM_API_WEB_EDGE_SECRET") or "").strip(),
            web_session_ttl_seconds=_env_int(
                "WUWATERM_API_WEB_SESSION_TTL_SECONDS",
                DEFAULT_WEB_SESSION_TTL_SECONDS,
                minimum=60,
                maximum=30 * 24 * 60 * 60,
            ),
            web_max_sessions=_env_int(
                "WUWATERM_API_WEB_MAX_SESSIONS",
                DEFAULT_WEB_MAX_SESSIONS,
                minimum=1,
                maximum=1024,
            ),
        )
