"""Tests for wuwaterm_client.api against a mocked httpx transport.

No network access, no running server, no Qt event loop.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from wuwaterm_client import errors, strings
from wuwaterm_client.api import ApiClient
from wuwaterm_client.errors import ERROR_TIMEOUT, ERROR_UNKNOWN, ClientError


def _client(handler, **kwargs) -> ApiClient:
    transport = httpx.MockTransport(handler)
    token_provider = kwargs.pop("token_provider", lambda: "wtd1.deadbeef.secret")
    return ApiClient(
        "https://test",
        token_provider=token_provider,
        _test_transport=transport,
        **kwargs,
    )


def _translation_payload(**overrides) -> dict:
    payload = {
        "kind": "exact",
        "text": "Rover",
        "direction": "en",
        "dictionary_miss": False,
        "request_id": "req-1",
    }
    payload.update(overrides)
    return payload


def test_bearer_header_sent_exactly() -> None:
    captured: dict[str, str | None] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=_translation_payload())

    client = _client(handler, token_provider=lambda: "wtd1.abc123.supersecret")

    async def scenario() -> None:
        await client.translate("test text")
        await client.aclose()

    asyncio.run(scenario())
    assert captured["authorization"] == "Bearer wtd1.abc123.supersecret"


def test_to_param_sent_for_forced_direction() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_translation_payload())

    client = _client(handler)

    async def scenario() -> None:
        await client.translate("test text", to="en")
        await client.aclose()

    asyncio.run(scenario())
    assert captured["body"] == {"text": "test text", "to": "en"}


def test_to_param_omitted_for_auto_direction() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_translation_payload())

    client = _client(handler)

    async def scenario() -> None:
        await client.translate("test text", to=None)
        await client.aclose()

    asyncio.run(scenario())
    assert "to" not in captured["body"]
    assert captured["body"] == {"text": "test text"}


SERVER_ERROR_CODES = [
    (errors.ERROR_UNAUTHORIZED, 401, strings.ERROR_MSG_UNAUTHORIZED),
    (errors.ERROR_FORBIDDEN, 403, strings.ERROR_MSG_FORBIDDEN),
    (errors.ERROR_RATE_LIMITED, 429, strings.ERROR_MSG_RATE_LIMITED),
    (errors.ERROR_PAYLOAD_TOO_LARGE, 413, strings.ERROR_MSG_PAYLOAD_TOO_LARGE),
    (errors.ERROR_INVALID_REQUEST, 400, strings.ERROR_MSG_INVALID_REQUEST),
    (errors.ERROR_INPUT_TOO_LONG, 422, strings.ERROR_MSG_INPUT_TOO_LONG),
    (errors.ERROR_LLM_UNAVAILABLE, 503, strings.ERROR_MSG_LLM_UNAVAILABLE),
    (errors.ERROR_LLM_BUDGET_EXHAUSTED, 503, strings.ERROR_MSG_LLM_BUDGET_EXHAUSTED),
    (errors.ERROR_INTERNAL, 500, strings.ERROR_MSG_INTERNAL),
]


@pytest.mark.parametrize("code,status,expected_message", SERVER_ERROR_CODES)
def test_each_stable_error_code_renders_mapped_message(
    code: str, status: int, expected_message: str
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "error": {"code": code, "message": "server-side text, not used"},
                "request_id": "req-err",
            },
        )

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.translate("test text")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected ClientError")

    exc = asyncio.run(scenario())
    assert exc.code == code
    assert exc.message == expected_message
    assert exc.request_id == "req-err"
    assert exc.status_code == status


def test_unrecognized_error_code_falls_back_to_unknown_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            418,
            json={
                "error": {"code": "a_future_code_this_client_does_not_know", "message": "x"},
                "request_id": "req-future",
            },
        )

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.translate("test text")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected ClientError")

    exc = asyncio.run(scenario())
    assert exc.code == "a_future_code_this_client_does_not_know"
    assert exc.message == strings.ERROR_MSG_UNKNOWN


def test_cancel_reports_cancellation_not_a_generic_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=_translation_payload())

    client = _client(handler)

    async def scenario() -> ClientError:
        task = asyncio.ensure_future(client.translate("test text"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected ClientError")

    exc = asyncio.run(scenario())
    assert exc.code == errors.ERROR_CANCELLED
    assert exc.message == strings.STATUS_CANCELLED
    assert exc.request_id is None


def test_connect_refused_renders_offline_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.translate("test text")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected ClientError")

    exc = asyncio.run(scenario())
    assert exc.code == errors.ERROR_OFFLINE
    assert exc.message == strings.ERROR_MSG_OFFLINE


def test_timeout_renders_timeout_message() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.translate("test text")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected ClientError")

    exc = asyncio.run(scenario())
    assert exc.code == errors.ERROR_TIMEOUT
    assert exc.message == strings.ERROR_MSG_TIMEOUT


def test_lookup_terms_round_trip() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "test query"
        return httpx.Response(
            200,
            json={
                "query": "test query",
                "matches": [
                    {
                        "zh": "test-zh",
                        "en": "Rover",
                        "category": "character",
                        "score": 1.0,
                        "reason": "exact",
                    }
                ],
                "request_id": "req-terms",
            },
        )

    client = _client(handler)

    async def scenario():
        result = await client.lookup_terms("test query")
        await client.aclose()
        return result

    result = asyncio.run(scenario())
    assert result.query == "test query"
    assert result.matches[0].en == "Rover"
    assert result.request_id == "req-terms"


def test_get_meta_round_trip() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "service_version": "0.2.1",
                "api_version": "v1",
                "schema_version": "1",
                "source_profile": "official",
                "source_commit": "abc123",
                "term_count": 42,
                "llm_configured": True,
                "request_id": "req-meta",
            },
        )

    client = _client(handler)

    async def scenario():
        result = await client.get_meta()
        await client.aclose()
        return result

    result = asyncio.run(scenario())
    assert result.term_count == 42
    assert result.llm_configured is True
    assert result.request_id == "req-meta"


def test_a_request_without_a_stored_credential_sends_no_auth_header() -> None:
    """No credential must mean no header, not an empty or literal one."""
    captured: dict[str, str | None] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "service_version": "0.2.1",
                "api_version": "v1",
                "schema_version": "2",
                "source_profile": "p",
                "source_commit": "c",
                "term_count": 1,
                "llm_configured": False,
                "request_id": "req-meta",
            },
        )

    client = _client(handler, token_provider=lambda: None)

    async def scenario() -> None:
        await client.get_meta()
        await client.aclose()

    asyncio.run(scenario())
    assert captured["authorization"] is None


def test_a_server_side_deadline_is_reported_as_a_timeout() -> None:
    """The service reports its own deadline as 504 with code `internal`.

    The contract says the status is what distinguishes that from a genuine
    failure, so rendering "something went wrong on the server" would
    misdescribe it to the only person who can act on it.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            504,
            json={
                "error": {"code": "internal", "message": "timed out"},
                "request_id": "req-504",
            },
        )

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.get_meta()
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    error = asyncio.run(scenario())
    assert error.code == ERROR_TIMEOUT
    assert error.request_id == "req-504"


