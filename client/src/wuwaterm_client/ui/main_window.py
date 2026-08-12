"""Top-level window: tabs for translate / term lookup / status, plus the
Settings and first-run flows."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import strings
from ..api import ApiClient
from ..config import ClientConfig
from ..credentials import CredentialStoreUnavailable, has_token, store_token
from ..errors import ClientError
from .first_run_dialog import FirstRunDialog
from .settings_dialog import SettingsDialog
from .status_view import StatusView
from .terms_view import TermsView
from .translate_view import TranslateView


def endpoint_status_text(base_url: str | None) -> str:
    """The line the window shows about where its requests go.

    A separate function, and the only place the two states are turned into
    text, so the state logic can be exercised without driving the widget.

    An unconfigured client has to LOOK unconfigured. The address used to be
    substituted silently when `config.json` went missing, and the owner then
    read a connection error - about a machine-local development port they had
    never chosen - as the service being down.
    """
    if base_url is None:
        return strings.ENDPOINT_NOT_CONFIGURED
    return strings.ENDPOINT_CONFIGURED.format(base_url=base_url)


class MainWindow(QMainWindow):
    def __init__(
        self, config: ClientConfig | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.APP_TITLE)
        self.config = config if config is not None else ClientConfig.load()
        self.api_client = ApiClient.from_config(self.config)

        self.tabs = QTabWidget(self)
        self.translate_view = TranslateView(self.api_client, self)
        self.terms_view = TermsView(self.api_client, self)
        self.status_view = StatusView(self.api_client, self)
        self.tabs.addTab(self.translate_view, strings.TRANSLATE_TAB_TITLE)
        self.tabs.addTab(self.terms_view, strings.TERMS_TAB_TITLE)
        self.tabs.addTab(self.status_view, strings.STATUS_TAB_TITLE)

        # Above the tabs rather than in a status bar: it is true of every tab,
        # and while it says "not configured" it is the only thing on screen
        # worth acting on.
        self.endpoint_label = QLabel(self)
        self.endpoint_label.setWordWrap(True)

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(self.endpoint_label)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self._refresh_endpoint_label()
        self._build_menu()

    def _refresh_endpoint_label(self) -> None:
        self.endpoint_label.setText(endpoint_status_text(self.config.base_url))

    def _discard_previous_endpoint_state(self) -> None:
        """Nothing on screen may outlive the address it came from.

        Changing the server address does not stop a request already on its
        way to the old one, and a reply carries no marking that would let a
        view know it is stale. Without this, an answer from the PREVIOUS
        service renders under the new address in the header - the most
        misleading thing this window can display, and the same class of
        defect as the silent fallback this whole change exists to remove.

        Each view cancels its own in-flight task through the mechanism it
        already had, and clears the state that belonged to that endpoint.
        Nothing here coordinates the cancellations or waits for them: they
        are fire-and-forget, exactly as the Cancel button's are.
        """
        for view in (self.translate_view, self.terms_view, self.status_view):
            view.reset_for_endpoint_change()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu(strings.MENU_FILE)
        settings_action = QAction(strings.SETTINGS_MENU_LABEL, self)
        settings_action.triggered.connect(self._on_settings_clicked)
        file_menu.addAction(settings_action)
        quit_action = QAction(strings.MENU_QUIT, self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu(strings.MENU_HELP)
        about_action = QAction(strings.MENU_ABOUT, self)
        about_action.triggered.connect(self._on_about_clicked)
        help_menu.addAction(about_action)

    def _on_settings_clicked(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings(dialog.result_config())
        # Whether or not the dialog was accepted: entering, changing or
        # forgetting a token inside it writes to the credential store
        # immediately, and Cancel does not undo that.
        self.status_view.refresh_credential_state()

    def _apply_settings(self, new_config: ClientConfig) -> None:
        """Adopt an accepted settings change, or refuse it whole.

        The address goes to the live transport FIRST, because it is the only
        part the client can refuse: an address that would carry the device
        token in the clear is rejected there. Adopting such a configuration -
        or writing it to disk, where it would come back at the next launch -
        would leave settings that do not describe the running client, so the
        refusal keeps the previous address in effect and says why.
        """
        previous_base_url = self.config.base_url
        try:
            self.api_client.update_base_url(new_config.base_url)
        except ClientError as exc:
            QMessageBox.warning(
                self,
                strings.SETTINGS_INVALID_BASE_URL_TITLE,
                exc.message,
            )
            return
        self.config = new_config
        # Timeouts too, or the saved settings and the running client
        # disagree until the next launch.
        self.api_client.update_timeouts(
            self.config.request_timeout_seconds,
            self.config.translate_timeout_seconds,
        )
        if new_config.base_url != previous_base_url:
            self._discard_previous_endpoint_state()
        # ...and the address on screen, which is the whole point of showing
        # it: a client that has just been configured must stop saying it is
        # not.
        self._refresh_endpoint_label()
        try:
            self.config.save()
        except OSError:
            # The settings still take effect for this session; what the
            # owner must not be left believing is that they were kept.
            QMessageBox.warning(
                self,
                strings.SETTINGS_TITLE,
                strings.SETTINGS_NOT_SAVED_MESSAGE,
            )

    def _on_about_clicked(self) -> None:
        QMessageBox.information(self, strings.MENU_ABOUT, strings.ABOUT_TEXT)

    def ensure_credential(self) -> bool:
        """Run the first-run flow if no device credential is stored yet.

        Returns True once a credential is present (already stored, or just
        entered); False if the user chose to quit instead.
        """
        if has_token():
            return True
        dialog = FirstRunDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            token = dialog.token()
            if token:
                try:
                    store_token(token)
                except CredentialStoreUnavailable:
                    QMessageBox.critical(
                        self,
                        strings.CREDENTIAL_STORE_ERROR_TITLE,
                        strings.CREDENTIAL_STORE_ERROR_MESSAGE,
                    )
                    return False
                # The status view read the credential state when it was
                # built, which was before this. Without the refresh it keeps
                # reporting a missing credential for the whole session.
                self.status_view.refresh_credential_state()
                return True
        return False
