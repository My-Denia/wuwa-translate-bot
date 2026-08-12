"""The window has to show which server it is talking to, or that it has none.

The failure this closes was invisible by construction: the client silently
adopted a machine-local development address whenever its settings file could
not be read, and nothing on screen said which address was in use - so a
missing `config.json` looked exactly like an unreachable service.

Real Qt under the offscreen platform (conftest.py), like the other UI tests
here; the state logic itself is a plain function so it can be checked without
driving a widget.

Named `test_ui_*` for the same reason the other Qt files are: the suite runs
in file order, `tests/test_packaging_entry.py` builds its OWN QApplication to
rehearse a packaged start-up, and libshiboken refuses a second one while a
session-scoped instance is alive. Every file that keeps a QApplication has to
sort after it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from wuwaterm_client import strings  # noqa: E402
from wuwaterm_client.config import ClientConfig  # noqa: E402
from wuwaterm_client.ui import main_window as main_window_module  # noqa: E402
from wuwaterm_client.ui.main_window import MainWindow, endpoint_status_text  # noqa: E402

CONFIGURED = "https://api.example.invalid/wuwaterm-api"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_the_state_logic_distinguishes_the_two_cases() -> None:
    unconfigured = endpoint_status_text(None)
    configured = endpoint_status_text(CONFIGURED)

    assert unconfigured == strings.ENDPOINT_NOT_CONFIGURED
    assert unconfigured != configured
    # The configured line names the address, so a client pointed somewhere
    # unexpected can be recognised as such by reading the window.
    assert CONFIGURED in configured
    # ...and the unconfigured line names the place to fix it, because nothing
    # the owner did put them in that state.
    assert "Settings" in unconfigured
    assert "not configured" in unconfigured


def test_the_window_shows_the_configured_address(qapp, monkeypatch) -> None:
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url=CONFIGURED))

    assert window.endpoint_label.text() == endpoint_status_text(CONFIGURED)
    assert CONFIGURED in window.endpoint_label.text()


def test_the_window_says_so_when_nothing_is_configured(qapp, monkeypatch) -> None:
    """A config file that vanished must produce a window that says the
    address is gone - not one that quietly points at a development port."""
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig())

    assert window.endpoint_label.text() == strings.ENDPOINT_NOT_CONFIGURED
    assert "127.0.0.1" not in window.endpoint_label.text()
    assert window.api_client.is_configured is False
    # The window is still usable: Settings is how the owner recovers, so it
    # has to be reachable rather than blocked behind a broken client.
    assert window.tabs.count() == 3


def test_setting_an_address_updates_what_the_window_shows(
    qapp, tmp_path, monkeypatch
) -> None:
    """The label is not read once at start-up: a client configured during the
    session must stop reporting that it is not."""
    from wuwaterm_client import config as config_module

    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig())
    assert window.endpoint_label.text() == strings.ENDPOINT_NOT_CONFIGURED

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_config(self):
            return ClientConfig(base_url=CONFIGURED)

    monkeypatch.setattr(main_window_module, "SettingsDialog", _AcceptedDialog)
    window._on_settings_clicked()

    assert window.endpoint_label.text() == endpoint_status_text(CONFIGURED)
    assert window.api_client.is_configured is True
    # ...and it was written where the next launch will look for it.
    assert ClientConfig.load(tmp_path).base_url == CONFIGURED


def test_a_refused_address_does_not_change_what_the_window_shows(
    qapp, monkeypatch
) -> None:
    """A refusal must not half-apply to the label either, or the window would
    describe a client that does not exist."""
    warned: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a))
    )
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url=CONFIGURED))

    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_config(self):
            return ClientConfig(base_url="http://198.51.100.7:8787")

    monkeypatch.setattr(main_window_module, "SettingsDialog", _AcceptedDialog)
    window._on_settings_clicked()

    assert warned
    assert window.endpoint_label.text() == endpoint_status_text(CONFIGURED)


def test_the_settings_dialog_opens_empty_when_nothing_is_configured(qapp) -> None:
    """The field shows what is set. Pre-filling it with a development address
    is how a value nobody chose gets saved by pressing OK."""
    from wuwaterm_client.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(ClientConfig())

    assert dialog.base_url_edit.text() == ""
    # The example still exists - as a placeholder, which is not a value.
    assert dialog.base_url_edit.placeholderText()
    assert dialog.result_config().base_url is None
