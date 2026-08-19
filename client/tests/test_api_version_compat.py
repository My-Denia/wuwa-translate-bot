"""The API-version compatibility contract, driven through the real refresh.

What is being pinned
--------------------
Client 0.2.x speaks HTTP API `v1`. The check runs on the `/v1/meta` reply the
status view ALREADY fetches when the owner presses 刷新 - there is no second
request, no startup request and no header - and an `api_version` outside
`SUPPORTED_API_VERSIONS` surfaces a warning that names BOTH versions while the
service facts stay on screen.

Why these tests go through `_on_refresh_clicked` rather than `_show_meta`
------------------------------------------------------------------------
Calling the private renderer directly proves the renderer renders. It does not
prove the refresh path reaches it, and reaching it is half the contract: the
whole design decision is that this check rides on an existing request. So the
accept and the reject cases both start at the button's own handler, inside a
running loop, with an httpx transport double answering `/v1/meta` - the same
shape `test_view_parity_with_main.py` uses. The transport also RECORDS every
request it is asked for, which is what turns "no new request" from a claim
into an assertion.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import wuwaterm_client  # noqa: E402

from wuwaterm_client import strings  # noqa: E402
from wuwaterm_client.api import SUPPORTED_API_VERSIONS, ApiClient  # noqa: E402
from wuwaterm_client.ui.error_presentation import SEVERITY_WARN  # noqa: E402
from wuwaterm_client.ui.status_view import StatusView  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _MetaService:
    """`/v1/meta` answered with a chosen api_version, and every ask recorded.

    ``api_version`` is mutable so one instance can answer a rejecting refresh
    and then an accepting one - which is exactly the sequence the stale-banner
    test needs, and it needs it against ONE client and ONE view.
    """

    def __init__(self, api_version: str = "v1") -> None:
        self.api_version = api_version
        self.asked: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "service_version": "0.4.0",
                "api_version": self.api_version,
                "schema_version": "1",
                "source_profile": "arikatsu",
                "source_commit": "6ce8d5e",
                "term_count": 10951,
                "llm_configured": True,
                "request_id": "req-meta-compat",
            },
        )


def _view(service: _MetaService) -> StatusView:
    return StatusView(
        ApiClient(
            "https://test",
            _test_transport=httpx.MockTransport(service),
            token_provider=lambda: "wtd1.device.secret",
        )
    )


async def _refresh(view: StatusView) -> None:
    """One press of 刷新, awaited to completion."""
    view._on_refresh_clicked()
    task = view._task
    assert task is not None, "the refresh never started"
    await asyncio.gather(task, return_exceptions=True)


# == the constant itself ===================================================


def test_supported_versions_is_exactly_v1() -> None:
    """One accepted version, as a tuple, not a bare string.

    A string would make `in` a SUBSTRING test - `"v1"` is in `"v12"` - so the
    shape is part of the contract, not a formatting preference.
    """
    assert SUPPORTED_API_VERSIONS == ("v1",)


def test_supported_versions_agrees_with_the_committed_server_contract() -> None:
    """The client suite cannot import the server package - different
    interpreter, different environment - so the server's own constant is read
    as TEXT out of the repository instead.

    `src/wuwaterm_api/__init__.py` was chosen over `docs/api/openapi.json`
    after inspecting both. The OpenAPI document declares `api_version` only as
    ``{"title": "Api Version", "type": "string"}`` inside `MetaResponseBody` -
    no example, no `const`, no `enum` - so it does not carry the VALUE this
    client has to agree with, and a test pinned against it would assert
    nothing about `v1` at all. The version claim the document DOES make is in
    its paths, every one of which is `/v1/...`, and that is checked below as
    the second half.

    What turns this red: the server changing `API_VERSION`, or publishing
    paths under another version prefix, without this client's
    `SUPPORTED_API_VERSIONS` following. That is the drift the whole compat
    check exists to make visible, and it must be visible here at build time
    too, not only to an owner who happens to press 刷新.
    """
    source = (REPO_ROOT / "src" / "wuwaterm_api" / "__init__.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^API_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    assert match is not None, "the server package no longer defines API_VERSION"
    assert match.group(1) in SUPPORTED_API_VERSIONS
    assert SUPPORTED_API_VERSIONS == (match.group(1),)

    document = json.loads(
        (REPO_ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    )
    versioned = [path for path in document["paths"] if path.startswith("/v")]
    assert versioned, "the API document publishes no versioned path"
    for path in versioned:
        assert path.startswith(f"/{match.group(1)}/"), path


# == the release version ===================================================


def test_the_package_version_and_the_declared_version_agree() -> None:
    """`client/build.ps1` asks the venv interpreter for
    `wuwaterm_client.__version__` and builds the release asset name
    `WuwaTerm-<version>-windows-x64.zip` out of it, while `pyproject.toml`
    declares the version of the package that gets installed. Nothing makes
    those two strings the same, and if they drift the release ships an asset
    whose name disagrees with the program inside it - a mismatch nobody would
    see until someone tried to match a download against a version on the
    status screen.

    Read as TEXT rather than through importlib.metadata: the metadata reports
    what was INSTALLED, which in an editable checkout can be a stale wheel's
    version, and the question here is about the two files as committed.
    """
    declared = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (REPO_ROOT / "client" / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert declared is not None, "client/pyproject.toml declares no version"
    assert declared.group(1) == wuwaterm_client.__version__


# == accept ================================================================


def test_a_supported_api_version_renders_the_facts_and_warns_about_nothing(
    qapp,
) -> None:
    """`api_version = "v1"`: the service facts appear and no banner does."""
    service = _MetaService("v1")
    view = _view(service)

    asyncio.run(_refresh(view))

    assert service.asked == ["/v1/meta"], "the refresh did not make exactly one call"
    assert view.service_version_value.text() == "0.4.0"
    assert view.term_count_value.text() == "10951"
    assert view.data_profile_value.text() == "arikatsu"
    assert view._banner.is_showing() is False, (
        f"an accepted version raised a banner: {view._banner.message_text!r}"
    )
    assert view._banner.message_text == ""


# == reject ================================================================


def test_an_unsupported_api_version_is_named_on_screen_beside_the_facts(
    qapp,
) -> None:
    """`api_version = "v2"`: the message on the WIDGET has to carry both the
    version the server reported and the version this client speaks.

    Asserted against the rendered text, not against
    `strings.STATUS_API_VERSION_UNSUPPORTED` and not against
    `SUPPORTED_API_VERSIONS`: a template with an unsubstituted placeholder,
    or a message built from the wrong end of the comparison, passes any
    assertion made against the constants and fails this one.
    """
    service = _MetaService("v2")
    view = _view(service)

    asyncio.run(_refresh(view))

    assert service.asked == ["/v1/meta"], "the check spent a request of its own"
    shown = view._banner.message_text
    assert view._banner.is_showing() is True, "an unsupported version said nothing"
    assert "v2" in shown, shown
    assert "v1" in shown, shown
    assert "{" not in shown, f"an unsubstituted placeholder reached the screen: {shown}"
    # A warning, not a failure: the severity is the one `_show_error` gives a
    # recoverable state, and the request id is there to be quoted.
    assert view._banner.property("severity") == SEVERITY_WARN
    assert view._banner.request_id == "req-meta-compat"
    # ...and the facts are STILL on screen. The owner has to be able to read
    # the version that is wrong; a check that hides the data hides its own
    # subject.
    assert view.service_version_value.text() == "0.4.0"
    assert view.term_count_value.text() == "10951"
    assert view.data_profile_value.text() == "arikatsu"
    assert view.status_label.text() == strings.STATUS_BAR_DONE


def test_the_message_is_the_one_from_the_strings_module(qapp) -> None:
    """The wording lives in strings.py, in Chinese, like every other line this
    application can display. Checked by rebuilding the same text from the
    template rather than by matching a phrase, so a re-wording moves one
    value and this test moves with it."""
    service = _MetaService("v2")
    view = _view(service)

    asyncio.run(_refresh(view))

    assert view._banner.message_text == strings.STATUS_API_VERSION_UNSUPPORTED.format(
        reported="v2", supported="v1"
    )


# == the stale warning =====================================================


def test_an_accepted_refresh_takes_down_the_previous_warning(qapp) -> None:
    """Reject, then accept, on the same view: nothing may be left behind.

    This is the failure mode a banner has that an inline label does not - it
    persists until something takes it down - and it would leave the owner
    reading a warning about a server they are no longer being told anything
    bad about.
    """
    service = _MetaService("v2")
    view = _view(service)

    async def scenario() -> None:
        await _refresh(view)
        assert view._banner.is_showing() is True, "fixture failed: no warning to clear"
        service.api_version = "v1"
        await _refresh(view)

    asyncio.run(scenario())

    assert service.asked == ["/v1/meta", "/v1/meta"]
    assert view._banner.is_showing() is False, (
        f"the stale warning survived a good refresh: {view._banner.message_text!r}"
    )
    assert view._banner.message_text == ""
    assert view.service_version_value.text() == "0.4.0"


def test_the_warning_survives_the_refresh_that_raised_it(qapp) -> None:
    """`_run_refresh` clears the banner BEFORE the request and runs a
    `finally` block AFTER the reply is rendered. A message written anywhere
    but between those two would be wiped by its own refresh - so this asserts
    the ordering directly, on a view that has finished its refresh completely
    (progress stopped, button handed back)."""
    service = _MetaService("v2")
    view = _view(service)

    asyncio.run(_refresh(view))

    assert view._progress.is_running() is False, "the refresh has not finished"
    assert view.refresh_button.isEnabled() is True
    assert view._banner.is_showing() is True, "the finally block wiped the warning"


# == the invariant the check may not break =================================


def test_an_unconfigured_client_still_sends_nothing(qapp) -> None:
    """The compat check rides on a request that already happens; it may not
    create one. An unconfigured client is where that is measurable: building
    the view and pressing 刷新 must leave the transport untouched (issues
    #68 / #80)."""
    service = _MetaService("v1")
    view = StatusView(
        ApiClient(
            None,
            _test_transport=httpx.MockTransport(service),
            token_provider=lambda: "wtd1.device.secret",
        )
    )

    async def scenario() -> None:
        view._on_refresh_clicked()
        assert view._task is None, "an unconfigured refresh started a request"
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert service.asked == [], f"a request was sent while unconfigured: {service.asked}"
    assert view._banner.is_showing() is False
