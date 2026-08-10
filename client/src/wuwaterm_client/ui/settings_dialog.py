"""Base URL / timeout / credential lifecycle settings dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import strings
from ..config import DEFAULT_BASE_URL, ClientConfig, usable_base_url
from ..credentials import (
    CredentialStoreUnavailable,
    active_backend_name,
    delete_token,
    has_token,
    store_token,
)
from .token_dialog import TokenDialog


class SettingsDialog(QDialog):
    def __init__(self, config: ClientConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.SETTINGS_TITLE)
        self._config = config

        self.base_url_edit = QLineEdit(config.base_url, self)
        self.base_url_edit.setPlaceholderText(strings.SETTINGS_BASE_URL_PLACEHOLDER)

        self.timeout_spin = QDoubleSpinBox(self)
        self.timeout_spin.setRange(1.0, 600.0)
        self.timeout_spin.setValue(config.request_timeout_seconds)

        self.backend_label = QLabel(active_backend_name(), self)

        self.credential_status_label = QLabel(self)
        self.enter_token_button = QPushButton(self)
        self.enter_token_button.clicked.connect(self._on_enter_token_clicked)
        self.forget_token_button = QPushButton(strings.SETTINGS_FORGET_TOKEN_BUTTON, self)
        self.forget_token_button.clicked.connect(self._on_forget_token_clicked)

        form = QFormLayout()
        form.addRow(strings.SETTINGS_BASE_URL_LABEL, self.base_url_edit)
        form.addRow(strings.SETTINGS_TIMEOUT_LABEL, self.timeout_spin)
        form.addRow(strings.STATUS_KEYRING_BACKEND_LABEL, self.backend_label)

        credential_row = QHBoxLayout()
        credential_row.addWidget(self.credential_status_label)
        credential_row.addWidget(self.enter_token_button)
        credential_row.addWidget(self.forget_token_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self._on_accepted)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(strings.SETTINGS_CREDENTIAL_SECTION_TITLE, self))
        layout.addLayout(credential_row)
        layout.addWidget(buttons)

        self._refresh_credential_status()

    def _refresh_credential_status(self) -> None:
        stored = has_token()
        self.credential_status_label.setText(
            strings.SETTINGS_TOKEN_STATUS_STORED
            if stored
            else strings.SETTINGS_TOKEN_STATUS_MISSING
        )
        self.enter_token_button.setText(
            strings.SETTINGS_CHANGE_TOKEN_BUTTON
            if stored
            else strings.SETTINGS_ENTER_TOKEN_BUTTON
        )

    def _on_enter_token_clicked(self) -> None:
        dialog = TokenDialog(self)
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
                    return
                self._refresh_credential_status()

    def _on_forget_token_clicked(self) -> None:
        confirm = QMessageBox.question(
            self,
            strings.CONFIRM_FORGET_TOKEN_TITLE,
            strings.CONFIRM_FORGET_TOKEN_MESSAGE,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                delete_token()
            except CredentialStoreUnavailable:
                QMessageBox.critical(
                    self,
                    strings.CREDENTIAL_STORE_ERROR_TITLE,
                    strings.CREDENTIAL_STORE_FORGET_ERROR_MESSAGE,
                )
                return
            self._refresh_credential_status()

    def _on_accepted(self) -> None:
        """A saved address that cannot be used is worse than no change.

        An address like `http://127.0.0.1:notaport` is accepted by a plain
        text field and then fails on every request until the operator works
        out that the setting itself is wrong.
        """
        if not usable_base_url(self.base_url_edit.text()):
            QMessageBox.warning(
                self,
                strings.SETTINGS_INVALID_BASE_URL_TITLE,
                strings.SETTINGS_INVALID_BASE_URL_MESSAGE,
            )
            self.base_url_edit.setFocus()
            return
        self.accept()

    def result_config(self) -> ClientConfig:
        base_url = self.base_url_edit.text().strip() or DEFAULT_BASE_URL
        return ClientConfig(
            base_url=base_url,
            request_timeout_seconds=self.timeout_spin.value(),
            translate_timeout_seconds=self._config.translate_timeout_seconds,
        )
