"""Shown on launch when no device credential is stored yet.

Same shape as the token dialog it shares a field with, plus the paragraph
that explains where the credential comes from and where it is kept - the two
questions an owner meeting this window for the first time actually has.

What this window must never do is let the application past it without a
credential. That guarantee is the CALLER's: ``ensure_credential`` treats
anything but an accepted dialog with a token as "not configured yet". Nothing
here weakens it - the empty-token path refuses to accept, and a credential
store that could not be written keeps the dialog open rather than reporting
success.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import strings
from .components import Banner
from .error_presentation import SEVERITY_DANGER
from .token_dialog import TokenField


class FirstRunDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.FIRST_RUN_TITLE)

        headline = QLabel(strings.FIRST_RUN_HEADLINE, self)
        headline.setObjectName("pageTitle")

        message_label = QLabel(strings.FIRST_RUN_MESSAGE, self)
        message_label.setWordWrap(True)

        # Where the secret ends up, said before it is typed rather than after.
        # It is the difference between pasting a credential into an
        # application and pasting one into a file.
        storage_note = QLabel(strings.FIRST_RUN_STORAGE_NOTE, self)
        storage_note.setObjectName("emptySubtitle")
        storage_note.setWordWrap(True)

        field_label = QLabel(strings.TOKEN_DIALOG_LABEL, self)

        self._field = TokenField(self)
        self.token_edit = self._field.edit
        field_label.setBuddy(self.token_edit)
        self.token_edit.textChanged.connect(self._on_token_changed)

        # A credential store that is momentarily unavailable used to end the
        # session: a modal error, then the application closed as though Quit
        # had been chosen. It is a retryable condition, so it is reported
        # inside the window that can retry it.
        self._banner = Banner(self)

        self.quit_button = QPushButton(strings.FIRST_RUN_QUIT_BUTTON, self)
        self.quit_button.setObjectName("secondaryButton")
        self.quit_button.clicked.connect(self.reject)

        self.continue_button = QPushButton(strings.FIRST_RUN_CONTINUE_BUTTON, self)
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setDefault(True)
        self.continue_button.clicked.connect(self._on_continue_clicked)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self.quit_button)
        button_row.addStretch(1)
        button_row.addWidget(self.continue_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(headline)
        layout.addWidget(message_label)
        layout.addWidget(storage_note)
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

        The caller keeps the dialog running instead of returning: coming back
        here with the token still in the field is the only useful response to
        a vault that was busy a second ago, and it is also what keeps the "no
        credential, no main window" rule intact - a failed write must never
        look like a successful one.
        """
        self._banner.show_message(text, SEVERITY_DANGER)

    # -- internals ---------------------------------------------------------

    def _on_token_changed(self) -> None:
        # Continue with nothing in the field used to be clickable and silently
        # do nothing but move focus, which reads as a broken button. Disabled
        # says the same thing without pretending an action happened.
        self.continue_button.setEnabled(bool(self.token()))

    def _on_continue_clicked(self) -> None:
        """Continue only means something with a token in the field.

        Kept as a check even though the button is disabled without one: this
        slot is reachable by other means (a default-button activation, a
        direct call in a test), and accepting an empty field closed the dialog
        - after which the caller found no credential and shut the whole
        application down as though the user had chosen Quit.
        """
        if not self.token():
            self.token_edit.setFocus()
            return
        self.accept()
