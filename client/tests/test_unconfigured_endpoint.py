"""No server address means no server address - not a development one.

`ClientConfig.load` used to substitute `http://127.0.0.1:8788` for anything
it could not read or accept: a missing file, a malformed one, an address it
refuses. The owner's config file really did go missing across a reboot, and
the client then spent the session reporting that it could not reach a server
nobody had configured it to talk to.

The contract now is: an address that cannot be used produces NO address, the
client says so, and every request path refuses with one stable code whose
message names where to fix it.

No Qt, no network. The window's half of this is in
test_ui_endpoint_state.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wuwaterm_client import strings
from wuwaterm_client.api import ApiClient
from wuwaterm_client.config import ClientConfig, config_path
from wuwaterm_client.errors import (
    ERROR_INSECURE_ENDPOINT,
    ERROR_NOT_CONFIGURED,
    ClientError,
    message_for,
)

# The address that used to stand in for "unset". Named here so that a
# reintroduced fallback fails these tests by name rather than by symptom.
FORMER_FALLBACK = "http://127.0.0.1:8788"

UNREADABLE_CONFIGURATIONS = {
    "malformed": "not json at all {{{",
    "not an object": json.dumps(["http://127.0.0.1:8788"]),
    "no address at all": json.dumps({"request_timeout_seconds": 5.0}),
    "address of the wrong type": json.dumps({"base_url": 8788}),
    "address with an unparseable port": json.dumps(
        {"base_url": "http://127.0.0.1:notaport"}
    ),
    "address this client would not send a token to": json.dumps(
        {"base_url": "http://198.51.100.7:8787"}
    ),
    "address with embedded credentials": json.dumps(
        {"base_url": "https://device:secret@api.example.invalid"}
    ),
    "empty address": json.dumps({"base_url": "   "}),
}


def test_a_missing_configuration_file_leaves_the_client_unconfigured(tmp_path) -> None:
    """The exact case that happened: the file was gone after a restart."""
    assert not config_path(tmp_path).exists()

    config = ClientConfig.load(tmp_path)

    assert config.base_url is None
    assert config.is_configured is False
    assert config.base_url != FORMER_FALLBACK


@pytest.mark.parametrize("description", sorted(UNREADABLE_CONFIGURATIONS))
def test_an_unusable_stored_address_leaves_the_client_unconfigured(
    tmp_path, description: str
) -> None:
    """Every way the file can fail to name a usable address ends in the same
    explicit state, and in none of them does an address appear that the owner
    did not choose."""
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNREADABLE_CONFIGURATIONS[description], encoding="utf-8")

    config = ClientConfig.load(tmp_path)

    assert config.base_url is None, description
    assert config.is_configured is False, description


def test_the_timeouts_still_have_defaults(tmp_path) -> None:
    """Only the ADDRESS lost its fallback. A timeout the client picks for
    itself is a preference; a server it picks for itself is a wrong answer."""
    config = ClientConfig.load(tmp_path)

    assert config.request_timeout_seconds > 0
    assert config.translate_timeout_seconds > 0


@pytest.mark.parametrize("call", ["translate", "lookup_terms", "get_meta"])
def test_every_request_path_refuses_while_unconfigured(call: str) -> None:
    """Not one entry point, all of them - and before anything is sent.

    The credential provider is never consulted either: the refusal happens
    above the header, so an unconfigured client does not reach into the OS
    credential store to fail.
    """

    def refuse_to_be_asked() -> str:
        raise AssertionError("the credential must not be read for a refused request")

    client = ApiClient.from_config(ClientConfig())
    client._token_provider = refuse_to_be_asked
    calls = {
        "translate": lambda: client.translate("今汐"),
        "lookup_terms": lambda: client.lookup_terms("今汐"),
        "get_meta": client.get_meta,
    }
    try:
        assert client.is_configured is False
        with pytest.raises(ClientError) as raised:
            asyncio.run(calls[call]())
        assert raised.value.code == ERROR_NOT_CONFIGURED
        assert raised.value.message == strings.ERROR_MSG_NOT_CONFIGURED
    finally:
        asyncio.run(client.aclose())


def test_the_refusal_tells_the_owner_where_to_fix_it() -> None:
    """A stable code is for the program; the message is for the person, and
    "an unexpected error occurred" would send them nowhere."""
    message = message_for(ERROR_NOT_CONFIGURED)

    assert message == strings.ERROR_MSG_NOT_CONFIGURED
    assert "Settings" in message
    assert message != strings.ERROR_MSG_UNKNOWN
    # Distinct from the refusal for an address that IS set but is unsafe:
    # the two send the owner to different actions.
    assert message != strings.ERROR_MSG_INSECURE_ENDPOINT


def test_an_unconfigured_client_becomes_configured_through_settings() -> None:
    """The recovery path. Setting an address in Settings pushes it into the
    live client, and the same object must start working without a restart."""
    client = ApiClient.from_config(ClientConfig())
    try:
        client.update_base_url("https://api.example.invalid")
        assert client.is_configured is True
        assert client._client.base_url.host == "api.example.invalid"
    finally:
        asyncio.run(client.aclose())


def test_the_transport_policy_is_not_relaxed_by_the_unconfigured_state() -> None:
    """`None` is the ONLY thing that means "unconfigured".

    Making the constructor accept a missing address is exactly the kind of
    change that turns into "and an empty string, and whitespace, and
    anything else that looks unset" - at which point an address the policy
    refuses would arrive as a harmless-looking unconfigured client instead of
    a refusal. Everything that is not `None` still goes through the same
    check it always did.
    """
    for refused in (
        "",
        "   ",
        "http://127.0.0.1:notaport",
        "http://198.51.100.7:8787",
        "ftp://example.invalid",
    ):
        with pytest.raises(ClientError) as raised:
            ApiClient(refused)
        assert raised.value.code == ERROR_INSECURE_ENDPOINT, refused

    # ...and the same for the live-update path.
    client = ApiClient("https://api.example.invalid", token_provider=lambda: None)
    try:
        with pytest.raises(ClientError) as raised:
            client.update_base_url("http://198.51.100.7:8787")
        assert raised.value.code == ERROR_INSECURE_ENDPOINT
        with pytest.raises(ClientError) as raised:
            client.update_base_url(None)
        assert raised.value.code == ERROR_NOT_CONFIGURED
        # Neither refusal moved the client off the address it was using.
        assert client._client.base_url.host == "api.example.invalid"
        assert client.is_configured is True
    finally:
        asyncio.run(client.aclose())


def test_an_unconfigured_client_is_refused_twice_over() -> None:
    """The claim in `ApiClient`'s docstring, checked rather than asserted.

    `_request` refuses first, on the flag. Underneath it the client has no
    origin at all, so `_guard_request_target` - the guard that exists to stop
    a request going anywhere but the configured origin - refuses as well.
    Removing the first check would produce a refusal, not a request.
    """
    client = ApiClient(None, token_provider=lambda: None)
    try:
        assert str(client._client.base_url) == ""
        client._configured = True  # exactly the regression being guarded
        with pytest.raises(ClientError) as raised:
            client._guard_request_target("/v1/meta")
        assert raised.value.code == ERROR_INSECURE_ENDPOINT
    finally:
        asyncio.run(client.aclose())


def test_a_saved_address_survives_a_reload_and_ends_the_unconfigured_state(
    tmp_path,
) -> None:
    """The end-to-end shape of the fix: unconfigured, then set, then loaded
    back as configured."""
    assert ClientConfig.load(tmp_path).is_configured is False

    ClientConfig(base_url="https://api.example.invalid").save(base_dir=tmp_path)
    reloaded = ClientConfig.load(tmp_path)

    assert reloaded.is_configured is True
    assert reloaded.base_url == "https://api.example.invalid"
