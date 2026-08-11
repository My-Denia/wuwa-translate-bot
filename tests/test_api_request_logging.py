"""Server-side request records for the HTTP adapter.

Two separate properties are under test here and they fail in different ways:

* the service EMITS a record at all. Until this module existed the adapter
  called ``LOGGER.info`` in a dozen places and configured no handler anywhere,
  so a deployed process produced no per-request evidence — a request id handed
  to a client could not be matched to anything on the server.
* what the record CONTAINS. A log line is an output channel like any other, so
  the same rules that govern the response body govern it: no credential, no raw
  device id, no request text, and nothing an unauthenticated caller can put on
  the wire may reach an operator's terminal unescaped.

The privacy half is asserted against captured records rather than by reading
the source, because the interesting failures (a field added later, a framework
that starts logging on its own) are invisible to inspection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import Path

import httpx
import pytest

from wuwaterm_api.app import create_app
from wuwaterm_api.auth import TOKEN_SCHEME, DeviceStore
from wuwaterm_api.settings import ApiConfigError, ApiSettings, validate_log_level

ROOT = Path(__file__).resolve().parents[1]

LOGGER_NAME = "wuwaterm_api"
COMPLETION_PREFIX = "request complete "

# A secret the operator supplies; the service never mints one.
TEST_SECRET = "unguessable-material-for-tests-0123456789abcdef"


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


def build_app(tmp_path: Path, db_path: Path, **overrides):
    defaults = dict(
        db_path=db_path,
        device_db_path=tmp_path / "api-state" / "devices.db",
        rate_limit_per_minute=100,
        llm_calls_per_minute=100,
        max_body_bytes=2048,
        request_timeout_seconds=30.0,
    )
    defaults.update(overrides)
    settings = ApiSettings(**defaults)
    store = DeviceStore(settings.device_db_path)
    store.initialize()
    return create_app(settings, device_store=store), store


def issue_device(store, name: str = "owner desktop", scopes=None):
    device = store.issue(name, scopes, secret=TEST_SECRET)
    return device, f"{TOKEN_SCHEME}.{device.device_id}.{TEST_SECRET}"


def run(coro):
    return asyncio.run(coro)


async def call(app, method: str, url: str, *, raise_app_exceptions=True, **kwargs):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://api.test"
    ) as client:
        return await client.request(method, url, **kwargs)


class RecordingHandler(logging.Handler):
    """Captures what a handler would actually emit, formatting included."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]

    @property
    def completions(self) -> list[str]:
        return [
            message
            for message in self.messages
            if message.startswith(COMPLETION_PREFIX)
        ]


@contextlib.contextmanager
def captured_records():
    """Attach a handler to the adapter's logger for the duration of a block.

    Deliberately not ``caplog``: the point is what an installed handler sees,
    and pytest's own capture level is global state other tests can move.
    """
    logger = logging.getLogger(LOGGER_NAME)
    handler = RecordingHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


_FIELD_PATTERNS = {
    "request_id": r"request_id=(\S+)",
    "method": r"method=(\S+)",
    "route": r"route=(.*?) status=",
    "status": r"status=(\d+)",
    "duration_ms": r"duration_ms=([0-9.]+)",
    "device": r"device=(\S+)",
}


def fields(message: str) -> dict[str, str]:
    assert message.startswith(COMPLETION_PREFIX), message
    parsed = {}
    for name, pattern in _FIELD_PATTERNS.items():
        found = re.search(pattern, message)
        assert found is not None, f"{name} missing from {message!r}"
        parsed[name] = found.group(1)
    return parsed


# --------------------------------------------------------------------------
# One record per request, on every outcome
# --------------------------------------------------------------------------


def test_an_authenticated_success_produces_one_completion_record(tmp_path, sample_db):
    app, store = build_app(tmp_path, sample_db)
    device, token = issue_device(store)

    with captured_records() as captured:
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers={"Authorization": f"Bearer {token}"},
            )
        )

    assert response.status_code == 200, response.text
    assert len(captured.completions) == 1, captured.messages
    record = fields(captured.completions[0])
    assert record["request_id"] == response.headers["X-Request-Id"]
    assert record["request_id"] == response.json()["request_id"]
    assert record["method"] == "POST"
    assert record["route"] == "/v1/translations"
    assert record["status"] == "200"
    assert float(record["duration_ms"]) >= 0.0
    # The principal is present and is the redaction helper's output, never the
    # device id it was derived from.
    assert record["device"].startswith("id:")
    assert device.device_id not in captured.completions[0]


