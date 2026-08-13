"""Real-Qt smoke test: constructs each widget under the offscreen platform
(set in conftest.py) and checks it builds without raising."""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wuwaterm_client.api import ApiClient  # noqa: E402
from wuwaterm_client.config import ClientConfig  # noqa: E402
from wuwaterm_client.errors import message_for  # noqa: E402
from wuwaterm_client.ui.first_run_dialog import FirstRunDialog  # noqa: E402
from wuwaterm_client.ui.main_window import MainWindow  # noqa: E402
from wuwaterm_client.ui.settings_dialog import SettingsDialog  # noqa: E402
from wuwaterm_client.ui.status_view import StatusView  # noqa: E402
from wuwaterm_client.ui.terms_view import TermsView  # noqa: E402
from wuwaterm_client.ui.translate_view import TranslateView  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _dummy_client() -> ApiClient:
    async def handler(request):  # pragma: no cover - never invoked here
        raise AssertionError("no network calls expected in a construction smoke test")

    return ApiClient("https://test", _test_transport=httpx.MockTransport(handler))


def _terms_payload(query: str, request_id: str) -> dict:
    """One row, named after the query that produced it.

    Naming the row after its own query is what makes "whose answer is on
    screen" readable from a single cell.
    """
    return {
        "query": query,
        "matches": [
            {
                "zh": query,
                "en": query.upper(),
                "category": "role",
                "score": 100.0,
                "reason": "exact",
            }
        ],
        "request_id": request_id,
    }


def _assert_the_three_areas_are_reachable(window: MainWindow) -> None:
    """The areas survived the move off tabs, in order, and still switch.

    The window used to hold a `QTabWidget` and `tabs.count() == 3` stood for
    "all three areas are here". A navigation column plus a stack can satisfy a
    count while pointing at nothing, so the same claim now has to be made in
    three parts: the pages exist, they are in the order the owner decided, and
    activating a navigation item really brings its page to the front.
    """
    from wuwaterm_client.ui import main_window as main_window_module

    assert window.stack.count() == 3
    assert len(window.nav_buttons) == 3
    assert [window.stack.widget(index) for index in range(3)] == [
        window.terms_view,
        window.translate_view,
        window.status_view,
    ]

    for page, view in (
        (main_window_module.PAGE_STATUS, window.status_view),
        (main_window_module.PAGE_TRANSLATE, window.translate_view),
        (main_window_module.PAGE_TERMS, window.terms_view),
    ):
        window.nav_buttons[page].click()
        assert window.stack.currentWidget() is view
        assert window.nav_buttons[page].isChecked()
        assert [button.isChecked() for button in window.nav_buttons].count(True) == 1


def test_translate_view_constructs(qapp) -> None:
    view = TranslateView(_dummy_client())
    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()


def test_terms_view_constructs(qapp) -> None:
    view = TermsView(_dummy_client())
    assert view.table.columnCount() == 5


def test_status_view_constructs(qapp) -> None:
    view = StatusView(_dummy_client())
    assert view.refresh_button.isEnabled()


def test_settings_dialog_constructs(qapp) -> None:
    dialog = SettingsDialog(ClientConfig())
    assert dialog.windowTitle()


def test_first_run_dialog_constructs(qapp) -> None:
    dialog = FirstRunDialog()
    assert dialog.windowTitle()


def test_main_window_constructs(qapp, tmp_path, monkeypatch) -> None:
    from wuwaterm_client.ui import main_window as main_window_module

    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(main_window_module, "has_token", lambda: False)

    window = MainWindow(config=ClientConfig())

    assert window.windowTitle()
    _assert_the_three_areas_are_reachable(window)
    # Term lookup is where the window lands: it is the reflex action, and the
    # one area that costs nothing to use.
    assert window.stack.currentWidget() is window.terms_view
    assert window.nav_buttons[main_window_module.PAGE_TERMS].isChecked()
    # An index nobody has is not a page change; the column must not end up
    # naming an area that is not on screen.
    window.show_page(99)
    assert window.stack.currentWidget() is window.terms_view


def test_settings_push_the_new_timeouts_into_the_live_client(qapp, tmp_path, monkeypatch) -> None:
    """A saved timeout that only takes effect next launch is a silent lie.

    The settings dialog writes the value and the window pushes it; without the
    push the running client keeps the old timeout while the file says
    otherwise.
    """
    from wuwaterm_client import config as config_module
    from wuwaterm_client.config import ClientConfig
    from wuwaterm_client.ui import main_window as main_window_module

    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url="https://test", request_timeout_seconds=5.0))
    changed = ClientConfig(
        base_url="https://elsewhere",
        request_timeout_seconds=42.0,
        translate_timeout_seconds=99.0,
    )

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

        def result_config(self):
            return changed

    monkeypatch.setattr(main_window_module, "SettingsDialog", _AcceptedDialog)
    window._on_settings_clicked()

    assert window.api_client._timeout == 42.0
    assert window.api_client._translate_timeout == 99.0
    assert str(window.api_client._client.base_url).startswith("https://elsewhere")


