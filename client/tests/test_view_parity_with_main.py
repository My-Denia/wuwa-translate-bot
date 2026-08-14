"""Behaviours the three views had BEFORE the redesign, still holding after it.

Why this file exists
--------------------
Four rounds of review on the redesign branch produced findings of one shape:
the new code did not do something the old code did. An empty results table on
a failed lookup, a cleared table on an address change - behaviours nobody
listed anywhere, so nothing noticed when a rewrite dropped them. Every gate
was green each time; the reviewer found them by reading the diff.

This file turns that class into a standing check. It is not a snapshot of the
redesign: it is a list of what the PREVIOUS implementation guaranteed, written
so it stays true through whatever the views become next.

How the list was derived
------------------------
By reading, line by line, the three view modules as they exist on
``origin/main`` at ``fe3604b`` - not from review comments, not from memory:

    git show origin/main:client/src/wuwaterm_client/ui/terms_view.py
    git show origin/main:client/src/wuwaterm_client/ui/translate_view.py
    git show origin/main:client/src/wuwaterm_client/ui/status_view.py

Coverage is those three files in full (118, 178 and 124 lines) - every
statement that produces an observable behaviour, as opposed to layout
construction. Each test below names the ``main`` line it came from. Behaviours
that exist only in the redesign are deliberately NOT here; this file would
then stop being a parity check and start being a second copy of the suite.

What a failure here means
-------------------------
Not "the new design is wrong" - that a redesign changes behaviour is expected
and is what review is for. It means a behaviour was dropped WITHOUT anyone
saying so. If one of these is meant to change, delete the test in the same
commit that changes it, with the reason. Silence is the failure mode this
file exists to break.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wuwaterm_client import strings  # noqa: E402
from wuwaterm_client.api import (  # noqa: E402
    ApiClient,
    MetaResult,
    TermMatch,
    TermsResult,
)
from wuwaterm_client.errors import ClientError  # noqa: E402
from wuwaterm_client.ui.status_view import StatusView  # noqa: E402
from wuwaterm_client.ui.terms_view import TermsView  # noqa: E402
from wuwaterm_client.ui.translate_view import TranslateView  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Service:
    """One transport for all three endpoints, recording what it was asked.

    ``held`` names the payloads whose reply never arrives, which is how a
    request is made to still be running when the next thing happens. A held
    request that is interrupted records itself, so "it was really stopped" is
    an assertion rather than an inference from the test not hanging.
    """

    def __init__(self, held: "set[str] | None" = None, fail: bool = False) -> None:
        self.asked: list[str] = []
        self.interrupted: list[str] = []
        self._held = held or set()
        self._fail = fail

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/terms"):
            key = request.url.params.get("q", "")
        elif path.endswith("/meta"):
            key = "meta"
        else:
            key = "translate"
        self.asked.append(key)
        if key in self._held:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.interrupted.append(key)
                raise
            raise AssertionError("a held request must never answer")
        if self._fail:
            return httpx.Response(
                503,
                json={"error": {"code": "llm_unavailable"}, "request_id": "req-fail"},
            )
        if path.endswith("/terms"):
            return httpx.Response(
                200,
                json={
                    "query": key,
                    "matches": [
                        {
                            "zh": "今汐",
                            "en": "Jinhsi",
                            "category": "resonator",
                            "score": 100.0,
                            "reason": "exact",
                        }
                    ],
                    "request_id": "req-terms",
                },
            )
        if path.endswith("/meta"):
            return httpx.Response(
                200,
                json={
                    "service_version": "9.9.9",
                    "source_profile": "full",
                    "source_commit": "abc1234",
                    "term_count": 7,
                    "llm_configured": True,
                    "request_id": "req-meta",
                },
            )
        return httpx.Response(
            200,
            json={
                "text": "Jinhsi",
                "kind": "exact",
                "dictionary_miss": False,
                "request_id": "req-translate",
            },
        )


def _client(handler, base_url: "str | None" = "https://test") -> ApiClient:
    return ApiClient(
        base_url,
        _test_transport=httpx.MockTransport(handler),
        token_provider=lambda: "wtd1.device.secret",
    )


async def _settle(view) -> None:
    task = view._task
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


def _populate_terms(view: TermsView) -> None:
    """Put real rows on screen the way a successful lookup would."""
    view._render_result(
        TermsResult(
            query="今汐",
            matches=(
                TermMatch(
                    zh="今汐", en="Jinhsi", category="resonator", score=100.0,
                    reason="exact",
                ),
            ),
            request_id="req-ok",
        )
    )
    assert view.table.rowCount() == 1, "fixture failed to put rows on screen"


# == TermsView =============================================================
# main: client/src/wuwaterm_client/ui/terms_view.py @ fe3604b


def test_terms_lookup_starts_only_when_submitted(qapp) -> None:
    """main:66-76 - the only caller of the request path is
    `_on_search_clicked`, reached from `clicked` and `returnPressed`. Nothing
    observes the text as it changes, so text alone never costs a request."""
    service = _Service()
    view = TermsView(_client(service))

    async def scenario() -> None:
        for text in ("J", "Ji", "Jin", "今汐"):
            view.query_edit.setText(text)
        assert service.asked == [], "typing alone must not reach the service"

        view._on_search_clicked()
        await _settle(view)

    asyncio.run(scenario())
    assert service.asked == ["今汐"]


def test_terms_refuses_a_second_lookup_while_one_runs(qapp) -> None:
    """main:71-72 - `if self._task is not None and not self._task.done():
    return`. Enter reaches the handler directly even while the button is
    disabled, so the handler itself has to refuse."""
    service = _Service(held={"今汐"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("今汐")
        view._on_search_clicked()
        await asyncio.sleep(0.05)
        running = view._task
        assert service.asked == ["今汐"]

        view._on_search_clicked()

        assert service.asked == ["今汐"], "a second submit started a second request"
        assert view._task is running, "the running request was replaced"

        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())
    assert service.interrupted == ["今汐"]


def test_terms_empty_query_is_not_sent(qapp) -> None:
    """main:73-75 - `query = ...strip()`, `if not query: return`.

    Driven inside a running loop on purpose. Without one, a view that DID
    start a request would only create a task that never runs, and the
    service would record nothing either way - the assertion would pass on
    broken code.
    """
    service = _Service()
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("   ")
        view._on_search_clicked()
        await _settle(view)

    asyncio.run(scenario())
    assert service.asked == []


def test_terms_error_clears_the_previous_matches(qapp) -> None:
    """main:96-98 - the `except ClientError` branch calls
    `self.table.setRowCount(0)` BEFORE writing the error. Rows left standing
    under a failure banner are read as the failed query's results."""
    view = TermsView(_client(_Service()))
    _populate_terms(view)

    view._render_error(ClientError("llm_unavailable", request_id="req-fail"))

    assert view.table.rowCount() == 0, "the previous query's rows survived a failure"


