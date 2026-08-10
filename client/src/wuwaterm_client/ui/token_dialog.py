"""A small dialog for entering a device token.

Shared by the first-run flow and Settings (enter/change token). It never
persists anything itself: the caller decides what to do with ``token()``.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

from .. import strings


class TokenDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.TOKEN_DIALOG_TITLE)

        self.token_edit = QLineEdit(self)
        self.token_edit.setPlaceholderText(strings.FIRST_RUN_TOKEN_PLACEHOLDER)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(strings.TOKEN_DIALOG_LABEL, self))
        layout.addWidget(self.token_edit)
        layout.addWidget(buttons)

    def token(self) -> str:
        return self.token_edit.text().strip()