def test_the_first_run_dialog_will_not_continue_without_a_token(qapp) -> None:
    """Accepting an empty field closed the dialog, and the caller then shut
    the whole application down as though the user had chosen Quit.

    The refusal is now stated before the click rather than after it: Continue
    is disabled while the field holds nothing usable, because a button that
    accepts the press and then silently moves focus reads as broken. The
    guarantee is unchanged and still belongs to the caller - `ensure_credential`
    treats anything but an accepted dialog as "no credential yet" - so what
    matters here is that an empty field cannot produce an accepted dialog by
    any route.
    """
    from PySide6.QtWidgets import QDialog

    dialog = FirstRunDialog()
    assert dialog.continue_button.isEnabled() is False

    dialog.token_edit.setText("   ")
    assert dialog.continue_button.isEnabled() is False
    dialog.continue_button.click()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.isVisible() is False or dialog.result() == 0

    # The slot stays reachable without the button - a default-button
    # activation, or a caller holding the dialog - so it refuses on its own
    # rather than relying on the disabled state.
    dialog._on_continue_clicked()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.token() == ""

    dialog.token_edit.setText("wtd1.device.secret")
    assert dialog.continue_button.isEnabled() is True
    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_only_the_last_of_several_term_searches_reaches_the_screen(qapp) -> None:
    """Searching as you type replaces requests instead of queueing behind them.

    This replaces the "a search in flight blocks the next one" rule, which was
    the wrong trade for a field that searches on every character: the answer
    worth showing is always the answer to the LAST thing typed. So the new
    search cancels the previous one - and the cancellation alone is not
    enough. `ApiClient._request` CONSUMES `CancelledError` and raises
    `ClientError(cancelled)` instead, so a cancelled task returns normally,
    arrives back in the view, and would repaint the newer search's state with
    the older search's outcome. What stops that is the generation guard, and
    the only way to see it working is to have several searches genuinely in
    flight at once.
    """
    import asyncio

    from wuwaterm_client import strings

    served: list[str] = []
    release: asyncio.Event | None = None

    async def handler(request):
        query = request.url.params.get("q")
        served.append(query)
        if query != "Jinhsi":
            # The two earlier searches are still waiting here when the last
            # one starts. Without that overlap there is nothing to replace and
            # the guard would never be exercised.
            await release.wait()
        return httpx.Response(200, json=_terms_payload(query, f"req-{query}"))

    view = TermsView(
        ApiClient("https://test", _test_transport=httpx.MockTransport(handler))
    )

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        tasks = []
        for query in ("Jin", "Jinh", "Jinhsi"):
            view.query_edit.setText(query)
            view._on_search_clicked()
            tasks.append(view._task)
            # Enough turns of the loop for the request to reach the transport
            # and block there.
            for _ in range(8):
                await asyncio.sleep(0)
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _ in range(8):
            await asyncio.sleep(0)
        return tasks

    tasks = asyncio.run(scenario())

    # All three really were sent, and they were three separate tasks: the old
    # rule would have produced one request and two silent no-ops.
    assert served == ["Jin", "Jinh", "Jinhsi"]
    assert len({id(task) for task in tasks}) == 3
    assert all(task.done() for task in tasks)

    # Only the last generation is on screen, and only its answer was kept.
    assert view.table.rowCount() == 1
    assert view.table.item(0, 0).text() == "Jinhsi"
    assert view._request_id == "req-Jinhsi"
    assert view.status_label.text() == strings.STATUS_BAR_DONE
    assert list(view._cache) == ["Jinhsi"]
    # ...and the two replaced searches left nothing behind them: no banner, no
    # loading state, no task the view still believes is running.
    assert view.banner.is_showing() is False
    assert view.progress.is_running() is False
    assert view._task is None