def test_an_unauthenticated_request_produces_one_completion_record(tmp_path, sample_db):
    app, _ = build_app(tmp_path, sample_db)

    with captured_records() as captured:
        response = run(call(app, "POST", "/v1/translations", json={"text": "声骸"}))

    assert response.status_code == 401
    assert len(captured.completions) == 1, captured.messages
    record = fields(captured.completions[0])
    assert record["request_id"] == response.headers["X-Request-Id"]
    assert record["request_id"] == response.json()["request_id"]
    assert record["status"] == "401"
    assert record["route"] == "/v1/translations"
    # No device was authenticated, so there is no principal to name.
    assert record["device"] == "-"


def test_an_error_path_produces_one_completion_record(tmp_path, sample_db, monkeypatch):
    """An unhandled failure still leaves exactly one record behind.

    This is the case a naive implementation loses: the application's own
    ``Exception`` handler runs OUTSIDE the request-id middleware, so the
    exception passes straight through it. Without a ``finally`` the request
    that most needs a server-side record is the one that produces none.
    """
    app, store = build_app(tmp_path, sample_db)
    _, token = issue_device(store)

    def boom(_service):
        raise RuntimeError("metadata read failed")

    monkeypatch.setattr("wuwaterm_api.app.service_metadata", boom)

    with captured_records() as captured:
        response = run(
            call(
                app,
                "GET",
                "/v1/meta",
                headers={"Authorization": f"Bearer {token}"},
                raise_app_exceptions=False,
            )
        )

    assert response.status_code == 500
    assert len(captured.completions) == 1, captured.messages
    record = fields(captured.completions[0])
    assert record["status"] == "500"
    assert record["route"] == "/v1/meta"
    # The envelope is the only place the id is published on this path, and it
    # is the same id the server recorded.
    assert record["request_id"] == response.json()["request_id"]
    assert record["device"].startswith("id:")


def test_a_request_refused_before_routing_still_produces_a_record(tmp_path, sample_db):
    """The record comes from the outermost layer, not from the route handler.

    A body over the cap is rejected by middleware that runs before anything is
    matched, so a record attached to the handler would silently miss it — and
    an oversized-body flood is exactly the traffic an operator needs to see.
    """
    app, _ = build_app(tmp_path, sample_db, max_body_bytes=64)

    with captured_records() as captured:
        response = run(
            call(app, "POST", "/v1/translations", json={"text": "x" * 4096})
        )

    assert response.status_code == 413
    assert len(captured.completions) == 1, captured.messages
    record = fields(captured.completions[0])
    assert record["status"] == "413"
    assert record["request_id"] == response.headers["X-Request-Id"]


# --------------------------------------------------------------------------
# What a record may never contain
# --------------------------------------------------------------------------


def test_no_credential_device_id_or_request_text_reaches_any_record(
    tmp_path, sample_db
):
    """The privacy contract, asserted against emitted records.

    The caller here does everything it can to get its own material written
    down: it presents a real credential AND sends a token-shaped
    ``X-Request-Id``, which is the exact shape that used to be echoed into the
    auth-reject line. Nothing it controls may appear; the server-minted id and
    the redacted principal must.
    """
    app, store = build_app(tmp_path, sample_db)
    device, token = issue_device(store)
    body_marker = "MARKER-4f3c1d-request-body-text"

    with captured_records() as captured:
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": body_marker},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-Id": token,
                },
            )
        )

    assert response.status_code in (200, 503), response.text
    assert len(captured.completions) == 1, captured.messages
    emitted = "\n".join(captured.messages)

    assert token not in emitted
    assert TEST_SECRET not in emitted
    assert device.device_id not in emitted
    assert body_marker not in emitted

    minted = response.headers["X-Request-Id"]
    assert minted != token
    assert minted in emitted
    assert f"device=id:" in emitted


def test_an_unmatched_path_is_never_recorded_as_the_caller_wrote_it(
    tmp_path, sample_db
):
    """An unauthenticated caller controls the request target.

    When nothing matched there is no route template to name, so the raw path is
    the only thing left to record — and it can carry terminal escape sequences.
    It is rendered escaped, so what an operator's terminal receives is inert
    text, and it is truncated so a long target cannot push the rest of the
    record off a line.
    """
    app, _ = build_app(tmp_path, sample_db)
    # Percent-encoded on the wire, exactly as a caller would have to send it;
    # the server decodes it back into control characters before anything sees
    # the path, which is what makes the decoded form unsafe to record.
    hostile = "/%1B%5B31mred%1B%5D0;title%07" + "A" * 300

    with captured_records() as captured:
        response = run(call(app, "GET", hostile))

    assert response.status_code == 404
    assert len(captured.completions) == 1, captured.messages
    message = captured.completions[0]
    assert "\x1b" not in message
    assert "\x07" not in message
    assert "\\x1b" in message
    assert len(fields(message)["route"]) <= 100


