"""Dictionary term lookup, searched as the query is typed.

Three things in here are not free choices.

The input is a ``QLineEdit`` and not a multi-line editor. A Chinese input
method holds its candidates in the preedit string, which never reaches
``QLineEdit.text()``, so ``textChanged`` fires once per COMMITTED character
rather than once per keystroke of a syllable. That turns "does typing Chinese
send a request per candidate keypress" from a question only a human at a real
machine could answer into one the architecture answers by construction.

Typing replaces requests rather than queueing behind them. The old guard
ignored a new query while one was running, which is the wrong trade for a
field that searches on every character: the answer the owner is waiting for
is always the LAST one. So a new search cancels the previous task - and that
is where the trap is. ``ApiClient._request`` CONSUMES ``CancelledError`` and
raises ``ClientError(ERROR_CANCELLED)`` instead, so a cancelled task does not
end as cancelled; it returns normally, reaches this view, and would overwrite
the newer search's loading state with the older one's outcome. Cancelling is
therefore only half of the mechanism. The other half is ``_generation``: every
search takes the next number, and every exit of the coroutine - success,
``ClientError``, ``CancelledError``, ``finally`` - compares it against the
current one BEFORE touching a widget. Anything from an older generation is
dropped in silence.

And a request this view cancelled by itself is not an event the owner caused,
so the cancellation is never rendered. The Cancel button in the translation
area reports "stopped waiting" because a person asked for it; here it would
only be noise from the machine talking to itself.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
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
from ..errors import (
    ERROR_CANCELLED,
    ERROR_OFFLINE,
    ERROR_RATE_LIMITED,
    ClientError,
)
from .components import (
    Banner,
    EmptyStateCard,
    FieldError,
    KindBadge,
    ProgressLine,
    ScoreBar,
    StatusStrip,
    mark_field_invalid,
)
from .error_presentation import ACTION_RETRY, SURFACE_FIELD, presentation_for

_COLUMNS = (
    strings.TERMS_COLUMN_ZH,
    strings.TERMS_COLUMN_EN,
    strings.TERMS_COLUMN_CATEGORY,
    strings.TERMS_COLUMN_SCORE,
    strings.TERMS_COLUMN_REASON,
)

# 220ms. Below about 150ms a normal typing rhythm still produces a request per
# character; above about 300ms a pure dictionary hit - which the service
# answers in milliseconds - starts to feel like it lagged behind the keyboard.
DEBOUNCE_MILLISECONDS = 220

# Past this many characters, or with a line break anywhere in it, the text is
# a sentence rather than a term. `GET /v1/terms` would answer it with an empty
# table, which reads as "no such term" instead of "wrong tool".
MAX_TERM_LENGTH = 40

# What the service asks for when it says the caller is going too fast, then
# twice that, then a ceiling. Held here rather than computed so the ceiling is
# a value someone chose and not an overflow nobody noticed.
BACKOFF_MILLISECONDS = (2000, 4000, 8000)

# Two refusals in a row are a network that is down, not one request that lost
# a race. Continuing to search on every keystroke against it is pure noise.
OFFLINE_STREAK_LIMIT = 2

# Enough that backspacing through a word never asks twice for the same thing,
# small enough that a session's worth of answers is not held indefinitely.
CACHE_CAPACITY = 32

# Results from the previous query stay on screen while the next one runs, at
# this opacity. Clearing the table on each keystroke makes it blink empty
# between every character, which is harder to read than slightly stale rows.
DIMMED_OPACITY = 0.55

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
    """The term lookup area: one field, one table, no submit step."""

    # Carries the current query to the translation area. A view that reached
    # for the main window instead would import the window that imports it, and
    # the empty-result bridge is the only thing it needs from up there.
    translate_requested = Signal(str)

    def __init__(self, api_client: ApiClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._api_client = api_client
        self._task: asyncio.Task | None = None
        # Monotonic. Read the module docstring before changing anything that
        # touches it: it is the only thing standing between a cancelled task
        # and the newer search it would otherwise overwrite.
        self._generation = 0
        self._last_query_sent: str | None = None
        self._cache: "OrderedDict[str, TermsResult]" = OrderedDict()
        self._request_id: str | None = None
        self._auto_paused = False
        self._backoff_step = 0
        self._offline_streak = 0

        self.banner = Banner(self)

        self.query_edit = QLineEdit(self)
        self.query_edit.setObjectName("searchField")
        self.query_edit.setPlaceholderText(strings.TERMS_QUERY_PLACEHOLDER)
        self.query_edit.textChanged.connect(self._on_query_changed)
        self.query_edit.returnPressed.connect(self._on_search_clicked)

        # Secondary, and never disabled while a request runs. It is the retry
        # entry: it skips the debounce, ignores the duplicate check and
        # ignores the cache, so it is the one control that can always ask the
        # service again. Disabling it during a request would take that away
        # at exactly the moment it is wanted.
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

        request_id_row = QHBoxLayout()
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
        results_layout.addLayout(request_id_row)

        # One effect object for the whole results block, kept disabled while
        # nothing is loading: a disabled QGraphicsEffect paints nothing extra,
        # so this costs only what a search costs.
        self._dim_effect = QGraphicsOpacityEffect(self._results_host)
        self._dim_effect.setOpacity(DIMMED_OPACITY)
        self._dim_effect.setEnabled(False)
        self._results_host.setGraphicsEffect(self._dim_effect)

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
        layout.addWidget(self.status_label)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(DEBOUNCE_MILLISECONDS)
        self._debounce_timer.timeout.connect(self._on_debounce_elapsed)

        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_auto_search)

        self._apply_request_id(None)
        self._apply_endpoint_state()
        self._show_idle_state()

    # -- typing ------------------------------------------------------------

    def _on_query_changed(self, _text: str = "") -> None:
        """Re-arm the debounce, or refuse to arm it at all.

        Every gate that can be decided from the text alone is decided here,
        so a query that will never be sent stops costing a timer as well as a
        request.
        """
        self.field_error.clear()
        mark_field_invalid(self.query_edit, False)

        raw = self.query_edit.text()
        if not raw.strip():
            self._debounce_timer.stop()
            self._abandon_in_flight()
            self._show_idle_state()
            return
        if self._is_a_sentence(raw):
            self._debounce_timer.stop()
            self._abandon_in_flight()
            self._show_sentence_state(raw.strip())
            return
        if not self._api_client.is_configured or self._auto_paused:
            # Gate three, and the brake. Neither is an error to report on a
            # keystroke: the empty card and the banner already say why, and
            # the button is still there for a deliberate attempt.
            self._debounce_timer.stop()
            return
        self._debounce_timer.start()

    def _on_debounce_elapsed(self) -> None:
        self._search(manual=False)

    def _on_search_clicked(self) -> None:
        self._search(manual=True)

    def _search(self, manual: bool) -> None:
        """Decide whether this query is sent, and send it.

        `manual` is the retry path: the search button and Enter. It skips the
        debounce, the duplicate check and the cache, because the reason to
        press it is that the last answer was not one - an error, or one this
        client is holding from before whatever the owner just fixed.
        """
        self._debounce_timer.stop()
        raw = self.query_edit.text()
        query = raw.strip()
        if not query:
            self._abandon_in_flight()
            self._show_idle_state()
            return
        if self._is_a_sentence(raw):
            self._abandon_in_flight()
            self._show_sentence_state(query)
            return
        if not self._api_client.is_configured:
            # The UI is short-circuited here purely to keep the screen quiet;
            # `_request` refuses an unconfigured client on its own, and that
            # refusal - not this line - is what makes the guarantee.
            return
        if not manual:
            if self._auto_paused:
                return
            cached = self._cache_get(query)
            if cached is not None:
                # A held answer still has to displace whatever is running.
                # Backspacing from a query that is in flight into one that is
                # cached would otherwise draw the cached rows and then have
                # them overwritten, seconds later, by the reply to a query
                # that is no longer in the field.
                self._abandon_in_flight()
                self._render_result(cached)
                return
            if query == self._last_query_sent:
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
        self._generation += 1
        generation = self._generation
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._last_query_sent = query
        self._begin_loading()
        self._task = asyncio.ensure_future(self._run_search(query, generation))

    async def _run_search(self, query: str, generation: int) -> None:
        try:
            result = await self._api_client.lookup_terms(query)
        except ClientError as exc:
            if generation != self._generation:
                return
            if exc.code == ERROR_CANCELLED:
                # This client cancelled it, not the owner. `_request` turns
                # the cancellation into this error rather than letting the
                # task end as cancelled, so it arrives here looking like an
                # outcome; rendering it would put "stopped waiting" on screen
                # for a request nobody asked to stop.
                return
            self._end_loading()
            self._render_error(exc)
        except asyncio.CancelledError:
            # Cancelled between awaits, outside the API client's own handler.
            # Same silence, for the same reason.
            return
        else:
            if generation != self._generation:
                return
            self._cache_put(query, result)
            self._end_loading()
            self._render_result(result)
        finally:
            if generation == self._generation:
                self._end_loading()
                self._task = None

    def _abandon_in_flight(self) -> None:
        """Drop whatever is running, and everything it would have written.

        The generation moves first: a task cancelled before its first step
        never runs its own body, and one cancelled mid-await comes back as an
        ordinary error, so neither can be relied on to clean up after itself.
        """
        self._generation += 1
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._last_query_sent = None
        self._end_loading()

    def _begin_loading(self) -> None:
        self.banner.clear()
        self.field_error.clear()
        mark_field_invalid(self.query_edit, False)
        self.progress.start()
        self.status_label.set_text(strings.TERMS_SEARCHING)
        self._dim_effect.setEnabled(self.table.rowCount() > 0)

    def _end_loading(self) -> None:
        self.progress.stop()
        self._dim_effect.setEnabled(False)

    # -- rendering ---------------------------------------------------------

    def _render_result(self, result: TermsResult) -> None:
        self.banner.clear()
        self._offline_streak = 0
        self._backoff_step = 0
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
        # Nothing on screen belongs to this query any more, and the next
        # attempt has to be allowed to repeat it.
        self._last_query_sent = None
        self.status_label.set_text(strings.STATUS_BAR_LAST_REQUEST_FAILED)

        if presentation.surface == SURFACE_FIELD:
            mark_field_invalid(self.query_edit, True)
            self.field_error.show_error(presentation.message)
            self._apply_request_id(exc.request_id)
            return

        paused = self._engage_brake(exc.code)
        actions: list[tuple[str, Callable[[], None]]] = []
        if presentation.action == ACTION_RETRY:
            actions.append((presentation.action_label, self._on_search_clicked))
        if paused:
            actions.append(
                (strings.ACTION_RESUME_AUTO_SEARCH, self._resume_auto_search)
            )
        # Codes whose action is "open settings" or "enter a token" arrive here
        # with no button: this view owns neither dialog, and a button that
        # does nothing is worse than none. Their messages name the place to go.
        # Without the id here: the line below renders it, in the same widget
        # and the same place a successful lookup uses. Two copies of one id,
        # each with its own copy button, read as two different ids.
        self.banner.show_message(
            presentation.message,
            presentation.severity,
            actions=actions,
        )
        self._apply_request_id(exc.request_id)

    # -- the brake ---------------------------------------------------------

    def _engage_brake(self, code: str) -> bool:
        """Stop searching on every keystroke when the answers say to.

        Searching as you type multiplies request volume against a service
        this change is not allowed to modify, so the client has to be the one
        that backs off. Returns whether automatic searching was paused, which
        is what decides if the banner offers to turn it back on.
        """
        if code == ERROR_RATE_LIMITED:
            delay = BACKOFF_MILLISECONDS[
                min(self._backoff_step, len(BACKOFF_MILLISECONDS) - 1)
            ]
            self._backoff_step += 1
            self._pause_auto_search()
            # A timer, not a sleep and not a thread: the wait has to be
            # interruptible by the owner pressing the button, and this view
            # has no business owning anything that outlives the window.
            self._resume_timer.start(delay)
            return True
        if code == ERROR_OFFLINE:
            self._offline_streak += 1
            if self._offline_streak >= OFFLINE_STREAK_LIMIT:
                # No timer here. Nothing about waiting fixes a network that
                # is down, and the owner is the one who will know when it is
                # back.
                self._pause_auto_search()
                return True
            return False
        self._offline_streak = 0
        return False

    def _pause_auto_search(self) -> None:
        self._auto_paused = True
        self._debounce_timer.stop()
        self.status_label.set_text(strings.BANNER_AUTO_SEARCH_PAUSED)

    def _resume_auto_search(self) -> None:
        self._resume_timer.stop()
        if not self._auto_paused:
            return
        self._auto_paused = False
        self._offline_streak = 0
        self.status_label.clear()
        if self.banner.is_showing():
            self.banner.clear()

    # -- states ------------------------------------------------------------

    def _show_idle_state(self, after_endpoint_change: bool = False) -> None:
        """Nothing to show, and why that is not a loss.

        An area that empties itself reads as data disappearing unless it says
        otherwise, so the wording after an address change is not the wording
        at first launch: one of them describes a deliberate discard, the
        other describes a client that has never been asked anything.
        """
        self.table.setRowCount(0)
        self._apply_request_id(None)
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
        self._apply_request_id(None)
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

    def _apply_request_id(self, request_id: str | None) -> None:
        """Always laid out, with or without an id.

        It is the only handle the owner has when asking an operator what
        happened, so it does not move between success and failure - and it
        does not disappear either, which would change the block's height as
        results come and go.
        """
        self._request_id = request_id
        shown = request_id if request_id else strings.REQUEST_ID_PLACEHOLDER
        self._request_id_label.setText(
            strings.REQUEST_ID_LABEL.format(request_id=shown)
        )
        self._request_id_label.setToolTip(shown)
        self._copy_button.setEnabled(request_id is not None)

    def _on_copy_request_id(self) -> None:
        if not self._request_id:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._request_id)
        self.status_label.set_text(strings.STATUS_COPIED)

    # -- the cache ---------------------------------------------------------

    def _cache_get(self, query: str) -> TermsResult | None:
        result = self._cache.get(query)
        if result is not None:
            self._cache.move_to_end(query)
        return result

    def _cache_put(self, query: str, result: TermsResult) -> None:
        self._cache[query] = result
        self._cache.move_to_end(query)
        while len(self._cache) > CACHE_CAPACITY:
            self._cache.popitem(last=False)

    # -- endpoint change ---------------------------------------------------

    def reset_for_endpoint_change(self) -> None:
        """Drop everything that came from the previous server address.

        A term's translation belongs to the dictionary one particular service
        was serving; leaving the table populated after the address changes
        shows one server's answers under another's name. The cache is part of
        that - it is the same answers, one layer down, and a cached hit after
        the address changed would put them back on screen without a request.

        The in-flight task is cancelled, and the generation moves with it, so
        the `cancelled` that `_request` produces from that cancellation is
        discarded rather than rendered over the empty state left here.
        """
        self._abandon_in_flight()
        self._cache.clear()
        self.banner.clear()
        self.field_error.clear()
        mark_field_invalid(self.query_edit, False)
        self._resume_timer.stop()
        self._auto_paused = False
        self._backoff_step = 0
        self._offline_streak = 0
        self._apply_endpoint_state()
        self._show_idle_state(after_endpoint_change=True)
