"""Exact dictionary term lookup: a query field and a results table."""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import strings
from ..api import ApiClient, TermsResult
from ..errors import ClientError, message_for

_COLUMNS = (
    strings.TERMS_COLUMN_ZH,
    strings.TERMS_COLUMN_EN,
    strings.TERMS_COLUMN_CATEGORY,
    strings.TERMS_COLUMN_SCORE,
    strings.TERMS_COLUMN_REASON,
)


class TermsView(QWidget):
    def __init__(self, api_client: ApiClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api_client = api_client
        self._task: asyncio.Task | None = None

        self.query_edit = QLineEdit(self)
        self.query_edit.setPlaceholderText(strings.TERMS_QUERY_PLACEHOLDER)
        self.query_edit.returnPressed.connect(self._on_search_clicked)

        self.search_button = QPushButton(strings.TERMS_SEARCH_BUTTON, self)
        self.search_button.clicked.connect(self._on_search_clicked)

        self.status_label = QLabel(self)

        self.table = QTableWidget(0, len(_COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        query_label = QLabel(strings.TERMS_QUERY_LABEL, self)

        query_row = QHBoxLayout()
        query_row.addWidget(query_label)
        query_row.addWidget(self.query_edit)
        query_row.addWidget(self.search_button)

        layout = QVBoxLayout(self)
        layout.addLayout(query_row)
        layout.addWidget(self.table)
        layout.addWidget(self.status_label)

    def _on_search_clicked(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            return
        self._task = asyncio.ensure_future(self._run_search(query))

    async def _run_search(self, query: str) -> None:
        self.search_button.setEnabled(False)
        self.status_label.setText(strings.STATUS_BAR_SEARCHING)
        try:
            result = await self._api_client.lookup_terms(query)
        except ClientError as exc:
            self.table.setRowCount(0)
            self.status_label.setText(message_for(exc.code))
        else:
            self._show_result(result)
        finally:
            self.search_button.setEnabled(True)
            self._task = None

    def _show_result(self, result: TermsResult) -> None:
        self.table.setRowCount(len(result.matches))
        for row, match in enumerate(result.matches):
            values = (
                match.zh,
                match.en,
                match.category,
                f"{match.score:.2f}",
                match.reason,
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        empty_status = strings.TERMS_EMPTY if not result.matches else ""
        self.status_label.setText(empty_status)
