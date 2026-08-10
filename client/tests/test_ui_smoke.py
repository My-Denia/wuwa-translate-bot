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
