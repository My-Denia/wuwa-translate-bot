"""Searching as the query is typed, and the four things that makes fragile.

Typing into a field that queries a service turns one deliberate press into
one request per character, against a service this client is not allowed to
change. Everything in this file is a gate on that:

* the debounce, so a word costs one request rather than one per letter;
* the four refusals that decide a query is not worth sending at all - empty,
  already asked, no address, not a term;
* the ordering guard, which is the successor to the old "ignore a new query
  while one is running" rule. That rule was replaced, not dropped: a field
  that searches while you type must answer the LAST query, so the newer
  search now cancels the older one. The trap is that `ApiClient._request`
  CONSUMES the cancellation and raises `ClientError(cancelled)` instead, so a
  cancelled task does not end as cancelled - it returns normally, arrives in
  this view with a renderable outcome, and would draw the older query's
  ending over the newer query's loading state. The generation counter is the
  only thing stopping it, and the tests below drive exactly that sequence;

The debounce timer is fired directly rather than waited on. Qt timers need a
Qt event loop, which no test here runs; driving the timeout by hand tests the
decision the timeout makes, and the arming of the timer is asserted
separately.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from wuwaterm_client import strings  # noqa: E402
from wuwaterm_client.api import ApiClient, TermMatch, TermsResult  # noqa: E402
from wuwaterm_client.ui import terms_view as terms_view_module  # noqa: E402
from wuwaterm_client.ui.terms_view import TermsView  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Service:
    """A stand-in for `GET /v1/terms` that records what it was asked.

    `held` names the queries whose reply never arrives, which is how a search
    is made to still be running when the next one starts. A held query that
    is interrupted records itself, so "the older request was really stopped"
    is an assertion rather than an inference from the test not hanging.
    """

    def __init__(self, held: "set[str] | None" = None) -> None:
        self.queries: list[str] = []
        self.interrupted: list[str] = []
        self._held = held or set()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        self.queries.append(query)
        if query in self._held:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.interrupted.append(query)
                raise
            raise AssertionError("a held query must never answer")
        return httpx.Response(
            200,
            json={
                "query": query,
                "matches": [
                    {
                        "zh": "今汐",
                        "en": "Jinhsi",
                        "category": "character",
                        "score": 100.0,
                        "reason": "exact",
                    }
                ],
                "request_id": f"req-{query}",
            },
        )


def _client(handler, base_url: "str | None" = "https://test") -> ApiClient:
    return ApiClient(
        base_url,
        _test_transport=httpx.MockTransport(handler),
        token_provider=lambda: "wtd1.device.secret",
    )


async def _settle(view: TermsView) -> None:
    """Wait out whatever request the view has running, if any."""
    task = view._task
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


def _action_labels(host) -> list[str]:
    return [button.text() for button in host.findChildren(QPushButton)]


def _card_action(card) -> QPushButton:
    buttons = card._action_host.findChildren(QPushButton)
    assert len(buttons) == 1, "the card must offer exactly one action here"
    return buttons[0]


def _held_answer(query: str) -> TermsResult:
    return TermsResult(
        query=query,
        matches=(TermMatch(zh="今汐", en="Jinhsi", category="character", score=100.0, reason="exact"),),
        request_id=f"req-{query}",
    )


# -- the debounce ----------------------------------------------------------


def test_typing_a_word_asks_the_service_once(qapp) -> None:
    """Four characters, one request. Without the debounce this is four."""
    service = _Service()
    view = TermsView(_client(service))

    async def scenario() -> None:
        for text in ("J", "Ji", "Jin", "Jinhsi"):
            view.query_edit.setText(text)
            assert service.queries == [], "no request may be sent while typing"

        # One timer, re-armed rather than stacked: it is single-shot, so the
        # four keystrokes above can only ever produce one timeout.
        assert view._debounce_timer.isSingleShot() is True
        assert view._debounce_timer.interval() == terms_view_module.DEBOUNCE_MILLISECONDS
        assert view._debounce_timer.isActive() is True

        view._on_debounce_elapsed()
        await _settle(view)

    asyncio.run(scenario())

    assert service.queries == ["Jinhsi"]
    assert view.table.rowCount() == 1


# -- the four gates --------------------------------------------------------


def test_an_empty_field_is_not_a_query(qapp) -> None:
    service = _Service()
    view = TermsView(_client(service))

    view.query_edit.setText("Jinhsi")
    assert view._debounce_timer.isActive() is True

    view.query_edit.setText("   ")
    assert view._debounce_timer.isActive() is False, (
        "an emptied field must disarm the timer, not merely refuse later"
    )

    # Not even the deliberate path: there is nothing to ask about.
    view._on_search_clicked()

    assert service.queries == []
    assert view.table.rowCount() == 0
    assert view.empty_card.title_text == strings.EMPTY_TERMS_TITLE


def test_the_same_query_is_not_asked_twice_while_it_is_in_flight(qapp) -> None:
    """The debounce can elapse again on text that has not changed - a stray
    signal, a repeated keystroke that undoes itself. Asking again would
    double the request count for nothing."""
    service = _Service(held={"Jinhsi"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("Jinhsi")
        view._on_debounce_elapsed()
        await asyncio.sleep(0.05)
        assert service.queries == ["Jinhsi"]
        running = view._task

        view._on_debounce_elapsed()

        assert service.queries == ["Jinhsi"]
        assert view._task is running, "the running request must not be replaced"

        view._abandon_in_flight()
        await asyncio.gather(running, return_exceptions=True)

    asyncio.run(scenario())

    assert service.queries == ["Jinhsi"]


def test_a_client_with_no_address_asks_nothing(qapp) -> None:
    """Typing into an area that has nowhere to send anything is not an error
    to report on every keystroke - but it is also not a request."""
    service = _Service()
    view = TermsView(_client(service, base_url=None))

    assert view.search_button.isEnabled() is False
    assert view.search_button.toolTip() == strings.TOOLTIP_NEEDS_ENDPOINT

    view.query_edit.setText("Jinhsi")
    assert view._debounce_timer.isActive() is False

    view._on_debounce_elapsed()
    view._on_search_clicked()

    assert service.queries == []
    # The area says what the area would show. It deliberately does NOT repeat
    # the window's setup checklist, which is on screen directly above it - one
    # heading drawn twice reads as one card drawn twice.
    assert view.empty_card.title_text == strings.EMPTY_TERMS_UNCONFIGURED_TITLE
    assert view.empty_card.title_text != strings.SETUP_STEPS_TITLE


def test_a_sentence_is_not_a_term_lookup(qapp) -> None:
    """An empty table would have said the dictionary has no such term. What
    is true is that this text was never a term."""
    service = _Service()
    view = TermsView(_client(service))

    long_query = "今" * (terms_view_module.MAX_TERM_LENGTH + 1)
    view.query_edit.setText(long_query)
    assert view._debounce_timer.isActive() is False
    view._on_search_clicked()

    assert service.queries == []
    assert view.table.rowCount() == 0
    assert view.empty_card.title_text == strings.TERMS_SENTENCE_HINT_TITLE
    assert view.empty_card.subtitle_text == strings.TERMS_SENTENCE_HINT_SUBTITLE

    # The button restates a SHORTENED query, but what it carries onward is the
    # whole of it: the owner would otherwise get a truncated source text in
    # the translation area.
    carried: list[str] = []
    view.translate_requested.connect(carried.append)
    _card_action(view.empty_card).click()
    assert carried == [long_query]

    # A line break makes it a sentence at any length, and it is read on the
    # raw text - stripping first would let a trailing one through as a term.
    view.query_edit.setText("今汐\n")
    assert view._debounce_timer.isActive() is False
    view._on_search_clicked()

    assert service.queries == []
    assert view.empty_card.title_text == strings.TERMS_SENTENCE_HINT_TITLE


# -- ordering --------------------------------------------------------------


def test_an_older_search_never_writes_over_a_newer_one(qapp) -> None:
    """The ordering invariant, driven the only way it can actually fail.

    Both queries are held, so when the older one comes back - as a
    `ClientError` the API client made out of its own cancellation - the newer
    one is still loading. Everything asserted after that point is the newer
    search's state, untouched.
    """
    service = _Service(held={"old", "new"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("old")
        view._on_debounce_elapsed()
        await asyncio.sleep(0.05)
        older = view._task
        assert older is not None

        view.query_edit.setText("new")
        view._on_debounce_elapsed()
        newer = view._task
        assert newer is not older

        await asyncio.gather(older, return_exceptions=True)

        # The older request has finished and rendered nothing.
        assert view.progress.is_running() is True
        assert view.status_label.text() == strings.TERMS_SEARCHING
        assert view.banner.is_showing() is False
        assert view.field_error.is_showing() is False
        assert view.table.rowCount() == 0
        assert view._request_id is None
        assert view._task is newer, "the older task must not clear the newer one"

        view._abandon_in_flight()
        await asyncio.gather(newer, return_exceptions=True)

    asyncio.run(scenario())

    assert service.queries == ["old", "new"]
    assert service.interrupted == ["old", "new"]


def test_every_ending_of_an_older_search_is_dropped(qapp) -> None:
    """The guard has to hold at each exit of the coroutine, not only at the
    one a cancellation happens to take. A success and a failure are driven
    here directly with a generation the view has already moved past."""

    def handler(request):
        query = request.url.params.get("q", "")
        if query == "boom":
            return httpx.Response(
                503,
                json={"error": {"code": "llm_unavailable"}, "request_id": "req-x"},
            )
        return httpx.Response(
            200,
            json={
                "query": query,
                "matches": [
                    {
                        "zh": "今汐",
                        "en": "Jinhsi",
                        "category": "character",
                        "score": 100.0,
                        "reason": "exact",
                    }
                ],
                "request_id": "req-stale",
            },
        )

    view = TermsView(_client(handler))
    view._generation = 5
    stale = 2

    asyncio.run(view._run_search("Jinhsi", stale))
    asyncio.run(view._run_search("boom", stale))

    assert view.table.rowCount() == 0
    assert view.banner.is_showing() is False
    assert view.field_error.is_showing() is False
    assert view._request_id is None
    assert view._task is None
    assert view.status_label.text() == strings.STATUS_BAR_READY
    assert view._generation == 5


def test_a_new_query_replaces_the_one_in_flight(qapp) -> None:
    """The replacement is the point, and it has two halves: the older request
    is really stopped, and the newer answer is really the one drawn."""
    service = _Service(held={"Jin"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("Jin")
        view._on_debounce_elapsed()
        await asyncio.sleep(0.05)
        older = view._task
        generation_before = view._generation

        view.query_edit.setText("Jinhsi")
        view._on_debounce_elapsed()

        # Advanced, not advanced by exactly one: changing the text
        # invalidates the in-flight request immediately (so a reply landing
        # inside the debounce window cannot draw the previous query's rows),
        # and starting the replacement advances it again. What the invariant
        # needs is that the older generation can no longer render, which is
        # "strictly greater", not a step size.
        assert view._generation > generation_before
        assert view._task is not older

        await asyncio.gather(older, return_exceptions=True)
        await _settle(view)

    asyncio.run(scenario())

    assert service.queries == ["Jin", "Jinhsi"]
    assert service.interrupted == ["Jin"]
    assert view.table.rowCount() == 1
    assert view._request_id == "req-Jinhsi"
    assert view.status_label.text() == strings.STATUS_BAR_DONE
    # A request this client stopped by itself is not an event the owner
    # caused, so it is never reported as one.
    assert view.banner.is_showing() is False
    assert view.status_label.text() != strings.STATUS_CANCELLED

def test_typing_invalidates_the_in_flight_request_before_the_debounce(qapp) -> None:
    """输入一变,在飞的那次查询就不再能回答它 —— 不等防抖到期。

    原实现只重排防抖:旧请求在此后最多 220 毫秒内仍是「当代」,它的回复若在这
    个窗口里到达,会通过代号检查,把**上一个查询**的结果与请求 ID 画在新的
    查询词底下。这条断言的是「作废发生在按键那一刻」。
    """
    service = _Service(held={"Jin"})
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("Jin")
        view._on_debounce_elapsed()
        await asyncio.sleep(0.05)
        older = view._task
        generation_before = view._generation

        # 只改文本,不触发防抖到期。
        view.query_edit.setText("Jinhsi")

        assert view._generation > generation_before, (
            "文本已变而在飞请求仍是当代 —— 它的回复会画在新查询词下"
        )
        assert older.cancelled() or older.done() or view._task is not older

        await asyncio.gather(older, return_exceptions=True)

    asyncio.run(scenario())
    assert service.interrupted == ["Jin"]

def test_the_request_id_survives_an_empty_or_failed_lookup(qapp) -> None:
    """每一次完成的请求都要留下可以拿去问运营方的句柄。

    请求 ID 行原本嵌在结果区里,而空结果与失败都会把结果区整块隐藏 —— 于是
    最可能被追问的那一类结局,恰好是唯一看不到 ID 的。
    """
    service = _Service()
    view = TermsView(_client(service))

    result = TermsResult(query="无此词", matches=(), request_id="req-empty")
    view._render_result(result)

    assert view._request_id == "req-empty"
    assert view._request_id_row.isVisibleTo(view) is True, (
        "空结果把请求 ID 行一起藏了"
    )
    assert "req-empty" in view._request_id_label.text()



def test_no_request_id_row_before_any_lookup(qapp) -> None:
    """没发生过请求的屏幕上不该有请求 ID 行。

    与 Banner 的三态语义一致:失败或空结果**要**显示该行(带占位符),因为那是
    最可能被追问的结局;而首次绘制、清空输入、未配置这些从未发出请求的状态,
    没有「缺失的 ID」可言 —— 印一行「请求 ID:—」等于请人去问一次本客户端从未
    发出过的调用。
    """
    view = TermsView(_client(_Service()))

    # 首次绘制:没有请求发生过。
    assert view._request_id_row.isVisibleTo(view) is False

    # 有请求、但没拿到 ID:该行出现,带占位符。
    view._apply_request_id(None)
    assert view._request_id_row.isVisibleTo(view) is True
    assert strings.REQUEST_ID_PLACEHOLDER in view._request_id_label.text()

    # 回到空闲态(输入被清空)——又变成「没有请求」。
    view._show_idle_state()
    assert view._request_id_row.isVisibleTo(view) is False
