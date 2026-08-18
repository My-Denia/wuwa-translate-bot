"""A small dialog for entering a device token.

Shared by the first-run flow and Settings (enter/change token). It never
persists anything itself: the caller decides what to do with ``token()``.

The field and its reveal button live here as ``TokenField`` because the
first-run dialog needs exactly the same one. A token is pasted, not typed,
and a masked field gives the owner no way to tell a good paste from one that
picked up a leading space or lost its last character - so the mask is a
default, not a cage.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import strings
from .components import Banner
from .error_presentation import SEVERITY_DANGER


class TokenField(QFrame):
    """A masked token input with a reveal toggle sitting against its edge.

    The toggle is checkable rather than a pair of buttons: its own pressed
    state is the second encoding of "the secret is currently on screen", which
    a label alone would not give on a screenshot.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(strings.FIRST_RUN_TOKEN_PLACEHOLDER)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.toggle_button = QPushButton(strings.TOKEN_SHOW_BUTTON, self)
        self.toggle_button.setObjectName("secondaryButton")
        self.toggle_button.setCheckable(True)
        self.toggle_button.toggled.connect(self._on_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.toggle_button)

    def _on_toggled(self, revealed: bool) -> None:
        self.edit.setEchoMode(
            QLineEdit.EchoMode.Normal if revealed else QLineEdit.EchoMode.Password
        )
        self.toggle_button.setText(
            strings.TOKEN_HIDE_BUTTON if revealed else strings.TOKEN_SHOW_BUTTON
        )


class TokenDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.TOKEN_DIALOG_TITLE)

        title = QLabel(strings.TOKEN_DIALOG_TITLE, self)
        title.setObjectName("pageTitle")

        message = QLabel(strings.TOKEN_DIALOG_MESSAGE, self)
        message.setObjectName("emptySubtitle")
        message.setWordWrap(True)

        field_label = QLabel(strings.TOKEN_DIALOG_LABEL, self)

        self._field = TokenField(self)
        self.token_edit = self._field.edit
        field_label.setBuddy(self.token_edit)
        self.token_edit.textChanged.connect(self._on_token_changed)

        # Storing the token is the caller's job and can fail in a way the
        # owner can retry - the vault being momentarily unavailable. That
        # failure belongs here, in the dialog that stays open, and not in a
        # box whose only button dismisses the one place the retry can happen.
        self._banner = Banner(self)

        self.cancel_button = QPushButton(strings.TOKEN_DIALOG_CANCEL_BUTTON, self)
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.reject)

        self.save_button = QPushButton(strings.TOKEN_DIALOG_SAVE_BUTTON, self)
        self.save_button.setObjectName("primaryButton")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._on_save_clicked)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch(1)
        button_row.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(message)
        layout.addWidget(field_label)
        layout.addWidget(self._field)
        layout.addWidget(self._banner)
        layout.addStretch(1)
        layout.addLayout(button_row)

        self._on_token_changed()

    # -- public API --------------------------------------------------------

    def token(self) -> str:
        return self.token_edit.text().strip()

    def show_store_error(self, text: str) -> None:
        """Report that the credential could not be stored, and stay open.

        The caller is the only one that knows storing failed, and the useful
        answer to it is another attempt - which needs this dialog still on
        screen with the token still in the field.
        """
        self._banner.show_message(text, SEVERITY_DANGER)

    # -- internals ---------------------------------------------------------

    def _on_token_changed(self) -> None:
        # Saving nothing is not a decision the owner can have meant; a button
        # that accepts and then does nothing reads as a broken button.
        self.save_button.setEnabled(bool(self.token()))

    def _on_save_clicked(self) -> None:
        if not self.token():
            return
        self.accept()
