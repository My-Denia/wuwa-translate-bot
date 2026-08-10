"""Multi-line source input, direction selector, translate/cancel, result."""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import strings
from ..api import ApiClient, TranslationResult
from ..errors import ERROR_CANCELLED, ClientError, error_status

DIRECTION_AUTO = "auto"
DIRECTION_TO_EN = "en"
DIRECTION_TO_ZH = "zh"

_DIRECTION_ITEMS = (
    (DIRECTION_AUTO, strings.DIRECTION_AUTO),
    (DIRECTION_TO_EN, strings.DIRECTION_TO_EN),
    (DIRECTION_TO_ZH, strings.DIRECTION_TO_ZH),
)

_KIND_LABELS = {
    "exact": strings.KIND_LABEL_EXACT,
    "fuzzy": strings.KIND_LABEL_FUZZY,
    "llm": strings.KIND_LABEL_LLM,
    "noop": strings.KIND_LABEL_NOOP,
}


class TranslateView(QWidget):
    def __init__(self, api_client: ApiClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api_client = api_client
        self._task: asyncio.Task | None = None

        self.input_edit = QPlainTextEdit(self)
        self.input_edit.setPlaceholderText(strings.INPUT_PLACEHOLDER)

        self.direction_combo = QComboBox(self)
        for value, label in _DIRECTION_ITEMS:
            self.direction_combo.addItem(label, value)

        self.translate_button = QPushButton(strings.TRANSLATE_BUTTON, self)
        self.translate_button.clicked.connect(self._on_translate_clicked)

        self.cancel_button = QPushButton(strings.CANCEL_BUTTON, self)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.cancel_button.setEnabled(False)

        self.result_edit = QPlainTextEdit(self)
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText(strings.RESULT_PLACEHOLDER)

        self.status_label = QLabel(self)

        input_label = QLabel(strings.INPUT_LABEL, self)
        direction_label = QLabel(strings.DIRECTION_LABEL, self)
        result_label = QLabel(strings.RESULT_LABEL, self)

        direction_row = QHBoxLayout()
        direction_row.addWidget(direction_label)
        direction_row.addWidget(self.direction_combo)
        direction_row.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.translate_button)
        button_row.addWidget(self.cancel_button)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(input_label)
        layout.addWidget(self.input_edit)
        layout.addLayout(direction_row)
        layout.addLayout(button_row)
        layout.addWidget(result_label)
        layout.addWidget(self.result_edit)
        layout.addWidget(self.status_label)

    def _selected_direction(self) -> str | None:
        value = self.direction_combo.currentData()
        if value == DIRECTION_AUTO:
            return None
        return value

    def _on_translate_clicked(self) -> None:
        text = self.input_edit.toPlainText()
        if not text.strip():
            return
        self._set_busy(True)
        self._task = asyncio.ensure_future(
            self._run_translate(text, self._selected_direction())
        )
        # A task cancelled before its first step never runs its own body, so
        # the coroutine's own finally never executes and the buttons would
        # stay in the busy state for good. The callback runs either way.
        self._task.add_done_callback(self._on_task_finished)

    def _on_task_finished(self, task: asyncio.Task) -> None:
        if task.cancelled():
            self._show_error(ClientError(ERROR_CANCELLED))
        self._set_idle()
        self._task = None

    def _on_cancel_clicked(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run_translate(self, text: str, to: str | None) -> None:
        try:
            result = await self._api_client.translate(text, to=to)
        except ClientError as exc:
            self._show_error(exc)
        except asyncio.CancelledError:
            # Cancelling between awaits does not pass through the API
            # client's own handler, so the outcome is rendered here as well.
            # Without this the view would sit at "translating" for good.
            self._show_error(ClientError(ERROR_CANCELLED))
        else:
            self._show_result(result)
        finally:
            self._set_idle()
            self._task = None

    def _set_busy(self, busy: bool) -> None:
        self.translate_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if busy:
            self.status_label.setText(strings.STATUS_BAR_TRANSLATING)

    def _set_idle(self) -> None:
        """Return the buttons to their resting state WITHOUT touching the
        status line: every outcome has just written its own text there, and
        clearing it here is how a rendered error or request id disappears
        before it can be read."""
        self.translate_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _show_result(self, result: TranslationResult) -> None:
        self.result_edit.setPlainText(result.text)
        kind_label = _KIND_LABELS.get(result.kind, result.kind)
        note = strings.DICTIONARY_MISS_NOTE if result.dictionary_miss else ""
        request_line = strings.REQUEST_ID_LABEL.format(request_id=result.request_id)
        parts = [part for part in (kind_label, note, request_line) if part]
        self.status_label.setText(" | ".join(parts))

    def _show_error(self, exc: ClientError) -> None:
        self.result_edit.setPlainText("")
        self.status_label.setText(error_status(exc))