def test_an_older_term_search_writes_nothing_however_it_ends(qapp) -> None:
    """A superseded search is silent whether it succeeds, fails or is cancelled.

    Cancelling is only half the mechanism, and the half that cannot be relied
    on: a task cancelled before its first step never runs its own body, and one
    cancelled mid-request comes back as an ordinary `ClientError` because the
    API client consumes the cancellation. So every ending of an older
    generation is driven here directly, against a view that already holds the
    current generation's answer, and the screen has to be identical afterwards.
    """
    import asyncio

    from wuwaterm_client import strings

    blocked: asyncio.Event | None = None
    served: list[str] = []

    async def handler(request):
        query = request.url.params.get("q")
        served.append(query)
        if query == "hang":
            await blocked.wait()
        if query == "boom":
            return httpx.Response(
                429,
                json={"error": {"code": "rate_limited"}, "request_id": "req-boom"},
            )
        return httpx.Response(200, json=_terms_payload(query, f"req-{query}"))

    view = TermsView(
        ApiClient("https://test", _test_transport=httpx.MockTransport(handler))
    )

    async def scenario():
        nonlocal blocked
        blocked = asyncio.Event()

        view.query_edit.setText("Jinhsi")
        view._on_search_clicked()
        await view._task

        stale = view._generation - 1
        assert stale >= 0

        # An older search that SUCCEEDS: its rows and its request id must not
        # replace the current ones, and its answer must not enter the cache -
        # a cached hit would put it back on screen later without a request.
        await view._run_search("Sanhua", stale)
        # An older search that FAILS: no banner, and no back-off either. The
        # brake belongs to the generation the owner is waiting on.
        await view._run_search("boom", stale)
        # An older search that is CANCELLED mid-request, which is how the
        # replacement path actually ends.
        cancelled = asyncio.ensure_future(view._run_search("hang", stale))
        for _ in range(8):
            await asyncio.sleep(0)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        for _ in range(8):
            await asyncio.sleep(0)

    asyncio.run(scenario())

    # All four searches really ran, so what follows is the guard holding and
    # not three coroutines that never reached an exit.
    assert served == ["Jinhsi", "Sanhua", "boom", "hang"]

    assert view.table.rowCount() == 1
    assert view.table.item(0, 0).text() == "Jinhsi"
    assert view._request_id == "req-Jinhsi"
    assert view.status_label.text() == strings.STATUS_BAR_DONE
    assert list(view._cache) == ["Jinhsi"]
    assert view.banner.is_showing() is False
    assert view.field_error.is_showing() is False
    assert view._auto_paused is False


def test_storing_a_first_run_credential_refreshes_the_status_view(
    qapp, tmp_path, monkeypatch
) -> None:
    """The status view read the credential state before the first-run dialog
    ran, so without a refresh it reports "missing" for the whole session."""
    from wuwaterm_client.config import ClientConfig
    from wuwaterm_client.ui import main_window as main_window_module

    stored: dict[str, str] = {}
    monkeypatch.setattr(main_window_module, "has_token", lambda: bool(stored))
    monkeypatch.setattr(
        main_window_module, "store_token", lambda token: stored.update(token=token)
    )

    window = MainWindow(ClientConfig(base_url="https://test"))
    refreshed: list[bool] = []
    monkeypatch.setattr(
        window.status_view,
        "refresh_credential_state",
        lambda: refreshed.append(True),
    )

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

        def token(self):
            return "wtd1.device.secret"

    monkeypatch.setattr(main_window_module, "FirstRunDialog", _AcceptedDialog)

    assert window.ensure_credential() is True
    assert stored["token"] == "wtd1.device.secret"
    assert refreshed == [True]


def test_an_unprotected_address_is_refused_on_screen_and_changes_nothing(
    qapp, monkeypatch
) -> None:
    """The refusal has to reach the owner, not just the transport.

    If the window swallowed the error the settings dialog would close on an
    address the client will never use, and the only sign would be that
    requests kept going somewhere else.

    The refusal used to be a modal warning box. It is now the global banner,
    which is a strictly stronger form of the same claim: a box disappears on
    the first click and takes the reason with it, while the banner stays
    readable while the owner goes back to Settings to fix the address.
    """
    from PySide6.QtWidgets import QDialog

    from wuwaterm_client import strings
    from wuwaterm_client.config import ClientConfig
    from wuwaterm_client.ui import main_window as main_window_module

    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url="http://127.0.0.1:8787"))
    refused = ClientConfig(base_url="http://198.51.100.7:8787", request_timeout_seconds=42.0)

    saved: list[str] = []
    monkeypatch.setattr(
        ClientConfig, "save", lambda self, base_dir=None: saved.append(self.base_url)
    )

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_config(self):
            return refused

    monkeypatch.setattr(main_window_module, "SettingsDialog", _AcceptedDialog)
    window._on_settings_clicked()

    assert window.global_banner.is_showing(), (
        "the owner must be told the address was refused"
    )
    assert window.global_banner.message_text == strings.ERROR_MSG_INSECURE_ENDPOINT
    # Nothing half-applied: not the address, not the timeout, not the file.
    live = window.api_client._client.base_url
    assert (live.scheme, live.host, live.port) == ("http", "127.0.0.1", 8787)
    assert window.config.base_url == "http://127.0.0.1:8787"
    assert window.api_client._timeout != 42.0
    assert saved == []
    # ...and the chip still names the address that is actually in use, rather
    # than the one that was just rejected.
    assert window.endpoint_chip.is_configured is True
    assert window.endpoint_chip.address_text == "http://127.0.0.1:8787"


