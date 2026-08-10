"""Real-Qt smoke test: constructs each widget under the offscreen platform
(set in conftest.py) and checks it builds without raising."""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from wuwaterm_client.api import ApiClient  # noqa: E402
from wuwaterm_client.config import ClientConfig  # noqa: E402
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
