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

    return ApiClient("http://test", transport=httpx.MockTransport(handler))


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
    monkeypatch.setenv("APPDATA", str(tmp_path))
    window = MainWindow(config=ClientConfig())
    assert window.windowTitle()
    assert window.tabs.count() == 3


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

    window = MainWindow(ClientConfig(base_url="http://test", request_timeout_seconds=5.0))
    changed = ClientConfig(
        base_url="http://elsewhere",
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
    assert str(window.api_client._client.base_url).startswith("http://elsewhere")


def test_the_first_run_dialog_will_not_continue_without_a_token(qapp) -> None:
    """Accepting an empty field closed the dialog, and the caller then shut
    the whole application down as though the user had chosen Quit."""
    from PySide6.QtWidgets import QDialog

    dialog = FirstRunDialog()
    dialog.token_edit.setText("   ")
    dialog.continue_button.click()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.isVisible() is False or dialog.result() == 0

    dialog.token_edit.setText("wtd1.device.secret")
    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_a_term_search_does_not_start_while_one_is_running(qapp) -> None:
    """The button is disabled during a search, but Enter calls the handler."""
    import asyncio

    view = TermsView(_dummy_client())
    view.query_edit.setText("Jinhsi")

    class _Pending:
        def done(self) -> bool:
            return False

    view._task = _Pending()
    started = []
    original = asyncio.ensure_future
    try:
        asyncio.ensure_future = lambda *args, **kwargs: started.append(args) or _Pending()
        view._on_search_clicked()
    finally:
        asyncio.ensure_future = original

    assert started == []


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

    window = MainWindow(ClientConfig(base_url="http://test"))
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


def test_settings_refuse_an_address_that_cannot_be_used(qapp, monkeypatch) -> None:
    """A saved address that fails every request is worse than no change."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    from wuwaterm_client.config import ClientConfig
    from wuwaterm_client.ui import settings_dialog as settings_module

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    dialog = SettingsDialog(ClientConfig(base_url="http://127.0.0.1:8787"))
    dialog.base_url_edit.setText("http://127.0.0.1:notaport")
    dialog._on_accepted()
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog.base_url_edit.setText("http://127.0.0.1:9999")
    dialog._on_accepted()
    assert dialog.result() == QDialog.DialogCode.Accepted


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
    """The button is disabled inside the coroutine, which has not run yet."""
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
    from PySide6.QtWidgets import QDialog, QMessageBox

    from wuwaterm_client.config import ClientConfig
    from wuwaterm_client.ui import main_window as main_window_module

    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[1]))
    )
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

    assert warned, "the owner must be told the settings were not kept"
    assert window.api_client._timeout == 42.0
