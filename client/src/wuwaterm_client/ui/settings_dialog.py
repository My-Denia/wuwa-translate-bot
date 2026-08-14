"""Base URL / timeout / credential lifecycle / appearance settings dialog.

Three cards, because the three groups fail independently: an address can be
refused while the credential is fine, a credential store can be unavailable
while the address is good, and the appearance is never wrong at all. A single
flat form made every one of those look like a fault of "the settings".

Nothing in here writes anything. The address and the timeout leave through
``result_config`` and are applied by the window; the credential buttons are
the exception and say so - they take effect immediately, which is why the
window refreshes its credential state whether or not this dialog was
accepted.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import strings, theme
from ..config import (
    APPEARANCE_DARK,
    APPEARANCE_LIGHT,
    APPEARANCE_SYSTEM,
    APPEARANCE_VALUES,
    DEFAULT_APPEARANCE,
    ClientConfig,
    usable_base_url,
)
from ..credentials import (
    CredentialStoreUnavailable,
    active_backend_name,
    delete_token,
    has_token,
    store_token,
)
from .components import (
    Banner,
    FieldError,
    apply_credential_backend,
    fit_to_workspace,
    mark_field_invalid,
)
from .error_presentation import SEVERITY_DANGER
from .token_dialog import TokenDialog

BaseUrlValidator = Callable[[str], "str | None"]

# What the three cards want when nothing constrains them; a PREFERENCE, not a
# floor. `fit_to_workspace` clamps it to the desktop that is actually there.
SETTINGS_PREFERRED_MINIMUM = (360, 560)
SETTINGS_DEFAULT_SIZE = (420, 620)
# The floor. Small enough for a 1366x768 panel at 200% scaling, which reports
# roughly 683x384 logical pixels - the case that made the button box
# unreachable before the scroll area existed.
SETTINGS_ABSOLUTE_MINIMUM = (320, 240)


def _card(title: str, parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
    """A titled surface, and the layout its content goes in.

    QFrame, not QWidget: a bare QWidget draws no background or border from a
    stylesheet. The margins are zeroed because the surface's own padding comes
    from the stylesheet.
    """
    frame = QFrame(parent)
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    caption = QLabel(title, frame)
    caption.setObjectName("cardTitle")
    layout.addWidget(caption)
    return frame, layout


class SettingsDialog(QDialog):
    """Edit the client's settings, or refuse the edit without closing.

    ``validator`` lets the caller add a second, non-local check to the
    address - the window passes the one the live transport applies - so that a
    refusal the client would make anyway is made HERE, against the field the
    owner is looking at, instead of after the dialog has closed on a value it
    will never use.
    """

    def __init__(
        self,
        config: ClientConfig,
        parent=None,
        validator: BaseUrlValidator | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.SETTINGS_TITLE)
        self._config = config
        self._validator = validator

        title = QLabel(strings.SETTINGS_TITLE, self)
        title.setObjectName("pageTitle")

        # -- connection ----------------------------------------------------

        connection_card, connection_layout = _card(
            strings.SETTINGS_CONNECTION_SECTION_TITLE, self
        )

        base_url_label = QLabel(strings.SETTINGS_BASE_URL_LABEL, connection_card)

        # Empty when the client is unconfigured: the field shows what is set,
        # and there is nothing set. The placeholder carries the example.
        self.base_url_edit = QLineEdit(config.base_url or "", connection_card)
        self.base_url_edit.setPlaceholderText(strings.SETTINGS_BASE_URL_PLACEHOLDER)
        self.base_url_edit.textChanged.connect(self._on_base_url_changed)
        # main 用的是 QFormLayout.addRow(文字, 控件),Qt 在那里**自动**把标签设为
        # 控件的 buddy。改成卡片 + 手排布局之后这层关联没了,标签只是碰巧摆在
        # 旁边——辅助技术读到的是一个无名输入框,而占位符在输入内容之后就消失了。
        base_url_label.setBuddy(self.base_url_edit)

        self.base_url_error = FieldError(connection_card)

        self.timeout_spin = QDoubleSpinBox(connection_card)
        self.timeout_spin.setRange(1.0, 600.0)
        self.timeout_spin.setValue(config.request_timeout_seconds)

        timeout_row = QHBoxLayout()
        timeout_row.setContentsMargins(0, 0, 0, 0)
        timeout_row.setSpacing(8)
        timeout_label = QLabel(strings.SETTINGS_TIMEOUT_LABEL, connection_card)
        timeout_label.setBuddy(self.timeout_spin)
        timeout_row.addWidget(timeout_label)
        timeout_row.addStretch(1)
        timeout_row.addWidget(self.timeout_spin)

        connection_layout.addWidget(base_url_label)
        connection_layout.addWidget(self.base_url_edit)
        connection_layout.addWidget(self.base_url_error)
        connection_layout.addLayout(timeout_row)

        # -- device credential ---------------------------------------------

        credential_card, credential_layout = _card(
            strings.SETTINGS_CREDENTIAL_SECTION_TITLE, self
        )

        self.backend_label = QLabel(credential_card)
        apply_credential_backend(self.backend_label, active_backend_name())

        backend_row = QHBoxLayout()
        backend_row.setContentsMargins(0, 0, 0, 0)
        backend_row.setSpacing(8)
        backend_name_label = QLabel(
            strings.STATUS_KEYRING_BACKEND_LABEL, credential_card
        )
        backend_name_label.setBuddy(self.backend_label)
        backend_row.addWidget(backend_name_label)
        backend_row.addStretch(1)
        backend_row.addWidget(self.backend_label)

        self.credential_status_label = QLabel(credential_card)

        self.enter_token_button = QPushButton(credential_card)
        self.enter_token_button.setObjectName("secondaryButton")
        self.enter_token_button.clicked.connect(self._on_enter_token_clicked)
        self.forget_token_button = QPushButton(
            strings.SETTINGS_FORGET_TOKEN_BUTTON, credential_card
        )
        self.forget_token_button.setObjectName("secondaryButton")
        self.forget_token_button.clicked.connect(self._on_forget_token_clicked)

        # The credential store can be unavailable for a moment, and the answer
        # to that is to try again - which needs this dialog still open. A modal
        # error box whose only button dismissed it took that away.
        self.credential_banner = Banner(credential_card)

        credential_row = QHBoxLayout()
        credential_row.setContentsMargins(0, 0, 0, 0)
        credential_row.setSpacing(8)
        credential_row.addWidget(self.enter_token_button)
        credential_row.addStretch(1)
        credential_row.addWidget(self.forget_token_button)

        credential_layout.addLayout(backend_row)
        credential_layout.addWidget(self.credential_status_label)
        credential_layout.addWidget(self.credential_banner)
        credential_layout.addLayout(credential_row)

        # -- appearance ------------------------------------------------------

        appearance_card, appearance_layout = _card(
            strings.SETTINGS_APPEARANCE_SECTION_TITLE, self
        )

        self.theme_system_radio = QRadioButton(
            strings.THEME_OPTION_SYSTEM, appearance_card
        )
        self.theme_light_radio = QRadioButton(
            strings.THEME_OPTION_LIGHT, appearance_card
        )
        self.theme_dark_radio = QRadioButton(strings.THEME_OPTION_DARK, appearance_card)

        self._appearance_buttons = {
            APPEARANCE_SYSTEM: self.theme_system_radio,
            APPEARANCE_LIGHT: self.theme_light_radio,
            APPEARANCE_DARK: self.theme_dark_radio,
        }
        # Explicit group rather than relying on a shared parent: the three are
        # laid out in a row inside a card that also holds a label, and
        # auto-exclusivity by parentage is a property of the layout rather
        # than of the choice being one choice.
        self._appearance_group = QButtonGroup(self)
        for value, button in self._appearance_buttons.items():
            self._appearance_group.addButton(button)
            button.toggled.connect(
                lambda checked, chosen=value: self._on_appearance_toggled(
                    checked, chosen
                )
            )

        self._initial_appearance = (
            config.appearance
            if config.appearance in APPEARANCE_VALUES
            else DEFAULT_APPEARANCE
        )
        # Signals blocked while the initial state is set. `setChecked` emits
        # `toggled`, and the handler re-renders the WHOLE application's
        # styling - so merely constructing this dialog used to reach out and
        # restyle the window behind it. With config and the running theme in
        # agreement that is an invisible no-op, which is exactly why it
        # survived: it is only visible when they disagree, and then it snaps
        # the application back without anybody choosing anything. Opening a
        # settings dialog is not a decision to change settings.
        initial_button = self._appearance_buttons[self._initial_appearance]
        was_blocked = initial_button.blockSignals(True)
        initial_button.setChecked(True)
        initial_button.blockSignals(was_blocked)

        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.setSpacing(16)
        theme_row.addWidget(QLabel(strings.SETTINGS_THEME_LABEL, appearance_card))
        theme_row.addWidget(self.theme_system_radio)
        theme_row.addWidget(self.theme_light_radio)
        theme_row.addWidget(self.theme_dark_radio)
        theme_row.addStretch(1)
        appearance_layout.addLayout(theme_row)

        # -- frame ------------------------------------------------------------

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        # Qt labels its own standard buttons, in English, from a translation
        # this application does not install - so the two most-clicked buttons
        # in the window were the only English left on screen. Relabelled from
        # strings.py like everything else the owner reads.
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            strings.DIALOG_OK_BUTTON
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            strings.DIALOG_CANCEL_BUTTON
        )
        buttons.accepted.connect(self._on_accepted)
        buttons.rejected.connect(self.reject)
        # Cancel has to undo the live preview as well as the edits, or a
        # dialog that changed nothing would leave the window a different
        # colour than it found it.
        self.rejected.connect(self._restore_initial_appearance)

        # The three cards go inside a scroll area and the button box stays
        # OUTSIDE it. Measured: this stack wants 584 logical pixels of height,
        # and a 1366x768 panel at 200% scaling offers about 384 - so on that
        # desktop the dialog opened taller than the screen and Ok/Cancel sat
        # below its bottom edge, unreachable. Scrolling the cards is what makes
        # the content reachable; keeping the buttons out of the scroll area is
        # what makes the DECISION always reachable, which matters more.
        scroll_body = QWidget(self)
        body_layout = QVBoxLayout(scroll_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        body_layout.addWidget(title)
        body_layout.addWidget(connection_card)
        body_layout.addWidget(credential_card)
        body_layout.addWidget(appearance_card)
        body_layout.addStretch(1)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("settingsScroll")
        self.scroll_area.setWidget(scroll_body)
        # Without this the inner widget keeps its sizeHint and the scroll area
        # shows a horizontal bar instead of letting the cards use the width.
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(buttons)

        # Same clamp the main window uses, from the same function. The dialog
        # is the reason that function is shared rather than a method: it had no
        # sizing logic of its own, so it inherited whatever its content asked
        # for, however tall that was.
        fit_to_workspace(
            self,
            SETTINGS_PREFERRED_MINIMUM,
            SETTINGS_DEFAULT_SIZE,
            SETTINGS_ABSOLUTE_MINIMUM,
        )

        self._refresh_credential_status()

    # -- public API --------------------------------------------------------

    def show_base_url_error(self, text: str) -> None:
        """Refuse the address on the field itself, with the dialog still open.

        Public because the window makes the same refusal from the other side:
        the live transport rejects an address that would carry the device
        token in the clear, and that answer has to land on the field that
        holds it rather than in a box over a dialog that has already closed.
        """
        self.base_url_error.show_error(text)
        mark_field_invalid(self.base_url_edit, True)
        self.base_url_edit.setFocus()

    def result_config(self) -> ClientConfig:
        """The edited settings.

        An empty field is `None` - unconfigured - and not a development
        address substituted for one. `_on_accepted` refuses an empty field
        before this is reached, so the branch is a floor rather than a path;
        what it must never do is invent an address nobody typed.
        """
        base_url = self.base_url_edit.text().strip() or None
        return ClientConfig(
            base_url=base_url,
            request_timeout_seconds=self.timeout_spin.value(),
            translate_timeout_seconds=self._config.translate_timeout_seconds,
            appearance=self.selected_appearance(),
        )

    def selected_appearance(self) -> str:
        for value, button in self._appearance_buttons.items():
            if button.isChecked():
                return value
        return DEFAULT_APPEARANCE

    # -- internals ---------------------------------------------------------

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

    def _on_base_url_changed(self) -> None:
        # The complaint is about the text that was there when OK was pressed.
        # Leaving it up while the owner edits marks a value nobody has judged.
        self._clear_base_url_error()

    def _clear_base_url_error(self) -> None:
        self.base_url_error.clear()
        mark_field_invalid(self.base_url_edit, False)

    def _on_appearance_toggled(self, checked: bool, chosen: str) -> None:
        """Re-render immediately, so the choice is judged by looking at it.

        `apply_theme` never raises and needs no application instance to be
        safe, which is what lets this run from a dialog constructed in a test
        with no styling in effect.
        """
        if not checked:
            return
        theme.apply_theme(QApplication.instance(), chosen)

    def _restore_initial_appearance(self) -> None:
        theme.apply_theme(QApplication.instance(), self._initial_appearance)

    def _on_enter_token_clicked(self) -> None:
        dialog = TokenDialog(self)
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            token = dialog.token()
            if not token:
                return
            try:
                store_token(token)
            except CredentialStoreUnavailable:
                # Kept open with the token still in the field. Reporting this
                # on the card behind a closed dialog threw away what had just
                # been pasted, and a temporarily unavailable credential store
                # is exactly the failure where retrying is the right move.
                dialog.show_store_error(strings.CREDENTIAL_STORE_ERROR_MESSAGE)
                continue
            break
        self.credential_banner.clear()
        self._refresh_credential_status()

    def _on_forget_token_clicked(self) -> None:
        # The one modal left in this dialog, deliberately: deleting the
        # credential cannot be undone from here, and a confirmation the owner
        # can dismiss by looking away is not a decision.
        # Built rather than called through the static helper, for one reason:
        # the helper's Yes/No come from Qt's own English defaults, and this is
        # the most consequential question the application asks.
        question = QMessageBox(self)
        question.setWindowTitle(strings.CONFIRM_FORGET_TOKEN_TITLE)
        question.setText(strings.CONFIRM_FORGET_TOKEN_MESSAGE)
        question.setIcon(QMessageBox.Icon.Question)
        question.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        question.button(QMessageBox.StandardButton.Yes).setText(
            strings.DIALOG_YES_BUTTON
        )
        question.button(QMessageBox.StandardButton.No).setText(
            strings.DIALOG_NO_BUTTON
        )
        # The safe answer is the one a stray Return key lands on.
        question.setDefaultButton(QMessageBox.StandardButton.No)
        confirm = question.exec()
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                delete_token()
            except CredentialStoreUnavailable:
                self.credential_banner.show_message(
                    strings.CREDENTIAL_STORE_FORGET_ERROR_MESSAGE, SEVERITY_DANGER
                )
                return
            self.credential_banner.clear()
            self._refresh_credential_status()

    def _on_accepted(self) -> None:
        """A saved address that cannot be used is worse than no change.

        An address like `http://127.0.0.1:notaport` is accepted by a plain
        text field and then fails on every request until the operator works
        out that the setting itself is wrong.

        A refusal here applies NOTHING: no configuration is built, no timeout
        is pushed anywhere, nothing is written to disk. All of that happens in
        the caller, after `accept`, and the only exit that reaches it is the
        last line.
        """
        self._clear_base_url_error()
        candidate = self.base_url_edit.text().strip()
        if not usable_base_url(candidate):
            self.show_base_url_error(strings.SETTINGS_INVALID_BASE_URL_MESSAGE)
            return
        if self._validator is not None:
            message = self._validator(candidate)
            if message:
                self.show_base_url_error(message)
                return
        self.accept()
