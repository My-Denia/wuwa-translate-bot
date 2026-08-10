"""Service/meta status: version, data profile/commit, term count, whether a
translation model is configured, and the active credential-store backend."""

from __future__ import annotations

import asyncio

from PySide6.QtWidgets import QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import strings
from ..api import ApiClient, MetaResult
from ..credentials import active_backend_name, has_token
from ..errors import ClientError, error_status


class StatusView(QWidget):
    def __init__(self, api_client: ApiClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api_client = api_client
        self._task: asyncio.Task | None = None

        self.service_version_value = QLabel(strings.STATUS_UNKNOWN_VALUE, self)
        self.data_profile_value = QLabel(strings.STATUS_UNKNOWN_VALUE, self)
        self.data_commit_value = QLabel(strings.STATUS_UNKNOWN_VALUE, self)
        self.term_count_value = QLabel(strings.STATUS_UNKNOWN_VALUE, self)
        self.model_configured_value = QLabel(strings.STATUS_UNKNOWN_VALUE, self)
        self.keyring_backend_value = QLabel(self)
        self.credential_status_value = QLabel(self)

        self.refresh_button = QPushButton(strings.STATUS_REFRESH_BUTTON, self)
        self.refresh_button.clicked.connect(self._on_refresh_clicked)

        self.status_label = QLabel(self)

        form = QFormLayout()
        form.addRow(strings.STATUS_SERVICE_VERSION_LABEL, self.service_version_value)
        form.addRow(strings.STATUS_DATA_PROFILE_LABEL, self.data_profile_value)
        form.addRow(strings.STATUS_DATA_COMMIT_LABEL, self.data_commit_value)
        form.addRow(strings.STATUS_TERM_COUNT_LABEL, self.term_count_value)
        form.addRow(strings.STATUS_MODEL_CONFIGURED_LABEL, self.model_configured_value)
        form.addRow(strings.STATUS_KEYRING_BACKEND_LABEL, self.keyring_backend_value)
        form.addRow(
            strings.SETTINGS_CREDENTIAL_SECTION_TITLE, self.credential_status_value
        )

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.status_label)

        self._refresh_credential_labels()

    def refresh_credential_state(self) -> None:
        """Public: the credential can change outside this view (first run,
        Settings), and the labels are only read when something asks."""
        self._refresh_credential_labels()

    def _refresh_credential_labels(self) -> None:
        self.keyring_backend_value.setText(active_backend_name())
        stored = has_token()
        credential_status = (
            strings.SETTINGS_TOKEN_STATUS_STORED
            if stored
            else strings.SETTINGS_TOKEN_STATUS_MISSING
        )
        self.credential_status_value.setText(credential_status)

    def _on_refresh_clicked(self) -> None:
        # The button is disabled inside the coroutine, which does not run
        # until the loop gets a turn. Two activations before that start two
        # refreshes whose replies can land in either order.
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.ensure_future(self._run_refresh())

    async def _run_refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.status_label.setText(strings.STATUS_LOADING)
        try:
            meta = await self._api_client.get_meta()
        except ClientError as exc:
            self.status_label.setText(error_status(exc))
        else:
            self._show_meta(meta)
            self.status_label.setText("")
        finally:
            self._refresh_credential_labels()
            self.refresh_button.setEnabled(True)
            self._task = None

    def _show_meta(self, meta: MetaResult) -> None:
        self.service_version_value.setText(meta.service_version)
        profile = meta.source_profile or strings.STATUS_UNKNOWN_VALUE
        commit = meta.source_commit or strings.STATUS_UNKNOWN_VALUE
        model_configured = strings.STATUS_YES if meta.llm_configured else strings.STATUS_NO
        self.data_profile_value.setText(profile)
        self.data_commit_value.setText(commit)
        self.term_count_value.setText(str(meta.term_count))
        self.model_configured_value.setText(model_configured)