def test_a_successful_body_that_is_not_ours_becomes_a_client_error() -> None:
    """A proxy or an unrelated local service can answer 200 with anything."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>hello from something else</html>")

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.get_meta()
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == ERROR_UNKNOWN


def test_a_successful_body_missing_fields_becomes_a_client_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.lookup_terms("x")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == ERROR_UNKNOWN


def test_the_client_does_not_trust_environment_proxies() -> None:
    """Requests go to the address the owner configured, and nowhere else.

    httpx trusts HTTP_PROXY by default, so a machine with a proxy configured
    and no NO_PROXY entry for the configured host would send every request -
    bearer credential included - to that proxy instead of to the service.
    """
    client = ApiClient("http://127.0.0.1:8787")
    try:
        assert client._client.trust_env is False
    finally:
        asyncio.run(client.aclose())


def test_a_credential_that_cannot_be_a_header_is_reported_as_unusable() -> None:
    """A pasted character outside ASCII would raise from inside a coroutine."""

    async def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be attempted")

    client = _client(handler, token_provider=lambda: "wtd1.device.sécret")

    async def scenario() -> ClientError:
        try:
            await client.get_meta()
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == errors.ERROR_UNAUTHORIZED


def test_a_result_field_of_the_wrong_type_becomes_a_client_error() -> None:
    """Every key present, one of them the wrong shape underneath.

    Without a check the value reaches a Qt call and raises there instead of
    arriving as the client's own rendered error state.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_translation_payload(text=[]))

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.translate("x")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == ERROR_UNKNOWN


def test_a_matches_field_that_is_not_a_list_becomes_a_client_error() -> None:
    """A string or an object is iterable too, and would produce nonsense."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"query": "x", "matches": "not-a-list", "request_id": "r"}
        )

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.lookup_terms("x")
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == ERROR_UNKNOWN


def test_metadata_the_contract_requires_must_be_present() -> None:
    """Nullable is not optional: those fields are required by the contract."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "service_version": "0.2.1",
                "api_version": "v1",
                "schema_version": "2",
                # source_profile omitted
                "source_commit": "c",
                "term_count": 1,
                "llm_configured": False,
                "request_id": "req-meta",
            },
        )

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.get_meta()
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == ERROR_UNKNOWN


def test_a_body_that_cannot_be_decoded_becomes_a_client_error() -> None:
    """A malformed compressed body is neither a transport failure nor a
    timeout; it is simply unusable, and must arrive as such."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            content=b"this is not gzip at all",
        )

    client = _client(handler)

    async def scenario() -> ClientError:
        try:
            await client.get_meta()
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == ERROR_UNKNOWN


def test_a_credential_store_failure_becomes_a_client_error() -> None:
    """It must not escape into whichever view happens to be running."""
    from wuwaterm_client.credentials import CredentialStoreUnavailable

    def unavailable():
        raise CredentialStoreUnavailable("vault unavailable")

    async def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be attempted")

    client = _client(handler, token_provider=unavailable)

    async def scenario() -> ClientError:
        try:
            await client.get_meta()
        except ClientError as exc:
            return exc
        finally:
            await client.aclose()
        raise AssertionError("expected a ClientError")

    assert asyncio.run(scenario()).code == errors.ERROR_UNAUTHORIZED
