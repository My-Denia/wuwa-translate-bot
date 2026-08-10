"""Transport-hardening gates for the desktop client.

Two properties are pinned here, both of them about what the client will do
with the device credential before any request is sent:

(b) an address that would carry the token to another machine in the clear is
    refused by the transport itself, with a stable error code; and
(c) certificate verification is on for every HTTPS request, with no way to
    turn it off.

No Qt, no network, no running server.
"""

from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from wuwaterm_client import strings
from wuwaterm_client.api import ApiClient
from wuwaterm_client.config import ClientConfig, endpoint_is_confidential, usable_base_url
from wuwaterm_client.errors import ERROR_INSECURE_ENDPOINT, ClientError

LOOPBACK = "http://127.0.0.1:8787"

REFUSED_ADDRESSES = [
    "http://198.51.100.7:8787",
    "http://api.example.com",
    "http://api.example.com:8787/wuwaterm-api",
    "http://[2001:db8::1]:8787",
    "http://127.0.0.1.example.com:8787",  # loopback-looking, not loopback
    "ftp://example.com",
    "//example.com/wuwaterm-api",  # no scheme at all
    "example.com:8787",
]

ACCEPTED_ADDRESSES = [
    "https://api.example.com",
    "https://api.example.com:8443/wuwaterm-api",
    LOOPBACK,
    "http://localhost:8787",
    "http://[::1]:8787",
]


def _close(client: ApiClient) -> None:
    asyncio.run(client.aclose())


@pytest.mark.parametrize("address", REFUSED_ADDRESSES)
def test_the_transport_refuses_an_address_it_cannot_protect(address: str) -> None:
    """Refused where the transport is built, not only where it is typed.

    The settings dialog validates too, but this constructor is reachable
    without it - a hand-edited configuration file, a future caller - and a
    check that lives only in the dialog is not a transport guarantee.
    """
    with pytest.raises(ClientError) as raised:
        ApiClient(address)

    assert raised.value.code == ERROR_INSECURE_ENDPOINT
    assert raised.value.message == strings.ERROR_MSG_INSECURE_ENDPOINT
    # The same address is refused by the setting validator and by the policy
    # predicate, so the three cannot drift apart silently.
    assert usable_base_url(address) is False
    assert endpoint_is_confidential(address) is False


@pytest.mark.parametrize("address", ACCEPTED_ADDRESSES)
def test_https_anywhere_and_plain_http_to_this_machine_are_accepted(address: str) -> None:
    """The rule must not be so strict that it refuses the supported forms:
    a gate that rejects everything proves nothing about the one case."""
    assert endpoint_is_confidential(address) is True
    client = ApiClient(address, token_provider=lambda: None)
    try:
        expected = httpx.URL(address)
        assert client._client.base_url.scheme == expected.scheme
        assert client._client.base_url.host == expected.host
    finally:
        _close(client)


def test_a_refused_address_leaves_the_running_client_where_it_was() -> None:
    """A rejected settings change must not half-apply: the previous address
    stays in effect, so the owner keeps a working client."""
    client = ApiClient(LOOPBACK, token_provider=lambda: None)
    try:
        with pytest.raises(ClientError) as raised:
            client.update_base_url("http://198.51.100.7:8787")
        assert raised.value.code == ERROR_INSECURE_ENDPOINT
        assert str(client._client.base_url).startswith(LOOPBACK)

        client.update_base_url("https://api.example.com")
        assert str(client._client.base_url).startswith("https://api.example.com")
    finally:
        _close(client)


def test_a_stored_configuration_can_never_carry_a_refused_address(tmp_path) -> None:
    """End to end for the path a real launch takes: whatever is on disk,
    ClientConfig.load falls back rather than handing the transport an
    address it will refuse."""
    (tmp_path / "config.json").write_text(
        '{"base_url": "http://198.51.100.7:8787"}', encoding="utf-8"
    )
    config = ClientConfig.load(tmp_path)

    assert config.base_url == ClientConfig().base_url
    client = ApiClient.from_config(config)
    _close(client)


def test_certificate_verification_is_on_for_the_client_it_actually_builds() -> None:
    """Not "the default is safe" - the SSL context this client hands to the
    connection pool is inspected, and must require a certificate and check
    the host name."""
    client = ApiClient("https://api.example.com", token_provider=lambda: None)
    try:
        transport = client._client._transport
        pool = getattr(transport, "_pool", None)
        context = getattr(pool, "_ssl_context", None)
        assert isinstance(context, ssl.SSLContext), (
            "could not reach the transport's SSL context; this assertion must "
            "be repaired rather than deleted, or the gate proves nothing"
        )
        assert context.verify_mode is ssl.CERT_REQUIRED
        assert context.check_hostname is True
    finally:
        _close(client)


def test_the_client_asks_for_verification_explicitly() -> None:
    """The httpx default is verification, and this pins that the client does
    not rely on that default staying what it is today."""
    recorded: dict[str, object] = {}
    original = httpx.AsyncClient

    class _Recording(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            recorded.update(kwargs)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _Recording  # type: ignore[misc]
    try:
        client = ApiClient("https://api.example.com", token_provider=lambda: None)
    finally:
        httpx.AsyncClient = original  # type: ignore[misc]
    _close(client)

    assert recorded["verify"] is True
    assert recorded["trust_env"] is False


def test_the_client_exposes_no_way_to_turn_verification_off() -> None:
    """A constructor keyword is how an insecure toggle usually arrives."""
    import inspect

    parameters = inspect.signature(ApiClient.__init__).parameters
    for suspicious in ("verify", "insecure", "allow_insecure", "ssl_verify", "no_verify"):
        assert suspicious not in parameters, suspicious