def test_settings_refuse_an_address_that_cannot_be_used(qapp) -> None:
    """A saved address that fails every request is worse than no change.

    The refusal is stated on the field that caused it and the dialog stays
    open, because the correction has to be made in that field: a modal over a
    dialog that had already closed left the owner with a value they could no
    longer see, let alone edit.
    """
    from PySide6.QtWidgets import QDialog

    from wuwaterm_client import strings

    dialog = SettingsDialog(ClientConfig(base_url="http://127.0.0.1:8787"))
    dialog.base_url_edit.setText("http://127.0.0.1:notaport")
    dialog._on_accepted()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.base_url_error.is_showing() is True
    assert dialog.base_url_error.text() == strings.SETTINGS_INVALID_BASE_URL_MESSAGE
    assert dialog.base_url_edit.property("invalid") is True

    dialog.base_url_edit.setText("http://127.0.0.1:9999")
    # Editing withdraws the complaint: it was about text nobody is looking at
    # any more.
    assert dialog.base_url_error.is_showing() is False
    assert dialog.base_url_edit.property("invalid") is False
    dialog._on_accepted()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_the_caller_can_refuse_an_address_before_the_dialog_closes(qapp) -> None:
    """The window's own refusal lands on the field, not after the fact.

    The transport is the authority on which addresses may be used, and it is
    reached through the callback the window hands in. This is the wiring that
    lets its answer arrive while the field is still on screen.
    """
    from PySide6.QtWidgets import QDialog

    from wuwaterm_client import strings

    seen: list[str] = []

    def refuse(base_url: str) -> str | None:
        seen.append(base_url)
        return strings.ERROR_MSG_INSECURE_ENDPOINT

    dialog = SettingsDialog(ClientConfig(), None, refuse)
    dialog.base_url_edit.setText("  https://api.example.invalid/wuwaterm-api  ")
    dialog._on_accepted()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.base_url_error.is_showing() is True
    assert dialog.base_url_error.text() == strings.ERROR_MSG_INSECURE_ENDPOINT
    assert dialog.base_url_edit.property("invalid") is True
    # The address handed to the check is the one the client would use, not the
    # raw field text: a leading space reads as a relative URL to the transport.
    assert seen == ["https://api.example.invalid/wuwaterm-api"]


def test_cancelling_before_the_task_starts_restores_the_buttons(qapp) -> None:
    """A task cancelled before its first step never runs its own body."""
    import asyncio

    view = TranslateView(_dummy_client())
    view.input_edit.setPlainText("Jinhsi")

    async def scenario() -> None:
        view._on_translate_clicked()
        # No await in between: the task has been scheduled and has not run.
        view._on_cancel_clicked()
        await asyncio.sleep(0.01)

    asyncio.run(scenario())

    assert view.translate_button.isEnabled()
    assert not view.cancel_button.isEnabled()
    assert view.status_label.text() == message_for("cancelled")


def test_a_status_refresh_does_not_start_while_one_is_running(qapp) -> None:
    """The button is disabled inside the coroutine, which has not run yet.

    Unlike term lookup, this area has NOT moved to replacement: a refresh is
    asked for deliberately, one press at a time, so a second one while the
    first is in flight is a double-click rather than a newer question.
    """
    import asyncio

    view = StatusView(_dummy_client())

    class _Pending:
        def done(self) -> bool:
            return False

    view._task = _Pending()
    started = []
    original = asyncio.ensure_future
    try:
        asyncio.ensure_future = lambda *args, **kwargs: started.append(args) or _Pending()
        view._on_refresh_clicked()
    finally:
        asyncio.ensure_future = original

    assert started == []


def test_settings_that_cannot_be_written_still_apply_and_say_so(
    qapp, monkeypatch
) -> None:
    """A save that fails must not be silent, and must not lose the session."""
    from PySide6.QtWidgets import QDialog

    from wuwaterm_client import strings
    from wuwaterm_client.config import ClientConfig
    from wuwaterm_client.ui import main_window as main_window_module

    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url="http://127.0.0.1:8787"))
    changed = ClientConfig(base_url="http://127.0.0.1:9999", request_timeout_seconds=42.0)

    def refuse_to_save(self, base_dir=None):
        raise OSError("read-only")

    monkeypatch.setattr(ClientConfig, "save", refuse_to_save)

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_config(self):
            return changed

    monkeypatch.setattr(main_window_module, "SettingsDialog", _AcceptedDialog)
    window._on_settings_clicked()

    assert window.global_banner.is_showing(), (
        "the owner must be told the settings were not kept"
    )
    assert window.global_banner.message_text == strings.SETTINGS_NOT_SAVED_MESSAGE
    # ...and they really did take effect for this session, which is the other
    # half of the sentence the banner has to be telling the truth about.
    assert window.api_client._timeout == 42.0
    assert window.config.base_url == "http://127.0.0.1:9999"
