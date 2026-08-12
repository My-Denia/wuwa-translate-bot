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


class _PendingTask:
    """Stands in for an in-flight request task. Records its cancellation."""

    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


def _accepting_dialog(config: ClientConfig):
    class _AcceptedDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_config(self):
            return config

    return _AcceptedDialog


def test_changing_the_address_cancels_in_flight_work_and_clears_stale_answers(
    qapp, tmp_path, monkeypatch
) -> None:
    """An answer from the OLD server must not render under the NEW address.

    Changing the address does not stop a request already on its way, and a
    reply carries nothing that marks it stale - so without this the window
    shows one service's translation, terms and version under another
    service's name. That is the same defect class as the silent fallback
    this change removes: the screen asserting something it cannot know.
    """
    from wuwaterm_client import config as config_module

    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url=CONFIGURED))

    # State that belongs to the address currently configured.
    tasks = [_PendingTask() for _ in range(3)]
    window.translate_view._task, window.terms_view._task, window.status_view._task = tasks
    window.translate_view.result_edit.setPlainText("Jinhsi")
    window.translate_view.status_label.setText("Exact dictionary match")
    window.terms_view.table.setRowCount(4)
    window.status_view.service_version_value.setText("0.2.1")
    window.status_view.term_count_value.setText("1234")
    window.status_view.status_label.setText("Loading...")

    monkeypatch.setattr(
        main_window_module,
        "SettingsDialog",
        _accepting_dialog(ClientConfig(base_url="https://other.example.invalid")),
    )
    window._on_settings_clicked()

    assert [task.cancelled for task in tasks] == [True, True, True]
    assert window.translate_view.result_edit.toPlainText() == ""
    assert window.translate_view.status_label.text() == ""
    assert window.terms_view.table.rowCount() == 0
    assert window.status_view.status_label.text() == ""
    for stale in ("0.2.1", "1234"):
        assert stale not in (
            window.status_view.service_version_value.text()
            + window.status_view.term_count_value.text()
        )
    # ...and the header names the new address, so nothing on screen still
    # refers to the old one.
    assert window.endpoint_label.text() == endpoint_status_text(
        "https://other.example.invalid"
    )


def test_settings_that_leave_the_address_alone_do_not_discard_work(
    qapp, tmp_path, monkeypatch
) -> None:
    """The reset is triggered by the ADDRESS changing, not by opening
    Settings. Editing a timeout must not cancel a translation in progress or
    wipe a result the owner is reading."""
    from wuwaterm_client import config as config_module

    monkeypatch.setattr(config_module, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url=CONFIGURED, request_timeout_seconds=5.0))
    task = _PendingTask()
    window.translate_view._task = task
    window.translate_view.result_edit.setPlainText("Jinhsi")

    monkeypatch.setattr(
        main_window_module,
        "SettingsDialog",
        _accepting_dialog(
            ClientConfig(base_url=CONFIGURED, request_timeout_seconds=42.0)
        ),
    )
    window._on_settings_clicked()

    assert task.cancelled is False
    assert window.translate_view.result_edit.toPlainText() == "Jinhsi"
    assert window.api_client._timeout == 42.0


def test_a_refused_address_does_not_discard_the_work_in_progress(
    qapp, monkeypatch
) -> None:
    """A refusal changes nothing, so it must not cancel anything either."""
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(main_window_module, "has_token", lambda: True)

    window = MainWindow(ClientConfig(base_url=CONFIGURED))
    task = _PendingTask()
    window.translate_view._task = task
    window.translate_view.result_edit.setPlainText("Jinhsi")

    monkeypatch.setattr(
        main_window_module,
        "SettingsDialog",
        _accepting_dialog(ClientConfig(base_url="http://198.51.100.7:8787")),
    )
    window._on_settings_clicked()

    assert task.cancelled is False
    assert window.translate_view.result_edit.toPlainText() == "Jinhsi"


def test_the_settings_dialog_opens_empty_when_nothing_is_configured(qapp) -> None:
    """The field shows what is set. Pre-filling it with a development address
    is how a value nobody chose gets saved by pressing OK."""
    from wuwaterm_client.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(ClientConfig())

    assert dialog.base_url_edit.text() == ""
    # The example still exists - as a placeholder, which is not a value.
    assert dialog.base_url_edit.placeholderText()
    assert dialog.result_config().base_url is None
