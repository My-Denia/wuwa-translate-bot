"""Exact dictionary term lookup: a query field and a results table.

A search starts when the owner asks for one - the button or Enter - and at no
other moment. There is no timer in this module and nothing here reacts to
``textChanged``; a lookup that searched while the query was being typed lived
here for part of this branch's history and was withdrawn whole, because the
machinery it needed (a debounce, a monotonic generation to drop replies from
superseded searches, an invalidation path on every keystroke) kept removing
behaviours this file had before it. See the ``instant search`` issue for what
would have to be settled before it comes back.

One request at a time, guarded the way it was before: the button is disabled
while a search runs, but Enter in the field reaches the handler directly, so
the handler itself refuses to start a second task. Without that guard each
press starts another request and overwrites ``_task``, and replies can land
out of order with the table showing an older query's results.

A request cancelled by this view - which happens only when the address
changes - is not an event the owner caused, so its outcome is not rendered.
``ApiClient._request`` CONSUMES ``CancelledError`` and raises
``ClientError(ERROR_CANCELLED)`` instead, so the cancelled task returns
normally and arrives here looking like an outcome; drawing it would put a
failure on top of the empty state the address change just produced.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
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
from ..errors import ERROR_CANCELLED, ClientError
from .components import (
    NO_REQUEST,
    Banner,
    EmptyStateCard,
    FieldError,
    KindBadge,
    ProgressLine,
    ScoreBar,
    StatusStrip,
    mark_field_invalid,
)
from .error_presentation import (
    ACTION_ENTER_TOKEN,
    ACTION_RETRY,
    SURFACE_FIELD,
    presentation_for,
)

_COLUMNS = (
    strings.TERMS_COLUMN_ZH,
    strings.TERMS_COLUMN_EN,
    strings.TERMS_COLUMN_CATEGORY,
    strings.TERMS_COLUMN_SCORE,
    strings.TERMS_COLUMN_REASON,
)

# Past this many characters, or with a line break anywhere in it, the text is
# a sentence rather than a term. `GET /v1/terms` would answer it with an empty
# table, which reads as "no such term" instead of "wrong tool". Checked when
# the search is submitted, so it costs nothing until then.
MAX_TERM_LENGTH = 40

# How much room the bridge button gives the query before shortening it. The
# button restates what the owner typed, and a pasted sentence would otherwise
# stretch the card past the window.
BRIDGE_QUERY_PIXELS = 160

# The service's own vocabulary for WHY a row matched (src/wuwaterm/lookup.py):
# an exact hit, one of four pinyin routes, a similarity match, or a score too
# low to mean anything.
_REASON_LABELS = {
    "exact": strings.REASON_LABEL_EXACT,
    "pinyin": strings.REASON_LABEL_PINYIN_FULL,
    "pinyin-abbrev": strings.REASON_LABEL_PINYIN_INITIALS,
    "pinyin-prefix": strings.REASON_LABEL_PINYIN_PREFIX,
    "pinyin-substring": strings.REASON_LABEL_PINYIN_CONTAINS,
    "fuzzy": strings.REASON_LABEL_FUZZY,
    "low-score": strings.REASON_LABEL_LOW_SCORE,
}

# `KindBadge`'s four values are a SHAPE-AND-COLOR vocabulary, not a claim
# about where a translation came from: filled dot / success, ring / warn,
# rounded square / info, dash / muted. The reason column borrows that
# vocabulary so a match reason and a translation source are read the same way
# - which is why "fuzzy" maps onto the info shape rather than onto the badge
# value that happens to share its name. A reason this release has never seen
# takes the neutral dash and keeps the service's own word for it.
_REASON_BADGE_KINDS = {
    "exact": "exact",
    "pinyin": "fuzzy",
    "pinyin-abbrev": "fuzzy",
    "pinyin-prefix": "fuzzy",
    "pinyin-substring": "fuzzy",
    "fuzzy": "llm",
    "low-score": "noop",
}
_FALLBACK_REASON_KIND = "noop"


# The service's category values, from CATEGORY_ORDER in the server's
# constants.py and the CategorySpec list that builds the database. The mapping
# lives here rather than in strings.py for the same reason _KIND_LABELS and
# _REASON_LABELS do: that module holds the words, the view holds which service
# value earns which word.
_CATEGORY_LABELS = {
    "core_term": strings.CATEGORY_LABEL_CORE_TERM,
    "resonator": strings.CATEGORY_LABEL_RESONATOR,
    "weapon": strings.CATEGORY_LABEL_WEAPON,
    "echo": strings.CATEGORY_LABEL_ECHO,
    "skill": strings.CATEGORY_LABEL_SKILL,
    "sonata_effect": strings.CATEGORY_LABEL_SONATA_EFFECT,
    "location": strings.CATEGORY_LABEL_LOCATION,
    "item": strings.CATEGORY_LABEL_ITEM,
    "speaker": strings.CATEGORY_LABEL_SPEAKER,
}


def _category_label(category: str) -> str:
    """The service's category, in the interface's language.

    Falls back to the service's own value, deliberately. This client does not
    own the category list - the service builds it, and it can gain an entry
    without asking - so an unknown one has to degrade to something readable
    rather than to a blank cell or a placeholder that says nothing. An English
    word in a Chinese column is a visible, reportable defect; an empty cell is
    a term that looks uncategorised.
    """
    return _CATEGORY_LABELS.get(category, category)


class TermsView(QWidget):
    """The term lookup area: one field, one button, one table."""

    # Carries the current query to the translation area. A view that reached
    # for the main window instead would import the window that imports it, and
    # the empty-result bridge is the only thing it needs from up there.
    translate_requested = Signal(str)

    def __init__(
        self,
        api_client: ApiClient,
        parent: QWidget | None = None,
        *,
        on_enter_token: "object | None" = None,
    ) -> None:
        super().__init__(parent)
        # The credential dialog belongs to the window, not to an area. Without
        # a way to reach it, this view had to drop the "enter a new token"
        # action the dispatch table assigns to `unauthorized` and `forbidden`
        # - so the table classified an action that nothing could offer.
        self._on_enter_token = on_enter_token
        self._api_client = api_client
        self._task: asyncio.Task | None = None
        self._request_id: str | None = None

        self.banner = Banner(self)

        self.query_edit = QLineEdit(self)
        self.query_edit.setObjectName("searchField")
        self.query_edit.setPlaceholderText(strings.TERMS_QUERY_PLACEHOLDER)
        self.query_edit.returnPressed.connect(self._on_search_clicked)

        # Disabled while a search runs and while the client has no address.
        # Both are the same statement - a press right now cannot produce an
        # answer - so both go through `_apply_endpoint_state`, which is what
        # the request path calls when it finishes rather than re-enabling the
        # button unconditionally and undoing the unconfigured state.
        self.search_button = QPushButton(strings.TERMS_SEARCH_BUTTON, self)
        self.search_button.setObjectName("secondaryButton")
        self.search_button.clicked.connect(self._on_search_clicked)

        self.field_error = FieldError(self)
        self.progress = ProgressLine(self)
        self.status_label = StatusStrip(self)

        self.empty_card = EmptyStateCard(self)

        self.table = QTableWidget(0, len(_COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # One line per term. With wrapping left on, `resizeRowsToContents`
        # below measures the longest cell against whatever width the column
        # happens to have at that moment and gives that ONE row two or three
        # lines of height - measured here as a 117px first row beside 45px
        # neighbours, which reads as the top match being emphasised when it is
        # only longer. A dictionary table is scanned down a column, so equal
        # rows are the point; text too long for its column elides.
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)

        self._request_id_label = QLabel(self)
        self._request_id_label.setObjectName("monoLabel")
        self._request_id_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._copy_button = QPushButton(strings.REQUEST_ID_COPY_BUTTON, self)
        self._copy_button.setObjectName("linkButton")
        self._copy_button.clicked.connect(self._on_copy_request_id)

        # A widget of its own, and deliberately NOT inside the results host.
        # It used to live in there beside the table, and `_show_empty_card`
        # hides that whole host - so a lookup that failed, or found nothing,
        # stored the id and then hid it. The one outcome an operator is most
        # likely to be asked about was the one with no visible handle, which
        # is the opposite of what this row is for.
        self._request_id_row = QWidget(self)
        request_id_row = QHBoxLayout(self._request_id_row)
        request_id_row.setContentsMargins(0, 0, 0, 0)
        request_id_row.setSpacing(8)
        request_id_row.addWidget(self._request_id_label)
        request_id_row.addWidget(self._copy_button)
        request_id_row.addStretch(1)

        self._results_host = QFrame(self)
        self._results_host.setObjectName("card")
        results_layout = QVBoxLayout(self._results_host)
        results_layout.setSpacing(8)
        results_layout.addWidget(self.table)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_row.addWidget(self.query_edit, 1)
        search_row.addWidget(self.search_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self.banner)
        layout.addLayout(search_row)
        layout.addWidget(self.field_error)
        layout.addWidget(self.progress)
        layout.addWidget(self.empty_card)
        layout.addWidget(self._results_host, 1)
        layout.addWidget(self._request_id_row)
        layout.addWidget(self.status_label)

        self._apply_request_id()
        self._apply_endpoint_state()
        self._show_idle_state()

    # -- submitting --------------------------------------------------------

    def _on_search_clicked(self) -> None:
        """The only path that starts a lookup: the button, or Enter.

        The in-flight guard is first and is not an optimisation. The button
        is disabled for the duration of a search, but Enter in the query
        field calls this directly, so without the guard each press starts
        another request and overwrites `_task` - and two replies can then
        land out of order, leaving the table showing the older query.
        """
        if self._task is not None and not self._task.done():
            return
        raw = self.query_edit.text()
        query = raw.strip()
        if not query:
            return
        if self._is_a_sentence(raw):
            self._show_sentence_state(query)
            return
        if not self._api_client.is_configured:
            # The UI is short-circuited here purely to keep the screen quiet;
            # `_request` refuses an unconfigured client on its own, and that
            # refusal - not this line - is what makes the guarantee.
            return
        self._start_search(query)

    @staticmethod
    def _is_a_sentence(raw: str) -> bool:
        """Whether this text belongs in the translation area instead.

        The line break is tested on the RAW text: stripping would remove a
        trailing one and let a two-line paste through as a single term.
        """
        if "\n" in raw or "\r" in raw:
            return True
        return len(raw.strip()) > MAX_TERM_LENGTH

    # -- the request -------------------------------------------------------

    def _start_search(self, query: str) -> None:
        self._begin_loading()
        self._task = asyncio.ensure_future(self._run_search(query))

    async def _run_search(self, query: str) -> None:
        try:
            result = await self._api_client.lookup_terms(query)
        except ClientError as exc:
            if exc.code == ERROR_CANCELLED:
                # Only `reset_for_endpoint_change` cancels, and it has already
                # put the screen where it wants it. `_request` turns the
                # cancellation into this error rather than letting the task
                # end as cancelled, so it arrives here looking like an
                # outcome; rendering it would draw a failure over that state.
                return
            self._render_error(exc)
        except asyncio.CancelledError:
            # Cancelled between awaits, outside the API client's own handler.
            # Same silence, for the same reason.
            return
        else:
            self._render_result(result)
        finally:
            self._end_loading()
            self._task = None

    def _begin_loading(self) -> None:
        self.banner.clear()
        self.field_error.clear()
        mark_field_invalid(self.query_edit, False)
        self.search_button.setEnabled(False)
        self.progress.start()
        self.status_label.set_text(strings.TERMS_SEARCHING)

    def _end_loading(self) -> None:
        self.progress.stop()
        # Not `setEnabled(True)`: the button is also the unconfigured state's
        # to disable, and a search that finished must not hand it back when
        # there is still no address to send the next one to.
        self._apply_endpoint_state()

    # -- rendering ---------------------------------------------------------

    def _render_result(self, result: TermsResult) -> None:
        self.banner.clear()
        self.table.setRowCount(len(result.matches))
        for row, match in enumerate(result.matches):
            for column, value in enumerate(
                (match.zh, match.en, _category_label(match.category))
            ):
                item = QTableWidgetItem(value)
                # Elided text is unreadable text; the whole value stays one
                # hover away rather than only in the column that fits it.
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, 3, self._score_cell(match.score))
            self.table.setCellWidget(row, 4, self._reason_cell(match.reason))
        self.table.resizeRowsToContents()
        self._apply_request_id(result.request_id)
        if result.matches:
            self._show_results()
            self.status_label.set_text(strings.STATUS_BAR_DONE)
        else:
            # An empty table and a table nobody has filled in yet look the
            # same, so the empty answer gets a card of its own - and the card
            # carries the query onward instead of making the owner retype it
            # in the other area.
            self.empty_card.set_content(
                strings.EMPTY_TERMS_NO_MATCH_TITLE,
                strings.EMPTY_TERMS_NO_MATCH_SUBTITLE,
                self._bridge_action(result.query),
            )
            self._show_empty_card()
            self.status_label.set_text(strings.TERMS_EMPTY)

    def _score_cell(self, score: float) -> QWidget:
        """The number and the same number as a length, side by side.

        The bar is the second encoding: a column of numbers has to be read
        one row at a time, and the point of a score is comparing rows.
        """
        cell = QWidget(self.table)
        label = QLabel(cell)
        label.setText(f"{score:.2f}")
        bar = ScoreBar(cell)
        bar.set_score(score)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        layout.addWidget(label)
        layout.addWidget(bar)
        layout.addStretch(1)
        return cell

    def _reason_cell(self, reason: str) -> QWidget:
        cell = QWidget(self.table)
        badge = KindBadge(cell)
        badge.set_kind(
            _REASON_BADGE_KINDS.get(reason, _FALLBACK_REASON_KIND),
            # An unrecognised reason keeps the service's own word rather than
            # being mapped onto one of ours that it may not mean.
            _REASON_LABELS.get(reason, reason),
        )
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)
        layout.addWidget(badge)
        layout.addStretch(1)
        return cell

    def _render_error(self, exc: ClientError) -> None:
        """Put a failed search where its own error code says it belongs."""
        presentation = presentation_for(exc.code)
        # The rows on screen answered the PREVIOUS query and this one has no
        # answer at all, so they are taken down before anything else is drawn.
        # Leaving them up under a failure banner lets them be read as this
        # query's results - the behaviour this file had before the redesign,
        # and the one it keeps.
        self._clear_matches()
        self.status_label.set_text(strings.STATUS_BAR_LAST_REQUEST_FAILED)

        if presentation.surface == SURFACE_FIELD:
            mark_field_invalid(self.query_edit, True)
            self.field_error.show_error(presentation.message)
            self._apply_request_id(exc.request_id)
            return

        actions: list[tuple[str, Callable[[], None]]] = []
        if presentation.action == ACTION_RETRY:
            actions.append((presentation.action_label, self._on_search_clicked))
        if (
            presentation.action == ACTION_ENTER_TOKEN
            and self._on_enter_token is not None
        ):
            actions.append((presentation.action_label, self._on_enter_token))
        # A code whose action is "open settings" still arrives with no button:
        # that dialog belongs to the window and this view has no handle on it.
        # An unwired action is left out rather than drawn, because a button
        # that does nothing costs a press to discover.
        # Without the id here: the line below renders it, in the same widget
        # and the same place a successful lookup uses. Two copies of one id,
        # each with its own copy button, read as two different ids.
        self.banner.show_message(
            presentation.message,
            presentation.severity,
            actions=actions,
        )
        self._apply_request_id(exc.request_id)

    # -- states ------------------------------------------------------------

    def _clear_matches(self) -> None:
        """Empty the table and put the card back in front of it.

        The rows and the results host are one statement: an empty table left
        visible reads as "the dictionary answered, with nothing", which is a
        different claim from "there is no answer on this screen".
        """
        self.table.setRowCount(0)
        # Not the first-launch wording: the owner HAS typed a term and asked
        # for it. "Type any term to start" under a failure banner reads as
        # though the attempt never happened.
        self.empty_card.set_content(
            strings.EMPTY_TERMS_FAILED_TITLE, strings.EMPTY_TERMS_FAILED_SUBTITLE
        )
        self._show_empty_card()

    def _show_idle_state(self, after_endpoint_change: bool = False) -> None:
        """Nothing to show, and why that is not a loss.

        An area that empties itself reads as data disappearing unless it says
        otherwise, so the wording after an address change is not the wording
        at first launch: one of them describes a deliberate discard, the
        other describes a client that has never been asked anything.
        """
        self.table.setRowCount(0)
        self._apply_request_id()
        if not self._api_client.is_configured:
            # Not the checklist's heading: the window is already showing that
            # card directly above this one, and two cards with one title read
            # as the same card drawn twice.
            self.empty_card.set_content(
                strings.EMPTY_TERMS_UNCONFIGURED_TITLE,
                strings.EMPTY_UNCONFIGURED_SUBTITLE,
            )
            self.status_label.set_text(strings.STATUS_BAR_NOT_CONFIGURED)
        elif after_endpoint_change:
            self.empty_card.set_content(
                strings.ENDPOINT_CHANGED_TITLE, strings.ENDPOINT_CHANGED_SUBTITLE
            )
            self.status_label.set_text(strings.STATUS_BAR_READY)
        else:
            self.empty_card.set_content(
                strings.EMPTY_TERMS_TITLE, strings.EMPTY_TERMS_SUBTITLE
            )
            self.status_label.set_text(strings.STATUS_BAR_READY)
        self._show_empty_card()

    def _show_sentence_state(self, query: str) -> None:
        """The fourth gate, on screen.

        An empty table would have said the dictionary has no such term. What
        is true is that this text was never a term lookup, so the card says
        that and hands the text to the area that can do something with it.
        """
        self.table.setRowCount(0)
        self._apply_request_id()
        self.empty_card.set_content(
            strings.TERMS_SENTENCE_HINT_TITLE,
            strings.TERMS_SENTENCE_HINT_SUBTITLE,
            self._bridge_action(query),
        )
        self._show_empty_card()
        self.status_label.clear()

    def _bridge_action(self, query: str) -> tuple[str, object]:
        """The button that carries this query into the translation area."""
        shortened = self.fontMetrics().elidedText(
            query, Qt.TextElideMode.ElideRight, BRIDGE_QUERY_PIXELS
        )
        label = strings.TERMS_TRANSLATE_BRIDGE_BUTTON.format(query=shortened)
        return (label, lambda: self.translate_requested.emit(query))

    def _show_empty_card(self) -> None:
        self.empty_card.setVisible(True)
        self._results_host.setVisible(False)

    def _show_results(self) -> None:
        self.empty_card.setVisible(False)
        self._results_host.setVisible(True)

    def _apply_endpoint_state(self) -> None:
        configured = self._api_client.is_configured
        self.search_button.setEnabled(configured)
        self.search_button.setToolTip(
            "" if configured else strings.TOOLTIP_NEEDS_ENDPOINT
        )

    def _apply_request_id(self, request_id: str | None = NO_REQUEST) -> None:
        """The id row, for every request outcome and for nothing else.

        Three states, not two - the same distinction the banner makes, and
        for the same reason. A lookup that FAILED or found nothing still gets
        the row, with a placeholder when it carried no id: it does not move
        between success and failure, because it is the only handle the owner
        has when asking an operator what happened, and a row that appears for
        one outcome and not another is a row nobody learns to look at.

        But a screen where no lookup has happened - first paint, an emptied
        field, a client with no address - has no id to be missing. Printing
        "request ID: -" there invites the owner to go asking about a call this
        client never made.
        """
        applicable = request_id is not NO_REQUEST
        self._request_id = request_id if applicable else None
        self._request_id_row.setVisible(applicable)
        if not applicable:
            return
        shown = self._request_id if self._request_id else strings.REQUEST_ID_PLACEHOLDER
        self._request_id_label.setText(
            strings.REQUEST_ID_LABEL.format(request_id=shown)
        )
        self._request_id_label.setToolTip(shown)
        self._copy_button.setEnabled(self._request_id is not None)

    def _on_copy_request_id(self) -> None:
        if not self._request_id:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._request_id)
        self.status_label.set_text(strings.STATUS_COPIED)

    # -- endpoint change ---------------------------------------------------

    def focus_input(self) -> None:
        """Where Ctrl+K puts the caret in this area.

        Published as a method because the window must not know which widget
        each area calls its input - and because a window that guesses would
        focus the page container instead, which looks like the shortcut did
        nothing at all.
        """
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def reset_for_endpoint_change(self) -> None:
        """Drop everything that came from the previous server address.

        A term's translation belongs to the dictionary one particular service
        was serving; leaving the table populated after the address changes
        shows one server's answers under another's name.

        The in-flight task is cancelled. The `cancelled` that `_request`
        produces from that cancellation is dropped by `_run_search` rather
        than rendered over the empty state left here.
        """
        # `_task` is deliberately NOT cleared here. A cancelled task is not
        # done until it has unwound, and its own `finally` is what releases
        # the slot. Clearing it now would let a submit in that sliver start a
        # second request, and the older task's `finally` would then take down
        # the newer one's loading state - the ordering bug this module no
        # longer has any machinery to defend against. Leaving it set means the
        # in-flight guard refuses that submit, which is what main did too.
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.progress.stop()
        self.banner.clear()
        self.field_error.clear()
        mark_field_invalid(self.query_edit, False)
        self._apply_endpoint_state()
        self._show_idle_state(after_endpoint_change=True)
