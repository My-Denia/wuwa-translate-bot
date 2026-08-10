"""The status line must survive the end of a request.

Every outcome of a translation is reported in one place: the status label.
A finally-clause that reset the label as part of restoring the buttons wiped
the rendered outcome in the same tick it was written, so a real run showed an
empty status for a success, for an error and for a cancellation alike. Unit
tests that called the render helpers directly could not see it; these drive
the whole coroutine.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wuwaterm_client.api import ApiClient  # noqa: E402
from wuwaterm_client.errors import ERROR_OFFLINE, message_for  # noqa: E402
from wuwaterm_client.ui.translate_view import TranslateView  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _view(handler) -> TranslateView:
    return TranslateView(
        ApiClient(
            "http://test",
            transport=httpx.MockTransport(handler),
            token_provider=lambda: "wtd1.device.secret",
        )
    )


def test_a_successful_translation_leaves_its_outcome_on_screen(qapp) -> None:
    def handler(request):
        return httpx.Response(
            200,
            json={
                "kind": "exact",
                "text": "Jinhsi",
                "direction": "en",
                "dictionary_miss": False,
                "request_id": "abc123",
            },
        )

    view = _view(handler)
    view.input_edit.setPlainText("今汐")
    asyncio.run(view._run_translate("今汐", None))

    assert view.result_edit.toPlainText() == "Jinhsi"
    assert "abc123" in view.status_label.text()
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_an_error_stays_readable_after_the_request_ends(qapp) -> None:
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    view = _view(handler)
    asyncio.run(view._run_translate("Jinhsi", None))

    assert view.status_label.text() == message_for(ERROR_OFFLINE)
    assert view.result_edit.toPlainText() == ""
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_a_cancelled_request_reports_that_it_was_cancelled(qapp) -> None:
    async def handler(request):
        await asyncio.sleep(30)  # pragma: no cover - cancelled first
        raise AssertionError("the request should have been cancelled")

    view = _view(handler)

    async def scenario() -> None:
        view._set_busy(True)
        task = asyncio.ensure_future(view._run_translate("Jinhsi", None))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert view.status_label.text() == message_for("cancelled")
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()
