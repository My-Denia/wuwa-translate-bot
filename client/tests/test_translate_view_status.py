"""Every outcome of a translation has to survive the end of its request.

A finally-clause that reset the status line as part of restoring the buttons
wiped the rendered outcome in the same tick it was written, so a real run
showed nothing at all for a success, for a failure and for a cancellation
alike. Unit tests that called the render helpers directly could not see it;
these drive the whole coroutine.

The outcome is no longer one line of text. It is now spread over four
surfaces, and each of them is asserted here rather than folded back into a
single string:

* the result box and the source badge, which say WHAT came back;
* the request id row, which is the only handle the owner has when asking an
  operator what happened - so it renders in the same widget, in the same
  place, for a success, for a failure and for no id at all;
* the banner and the field-error line, which is where a failure lands
  according to ``ui/error_presentation`` and not according to this view;
* the status line, which keeps one terminal word.

The last test is the ordering guarantee. ``api._request`` turns a
cancellation into an ordinary error, so a request stopped because the server
address changed comes back looking like a perfectly renderable outcome; the
view's generation counter is what stops it being drawn over a cleared area.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wuwaterm_client import strings  # noqa: E402
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
            "https://test",
            _test_transport=httpx.MockTransport(handler),
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

    # The id is readable, and readable in full: the row shortens what it
    # DRAWS, never what it holds, so a value read back to an operator is the
    # value the service issued.
    assert view.current_request_id == "abc123"
    assert view.request_id_label.full_text() == "abc123"
    assert view.request_id_label.toolTip() == "abc123"
    assert view.request_id_copy_button.isEnabled()

    # Where the text came from, kept apart from whether an official term
    # stood behind it - two questions, two badges.
    assert view.kind_badge.current_kind == "exact"
    assert view.kind_badge.label_text == strings.KIND_LABEL_EXACT
    assert view.kind_badge.isVisibleTo(view)
    assert view.miss_badge.isVisibleTo(view) is False
    assert view.miss_note.isVisibleTo(view) is False

    assert view.status_label.text() == strings.STATUS_BAR_DONE
    assert view.banner.is_showing() is False
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_a_translation_with_no_official_term_behind_it_says_so(qapp) -> None:
    """The second badge is a second dimension, not a replacement.

    Which of the four kinds produced the text and whether the dictionary
    backed it are independently true, and collapsing them into one mark would
    lose whichever one lost the fight.
    """

    def handler(request):
        return httpx.Response(
            200,
            json={
                "kind": "llm",
                "text": "Jinhsi",
                "direction": "en",
                "dictionary_miss": True,
                "request_id": "abc123",
            },
        )

    view = _view(handler)
    asyncio.run(view._run_translate("今汐", None))

    assert view.kind_badge.current_kind == "llm"
    assert view.kind_badge.label_text == strings.KIND_LABEL_LLM
    assert view.miss_badge.isVisibleTo(view)
    assert view.miss_badge.label_text == strings.DICTIONARY_MISS_BADGE
    assert view.miss_note.text() == strings.DICTIONARY_MISS_NOTE
    assert view.miss_note.isVisibleTo(view)


def test_an_error_stays_readable_after_the_request_ends(qapp) -> None:
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    view = _view(handler)
    asyncio.run(view._run_translate("Jinhsi", None))

    # The wording did not move: it moved SURFACE. A failure the owner has to
    # act on is drawn in the area's banner, and the status line keeps the one
    # terminal word.
    assert view.banner.is_showing() is True
    assert view.banner.message_text == message_for(ERROR_OFFLINE)
    assert view.status_label.text() == strings.STATUS_BAR_LAST_REQUEST_FAILED

    # The id row does not disappear for the outcomes an operator is actually
    # asked about; with no id it holds the placeholder and offers no copy.
    assert view.current_request_id is None
    assert view.request_id_label.full_text() == strings.REQUEST_ID_PLACEHOLDER
    assert view.request_id_copy_button.isEnabled() is False

    assert view.result_edit.toPlainText() == ""
    assert view.field_error.is_showing() is False
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_a_fault_in_the_text_is_reported_against_the_text(qapp) -> None:
    """A code whose fix is in the box in front of the owner is not banner
    material: the complaint goes on the box, and the first keystroke that
    could fix it takes the complaint back off."""

    def handler(request):
        return httpx.Response(
            400,
            json={"error": {"code": "input_too_long"}, "request_id": "req-7"},
        )

    view = _view(handler)
    view.input_edit.setPlainText("今汐" * 100)
    asyncio.run(view._run_translate("今汐", None))

    assert view.field_error.is_showing() is True
    assert view.field_error.text() == strings.ERROR_MSG_INPUT_TOO_LONG
    assert view.input_edit.property("invalid") is True
    assert view.banner.is_showing() is False
    assert view.status_label.text() == strings.STATUS_BAR_LAST_REQUEST_FAILED
    # Same row, same place, for this outcome too.
    assert view.current_request_id == "req-7"

    view.input_edit.setPlainText("今汐")
    assert view.field_error.is_showing() is False
    assert view.input_edit.property("invalid") is False


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

    # The owner asked for this, so it is a confirmation and stays on the quiet
    # line rather than turning into a coloured box.
    assert view.status_label.text() == message_for("cancelled")
    assert view.banner.is_showing() is False
    # ...and what cancelling did NOT do is said out loud, because the machine
    # on the other side may well still be working on it.
    assert view.cancel_note.text() == strings.STATUS_CANCELLED_NOTE
    assert view.cancel_note.isVisibleTo(view)
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_an_outcome_from_the_previous_address_is_never_drawn(qapp) -> None:
    """The ordering guarantee, driven the only way it can fail.

    The request has to be IN the await when the address changes: a task
    cancelled before its first step never reaches the rendering code at all,
    so that arrangement stays green with the generation counter deleted and
    proves nothing. Here the coroutine really does come back with a
    renderable outcome - `api._request` hands it a `ClientError` rather than
    letting it end as cancelled - into an area that has already been cleared
    for a different server.
    """

    async def handler(request):
        await asyncio.sleep(30)  # pragma: no cover - cancelled first
        raise AssertionError("the request should have been cancelled")

    view = _view(handler)
    view.input_edit.setPlainText("今汐")

    async def scenario() -> None:
        view._on_translate_clicked()
        await asyncio.sleep(0.05)
        task = view._task
        assert task is not None, "the request must be running before the reset"
        view.reset_for_endpoint_change()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert view.status_label.text() == ""
    assert view.banner.is_showing() is False
    assert view.cancel_note.text() == ""
    assert view.current_request_id is None
    assert view.result_edit.toPlainText() == ""
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_retry_sends_what_the_editor_shows_now(qapp) -> None:
    """重试提交的必须是屏幕上当前的原文。

    提示条在编辑原文时不会消失,所以「重试」曾经重发保存下来的旧原文 —— 于是
    旧原文的译文渲染在新原文底下,被归给了一段从未产生过它的输入。这与换址
    清屏要防的是同一件事:答案绝不能出现在并非它来源的文本之下。
    """
    sent: list[str] = []

    async def handler(request):
        import json as _json

        body = _json.loads(request.content.decode("utf-8"))
        sent.append(body["text"])
        return httpx.Response(
            200,
            json={
                "kind": "exact",
                "text": "Resonance Circuit",
                "direction": "en",
                "dictionary_miss": False,
                "request_id": "req-retry",
            },
        )

    view = TranslateView(
        ApiClient(
            "https://test",
            _test_transport=httpx.MockTransport(handler),
            token_provider=lambda: "wtd1.device.secret",
        )
    )

    async def scenario() -> None:
        view.input_edit.setPlainText("原文甲")
        view._last_request = ("原文甲", None)
        # 用户在失败之后把原文改成了别的东西。
        view.input_edit.setPlainText("原文乙")
        view._retry_last_request()
        if view._task is not None:
            await asyncio.gather(view._task, return_exceptions=True)

    asyncio.run(scenario())

    assert sent == ["原文乙"], f"重试发的是 {sent},不是编辑框当前内容"
