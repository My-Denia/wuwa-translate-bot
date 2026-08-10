"""Shown on launch when no device credential is stored yet."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import strings


class FirstRunDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.FIRST_RUN_TITLE)

        message_label = QLabel(strings.FIRST_RUN_MESSAGE, self)
        message_label.setWordWrap(True)

        self.token_edit = QLineEdit(self)
        self.token_edit.setPlaceholderText(strings.FIRST_RUN_TOKEN_PLACEHOLDER)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.continue_button = QPushButton(strings.FIRST_RUN_CONTINUE_BUTTON, self)
        self.continue_button.clicked.connect(self.accept)

        self.quit_button = QPushButton(strings.FIRST_RUN_QUIT_BUTTON, self)
        self.quit_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(self.quit_button)
        button_row.addStretch(1)
        button_row.addWidget(self.continue_button)

        layout = QVBoxLayout(self)
        layout.addWidget(message_label)
        layout.addWidget(self.token_edit)
        layout.addLayout(button_row)

    def token(self) -> str:
        return self.token_edit.text().strip()
