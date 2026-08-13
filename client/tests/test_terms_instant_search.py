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
* the held answers, which must be discarded when the address changes - a
  cached hit after a new address would put the previous service's answers
  back on screen without a request, which is the same failure as leaving the
  table populated, one layer down;
* the brake, so a service that says "too fast" is not immediately asked again
  by the next keystroke.

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
from wuwaterm_client.errors import ERROR_OFFLINE, ERROR_RATE_LIMITED  # noqa: E402
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
    assert len(view._cache) == 0, "an outcome nobody may draw is not worth holding"
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


# -- held answers ----------------------------------------------------------


def test_a_held_answer_is_reused_and_a_new_address_throws_it_away(qapp) -> None:
    service = _Service()
    view = TermsView(_client(service))

    async def scenario() -> None:
        view.query_edit.setText("Jinhsi")
        view._on_debounce_elapsed()
        await _settle(view)

        assert service.queries == ["Jinhsi"]
        assert view.table.rowCount() == 1

        # Backspacing into a word already asked about must cost nothing.
        view._on_debounce_elapsed()
        await _settle(view)

        assert service.queries == ["Jinhsi"], "a held answer must not be re-asked"
        assert view.table.rowCount() == 1
        assert view._request_id == "req-Jinhsi"

        # The held answers belong to the service that gave them.
        view.reset_for_endpoint_change()

        assert len(view._cache) == 0
        assert view.table.rowCount() == 0
        assert view._request_id is None
        assert view.empty_card.title_text == strings.ENDPOINT_CHANGED_TITLE

        view._on_debounce_elapsed()
        await _settle(view)

        assert service.queries == ["Jinhsi", "Jinhsi"], (
            "after an address change the same query must be asked again"
        )
        assert view.table.rowCount() == 1

    asyncio.run(scenario())


def test_the_held_answers_have_a_ceiling(qapp) -> None:
    """Held answers are a convenience, not a store: one long session must not
    accumulate every query it ever made."""
    service = _Service()
    view = TermsView(_client(service))

    for index in range(terms_view_module.CACHE_CAPACITY + 1):
        view._cache_put(f"q{index}", _held_answer(f"q{index}"))

    assert len(view._cache) == terms_view_module.CACHE_CAPACITY
    assert "q0" not in view._cache, "the oldest is the one that goes"
    assert f"q{terms_view_module.CACHE_CAPACITY}" in view._cache


# -- the brake -------------------------------------------------------------


def test_being_told_to_slow_down_stops_the_search_from_typing(qapp) -> None:
    """A service that refuses for going too fast must not be asked again by
    the next keystroke. The deliberate attempt survives, because the owner is
    the one who knows whether whatever caused it has been dealt with."""
    asked: list[str] = []

    def handler(request):
        asked.append(request.url.params.get("q", ""))
        return httpx.Response(
            429,
            json={"error": {"code": "rate_limited"}, "request_id": "req-9"},
        )

    view = TermsView(_client(handler))

    async def first_search() -> None:
        view.query_edit.setText("Jinhsi")
        view._on_debounce_elapsed()
        await _settle(view)

    asyncio.run(first_search())

    assert asked == ["Jinhsi"]
    assert view._auto_paused is True
    assert view._backoff_step == 1
    assert view._resume_timer.isActive() is True
    assert view.status_label.text() == strings.BANNER_AUTO_SEARCH_PAUSED
    assert view.banner.is_showing() is True
    assert view.banner.message_text == strings.ERROR_MSG_RATE_LIMITED
    # The id is on the area's own row - the same widget in the same place a
    # successful lookup uses - and deliberately NOT on the banner as well:
    # one failure must not put two ids with two copy buttons on screen.
    assert view._request_id == "req-9"
    assert "req-9" in view._request_id_label.text()
    assert view.banner.request_id is None
    labels = _action_labels(view.banner._actions_host)
    assert strings.ACTION_RESUME_AUTO_SEARCH in labels
    assert strings.ACTION_RETRY in labels

    # Typing no longer arms anything, and firing the timeout by hand still
    # sends nothing.
    view.query_edit.setText("Jinhsi2")
    assert view._debounce_timer.isActive() is False
    view._on_debounce_elapsed()
    assert asked == ["Jinhsi"]

    async def deliberate_retry() -> None:
        view._on_search_clicked()
        await _settle(view)

    asyncio.run(deliberate_retry())

    assert asked == ["Jinhsi", "Jinhsi2"], "the button must still be able to ask"
    assert view._backoff_step == 2, "a second refusal waits longer than the first"

    view._resume_auto_search()

    assert view._auto_paused is False
    assert view._resume_timer.isActive() is False
    assert view.banner.is_showing() is False

    view.query_edit.setText("Jinhsi3")
    assert view._debounce_timer.isActive() is True


# -- Codex P2 回归门(PR #63 评审发现) --------------------------------------


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


def test_a_rate_limit_breaks_the_offline_streak(qapp) -> None:
    """429 是服务端答的,证明它可达,离线连击必须归零。

    否则一次离线 + 一次 429 + 再一次离线会被算成「连续两次离线」,把自动查询
    无限期暂停 —— 中间那次成功的往返被无视了。
    """
    view = TermsView(_client(_Service()))

    view._engage_brake(ERROR_OFFLINE)
    assert view._offline_streak == 1

    view._engage_brake(ERROR_RATE_LIMITED)
    assert view._offline_streak == 0, "限流响应证明服务器可达,连击应被打断"

    paused = view._engage_brake(ERROR_OFFLINE)
    assert view._offline_streak == 1
    assert paused is False, "这只是本轮第一次离线,不应暂停自动查询"


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


def test_a_successful_retry_releases_the_offline_pause(qapp) -> None:
    """离线暂停没有恢复定时器,成功的手动重试是唯一能解开它的东西。

    不解开的话:暂停会活得比断网更久,而承载「恢复自动查询」的提示条在渲染
    结果时已被清掉 —— 于是继续打字什么也不发,屏幕上也没有任何东西说明为什么。
    """
    service = _Service()
    view = TermsView(_client(service))

    view._engage_brake(ERROR_OFFLINE)
    view._engage_brake(ERROR_OFFLINE)
    assert view._auto_paused is True

    view._render_result(_held_answer("Jinhsi"))

    assert view._auto_paused is False, "成功的重试之后自动查询仍是暂停的"
    assert view._offline_streak == 0


def test_a_field_level_response_breaks_the_offline_streak(qapp) -> None:
    """invalid_request 一类同样来自 HTTP 响应,证明服务可达。

    它们在 _engage_brake 之前就 return,于是一次离线 + 一次字段级错误 + 再一次
    离线会被当成连续两次离线。
    """
    from wuwaterm_client.errors import ERROR_INPUT_TOO_LONG, ClientError

    view = TermsView(_client(_Service()))

    view._engage_brake(ERROR_OFFLINE)
    assert view._offline_streak == 1

    view._render_error(ClientError(ERROR_INPUT_TOO_LONG))

    assert view._offline_streak == 0, "字段级响应也证明服务器可达,连击应打断"
    assert view._auto_paused is False