def test_a_hostile_path_that_matched_a_route_is_recorded_as_the_template(
    tmp_path, sample_db
):
    """A matched request is named by the template, not by what arrived.

    The template is repository text, so it cannot carry anything; recording it
    also means one record shape per endpoint instead of one per caller-chosen
    spelling.
    """
    app, _ = build_app(tmp_path, sample_db)

    with captured_records() as captured:
        response = run(call(app, "GET", "/v1/meta?q=%1b%5b31m"))

    assert response.status_code == 401
    assert fields(captured.completions[0])["route"] == "/v1/meta"


# --------------------------------------------------------------------------
# Where the handler comes from
# --------------------------------------------------------------------------


def test_serving_installs_the_process_log_handler_at_the_configured_level(
    tmp_path, sample_db, monkeypatch
):
    from wuwaterm_api import cli

    configured: list[dict] = []
    served: list[dict] = []
    monkeypatch.setattr(
        logging, "basicConfig", lambda **kwargs: configured.append(kwargs)
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: served.append(kwargs))
    monkeypatch.setenv("WUWATERM_DB_PATH", str(sample_db))
    monkeypatch.setenv("WUWATERM_API_STATE_DIR", str(tmp_path / "state-api"))
    monkeypatch.setenv("WUWATERM_API_LOG_LEVEL", "debug")

    assert cli.main(["serve"]) == 0

    assert len(configured) == 1, configured
    assert configured[0]["level"] == "DEBUG"
    assert "%(levelname)s" in configured[0]["format"]
    assert "%(message)s" in configured[0]["format"]
    assert served, "the server was never started"


def test_the_log_level_defaults_to_info(tmp_path, sample_db, monkeypatch):
    from wuwaterm_api import cli

    configured: list[dict] = []
    monkeypatch.setattr(
        logging, "basicConfig", lambda **kwargs: configured.append(kwargs)
    )
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)
    monkeypatch.setenv("WUWATERM_DB_PATH", str(sample_db))
    monkeypatch.setenv("WUWATERM_API_STATE_DIR", str(tmp_path / "state-api"))
    monkeypatch.delenv("WUWATERM_API_LOG_LEVEL", raising=False)

    assert cli.main(["serve"]) == 0

    assert configured[0]["level"] == "INFO"


def test_operator_subcommands_never_touch_the_process_log_handler(
    tmp_path, sample_db, monkeypatch
):
    """`device list` is run inside an operator's shell session.

    Installing a root handler there would put this service's records into the
    middle of the operator's own command output, and the subcommands print
    what they have to say themselves.
    """
    from wuwaterm_api import cli

    configured: list[dict] = []
    monkeypatch.setattr(
        logging, "basicConfig", lambda **kwargs: configured.append(kwargs)
    )
    monkeypatch.setenv("WUWATERM_DB_PATH", str(sample_db))
    monkeypatch.setenv("WUWATERM_API_STATE_DIR", str(tmp_path / "state-api"))
    monkeypatch.setenv("WUWATERM_API_LOG_LEVEL", "DEBUG")

    assert cli.main(["device", "list"]) == 0

    assert configured == []


def test_importing_or_building_the_application_installs_no_handler(
    tmp_path, sample_db, monkeypatch
):
    """Library and test users get no global logging side effect."""
    configured: list[dict] = []
    monkeypatch.setattr(
        logging, "basicConfig", lambda **kwargs: configured.append(kwargs)
    )

    build_app(tmp_path, sample_db)

    assert configured == []


def test_an_unusable_log_level_stops_serving_but_not_credential_commands(
    tmp_path, sample_db, monkeypatch
):
    """Same rule the bind already follows.

    A serve-time setting is validated on the serve path only: a typo in it must
    never be able to block `device revoke`, which is the one operation that has
    to work on a machine whose environment is wrong.
    """
    from wuwaterm_api import cli

    served: list[dict] = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: served.append(kwargs))
    monkeypatch.setenv("WUWATERM_DB_PATH", str(sample_db))
    monkeypatch.setenv("WUWATERM_API_STATE_DIR", str(tmp_path / "state-api"))
    monkeypatch.setenv("WUWATERM_API_LOG_LEVEL", "chatty")

    assert cli.main(["serve"]) == 2
    assert served == []
    assert cli.main(["device", "list"]) == 0


def test_the_log_level_guard_accepts_only_the_standard_names():
    for accepted in ("critical", "ERROR", " warning ", "info", "Debug"):
        assert validate_log_level(accepted) == accepted.strip().upper()
    for refused in ("", "trace", "verbose", "10", "INFO;DEBUG"):
        with pytest.raises(ApiConfigError):
            validate_log_level(refused)


def test_the_refusal_never_echoes_the_configured_value():
    """Settings never reflect a raw environment value back."""
    with pytest.raises(ApiConfigError) as raised:
        validate_log_level("s3cr3t-looking-value")

    assert "s3cr3t-looking-value" not in str(raised.value)


def test_the_api_example_environment_documents_the_log_level():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "WUWATERM_API_LOG_LEVEL=INFO" in text