def test_terms_endpoint_change_clears_and_cancels(qapp) -> None:
    """main:78-89 - cancel the in-flight task, `setRowCount(0)`, clear the
    status line, and hand the button back."""
    service = _Service(held={"今汐"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        _populate_terms(view)
        view.query_edit.setText("今汐")
        view._on_search_clicked()
        await asyncio.sleep(0.05)
        running = view._task

        view.reset_for_endpoint_change()

        assert view.table.rowCount() == 0, "rows from the old address survived"

        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())
    assert service.interrupted == ["今汐"], "the in-flight lookup was not stopped"
    assert view.table.rowCount() == 0, "a late reply repopulated the table"


def test_terms_empty_result_says_so(qapp) -> None:
    """main:117 - `empty_status = strings.TERMS_EMPTY if not result.matches`.
    An empty table and an unasked table look identical; the difference has to
    be stated."""
    view = TermsView(_client(_Service()))

    view._render_result(TermsResult(query="无此词", matches=(), request_id="req-empty"))

    assert view.status_label.text() == strings.TERMS_EMPTY


def test_terms_button_is_disabled_while_the_lookup_runs(qapp) -> None:
    """main:92 and main:102 - disabled at the start of `_run_search`, handed
    back in its `finally`."""
    service = _Service(held={"今汐"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("今汐")
        view._on_search_clicked()
        await asyncio.sleep(0.05)
        assert view.search_button.isEnabled() is False, "clickable during a lookup"
        running = view._task
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert view.search_button.isEnabled() is True, "the button never came back"


# == TranslateView =========================================================
# main: client/src/wuwaterm_client/ui/translate_view.py @ fe3604b


def test_translate_empty_source_is_not_sent(qapp) -> None:
    """main:95-97 - `if not text.strip(): return`.

    Inside a running loop, for the reason given on the lookup twin: with no
    loop, a started request is a task that never runs, and the transport
    stays empty whether or not the refusal works.
    """
    service = _Service()
    view = TranslateView(_client(service))

    async def scenario() -> None:
        view.input_edit.setPlainText("   \n  ")
        view._on_translate_clicked()
        await _settle(view)

    asyncio.run(scenario())
    assert service.asked == []


def test_translate_buttons_swap_between_busy_and_idle(qapp) -> None:
    """main:154-166 - busy disables Translate and enables Cancel; idle is the
    exact inverse. A Cancel that stays live with nothing running cancels
    nothing, and a Translate that stays live starts a second request."""
    service = _Service(held={"translate"})
    view = TranslateView(_client(service))

    async def scenario() -> None:
        view.input_edit.setPlainText("今汐")
        view._on_translate_clicked()
        await asyncio.sleep(0.05)
        assert view.translate_button.isEnabled() is False
        assert view.cancel_button.isEnabled() is True

        running = view._task
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert view.translate_button.isEnabled() is True
    assert view.cancel_button.isEnabled() is False


def test_translate_cancel_before_the_first_step_still_returns_to_idle(qapp) -> None:
    """main:102-105 - the done callback, not the coroutine's `finally`. A task
    cancelled before its first step never runs its body, so a view that
    relied on the coroutine alone would sit in the busy state for good."""
    service = _Service(held={"translate"})
    view = TranslateView(_client(service))

    async def scenario() -> None:
        view.input_edit.setPlainText("今汐")
        view._on_translate_clicked()
        # No await between starting and cancelling: the coroutine has not run.
        view._task.cancel()
        await asyncio.gather(view._task, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert view.translate_button.isEnabled() is True, "stuck busy after an early cancel"
    assert view.cancel_button.isEnabled() is False


def test_translate_cancellation_is_reported(qapp) -> None:
    """main:107-109 and main:143-147 - a cancellation the owner asked for is
    an outcome and gets rendered, unlike a lookup this client stopped by
    itself."""
    service = _Service(held={"translate"})
    view = TranslateView(_client(service))

    async def scenario() -> None:
        view.input_edit.setPlainText("今汐")
        view._on_translate_clicked()
        await asyncio.sleep(0.05)
        view._on_cancel_clicked()
        await asyncio.gather(view._task, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    # Asserted on the note the cancelled OUTCOME writes, not on the status
    # line: the line already says "cancelling" from the moment the button was
    # pressed, so it is non-empty whether or not the ending is ever rendered.
    assert view.cancel_note.text().strip(), "the cancelled ending was never drawn"
    assert view.status_label.text() != strings.STATUS_CANCELLING, (
        "the view stayed at 'cancelling' instead of reporting an ending"
    )


def test_translate_error_clears_the_previous_translation(qapp) -> None:
    """main:176-178 - `_show_error` blanks `result_edit` first. A translation
    left under a failure is read as the answer to the text now on screen."""
    view = TranslateView(_client(_Service()))
    view.result_edit.setPlainText("Jinhsi")

    view._show_error(ClientError("llm_unavailable", request_id="req-fail"))

    assert view.result_edit.toPlainText() == ""


def test_translate_endpoint_change_clears_and_cancels(qapp) -> None:
    """main:122-136 - cancel through the same path Cancel uses, blank the
    result, clear the status line, return to idle."""
    service = _Service(held={"translate"})
    view = TranslateView(_client(service))

    async def scenario() -> None:
        view.result_edit.setPlainText("Jinhsi")
        view.input_edit.setPlainText("今汐")
        view._on_translate_clicked()
        await asyncio.sleep(0.05)
        running = view._task

        view.reset_for_endpoint_change()

        assert view.result_edit.toPlainText() == "", "an old address's answer stayed"
        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())
    assert service.interrupted == ["translate"], "the in-flight request was not stopped"


def test_translate_idle_does_not_wipe_the_outcome(qapp) -> None:
    """main:160-166 - `_set_idle` touches the buttons and NOTHING else. It
    runs in the `finally` of every request, i.e. immediately after the
    outcome wrote its own line; clearing there is how a rendered error
    disappears before it can be read."""
    view = TranslateView(_client(_Service()))
    view._show_error(ClientError("llm_unavailable", request_id="req-fail"))
    before = view.status_label.text() + view.banner.message_text
    assert before.strip(), "fixture failed to render an outcome"

    view._set_idle()

    after = view.status_label.text() + view.banner.message_text
    assert after == before, "returning to idle erased the outcome"


# == StatusView ============================================================
# main: client/src/wuwaterm_client/ui/status_view.py @ fe3604b


def test_status_refuses_a_second_refresh_while_one_runs(qapp) -> None:
    """main:93-98 - the button is disabled INSIDE the coroutine, which does
    not run until the loop gets a turn, so two activations before that would
    otherwise start two refreshes whose replies can land in either order."""
    service = _Service(held={"meta"})
    view = StatusView(_client(service))

    async def scenario() -> None:
        view._on_refresh_clicked()
        await asyncio.sleep(0.05)
        running = view._task
        view._on_refresh_clicked()

        assert service.asked == ["meta"], "a second activation started a second refresh"
        assert view._task is running

        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())


def test_status_button_is_disabled_while_the_refresh_runs(qapp) -> None:
    """main:102 and main:113 - disabled at the top of `_run_refresh`, handed
    back in its `finally`."""
    service = _Service(held={"meta"})
    view = StatusView(_client(service))

    async def scenario() -> None:
        view._on_refresh_clicked()
        await asyncio.sleep(0.05)
        assert view.refresh_button.isEnabled() is False
        running = view._task
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert view.refresh_button.isEnabled() is True


def test_status_failure_is_visible(qapp) -> None:
    """main:106-107 - a failed refresh writes the error where the owner is
    already looking. Silence would read as "the service said nothing"."""
    service = _Service(fail=True)
    view = StatusView(_client(service))

    async def scenario() -> None:
        view._on_refresh_clicked()
        await _settle(view)

    asyncio.run(scenario())

    # Asserted on the banner, not on "the status line is non-empty": the
    # refresh writes its loading text there before the request, so that line
    # is non-empty whether or not the failure is ever rendered. The first
    # version of this assertion passed against a `_show_error` mutated to do
    # nothing at all.
    assert view._banner.message_text.strip(), "a failed refresh reported nothing"
    assert view.status_label.text() != strings.STATUS_LOADING, (
        "the area stayed at 'loading' after the refresh had failed"
    )


def test_status_endpoint_change_forgets_the_service_facts(qapp) -> None:
    """main:68-91 - version, profile, commit, term count and model-configured
    identify a SERVICE; left on screen after the address changes they
    describe the wrong one, on the very tab read to find out which service is
    being talked to."""
    service = _Service(held={"meta"})
    view = StatusView(_client(service))

    async def scenario() -> None:
        view._show_meta(
            MetaResult(
                service_version="9.9.9",
                api_version="v1",
                schema_version="1",
                source_profile="full",
                source_commit="abc1234",
                term_count=7,
                llm_configured=True,
                request_id="req-meta",
            )
        )
        view._on_refresh_clicked()
        await asyncio.sleep(0.05)
        running = view._task

        view.reset_for_endpoint_change()

        for label in (
            view.service_version_value,
            view.data_profile_value,
            view.data_commit_value,
            view.term_count_value,
            view.model_configured_value,
        ):
            assert label.text() == strings.STATUS_UNKNOWN_VALUE, (
                "a fact about the previous service survived the address change"
            )
        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())
    assert service.interrupted == ["meta"], "the in-flight refresh was not stopped"


def test_status_endpoint_change_keeps_the_credential_rows(qapp) -> None:
    """main:76-79 - the credential-store rows describe THIS MACHINE, not the
    server, and are the same whichever address is configured. Blanking them
    with the service facts would report the keyring as unknown after an
    unrelated settings change."""
    view = StatusView(_client(_Service()))
    backend_before = view.keyring_backend_value.text()
    credential_before = view.credential_status_value.text()
    assert backend_before.strip(), "fixture failed: no backend name on screen"

    view.reset_for_endpoint_change()

    assert view.keyring_backend_value.text() == backend_before
    assert view.credential_status_value.text() == credential_before
