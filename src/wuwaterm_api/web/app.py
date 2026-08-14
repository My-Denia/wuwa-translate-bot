"""The owner-private web presentation layer, mounted inside the API process.

WHY IN THIS PROCESS (the full argument lives in docs/adr/0014): ADR 0009
accounts the LLM budget per process. A third process would carry a third
independent budget, so the aggregate ceiling across the deployment would rise
even though no single limit changed - which is the opposite of "add no new
amplification surface". Sharing the process also removes the need for a
front-end backend that holds a device token and forwards it: the browser
session is resolved to an already-authenticated principal inside the same
process, so ADR 0010's device-principal semantics are untouched.

WHAT SHARING COSTS, stated plainly because a reader deserves it: a defect here
can take down the process the desktop client depends on. Three mitigations - a
sub-application mount rather than routes merged into the API's own router, a
startup switch that is OFF by default, and a private protection layer that
sits in front at the edge rather than inside this code.

HOW THE SHARED INSTANCES ARE SHARED, which is the subtle part: this
sub-application does not receive copies of the rate limiter and the call
budget. It is given the parent's ``State`` OBJECT ITSELF. Copying references at
construction time would have looked equivalent and been wrong, because the
credential pool is REPLACED on every lifespan cycle - a copy taken at
construction would point at a dead executor after the first restart of the ASGI
lifespan. Sharing the object means there is nothing to keep in step, and it
makes the property testable by identity rather than by inspection.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from wuwaterm.application import (
    ERROR_FORBIDDEN,
    ERROR_INTERNAL,
    ERROR_INVALID_REQUEST,
    ERROR_RATE_LIMITED,
    ERROR_UNAUTHORIZED,
    KIND_ERROR,
    KIND_LLM,
    TranslationJob,
    llm_configured,
    lookup_exact_terms,
    translate_request_async,
)

from wuwaterm.translation_policy import LLM_INPUT_CHAR_LIMIT

from ..auth import SCOPE_META, SCOPE_TRANSLATE
from ..errors import ApiError
from ..settings import WEB_MOUNT_PATH
from . import render
from .session import SessionStore

LOGGER = logging.getLogger("wuwaterm.api.web")

SESSION_COOKIE_NAME = "wuwaterm_session"
# The header the edge injects. Named with a vendor prefix so it cannot collide
# with anything a proxy sets on its own.
EDGE_HEADER_NAME = "x-wuwaterm-edge"
# The shared pipeline's own ceiling, IMPORTED rather than restated. A local
# constant here was set to 4000 while the limit actually enforced for every
# surface is 2000, so this layer accepted text it would then refuse deeper in -
# the divergence a second presentation layer is most likely to introduce, and
# exactly the thing copying a number instead of importing it produces.
TEXT_MAX_LENGTH = LLM_INPUT_CHAR_LIMIT
TERM_QUERY_MAX_LENGTH = 120

# Published error vocabulary rendered for a human reading Chinese. The API
# answers these as codes in JSON; this surface answers the same conditions with
# the same meanings, which is what keeps the two presentation layers consistent
# about what happened rather than merely both failing.
_MESSAGE_BY_CODE = {
    ERROR_UNAUTHORIZED: "会话已失效，请重新加载页面。",
    ERROR_FORBIDDEN: "当前凭据没有这项功能的权限。",
    ERROR_RATE_LIMITED: "请求过于频繁，请稍后再试。",
    ERROR_INVALID_REQUEST: "请求无效。",
    ERROR_INTERNAL: "服务内部错误。",
    "llm_unavailable": "翻译服务暂时不可用。",
    "llm_budget_exhausted": "翻译额度已用尽，请稍后再试。",
    "input_too_long": "文本过长，请分段翻译。",
}


def _message_for(code: str) -> str:
    return _MESSAGE_BY_CODE.get(code, _MESSAGE_BY_CODE[ERROR_INTERNAL])


class _NeedsSession(Exception):
    """A state-changing request arrived without a live session.

    Answered by sending the browser to the matching page, which mints one on
    the way. Not an error the owner needs to read: the ordinary cause is a form
    submitted after the session expired, and the ordinary repair is to load the
    page again - which is exactly what the redirect does.
    """


class _Refused(Exception):
    """Raised when a request must not reach application logic at all.

    Deliberately distinct from ApiError: an ApiError describes a request that
    was understood and refused, and it renders as a page. This one describes a
    request that never established who it was from, and it renders as a bare
    404 with no page, no explanation and no hint that anything is mounted here.
    """


def _edge_marker_ok(request: Request, expected: str) -> bool:
    """Constant-time comparison of the edge marker, on BYTES.

    Comparing the decoded strings was a fault, not merely a style choice.
    Starlette decodes header values as latin-1, so any byte >= 0x80 in the
    header yields a non-ASCII str, and ``secrets.compare_digest`` raises
    TypeError on non-ASCII str operands. That TypeError was not an ApiError and
    not a _Refused, so it escaped the sub-application entirely and surfaced as a
    500 from the parent's exception handler -- an UNAUTHENTICATED caller could
    both distinguish this route from a 404 and drive a traceback into the API
    process's log, once per request. Encoding both sides first removes the fault
    while keeping the constant-time comparison the header deserves.
    """
    if not expected:
        return False
    presented = request.headers.get(EDGE_HEADER_NAME, "")
    try:
        left = presented.encode("latin-1", "replace")
        right = expected.encode("utf-8")
    except (UnicodeError, AttributeError):
        return False
    return secrets.compare_digest(left, right)


async def _establish_principal(request: Request, *, may_mint: bool):
    """Resolve this browser request to a device principal.

    The order here is load-bearing and mirrors the API's own admission
    sequence in ``auth.authenticated_device``: prove the request came through
    the edge, then resolve identity, then apply the per-device rate limit.
    Reordering it would let an unauthenticated caller reach the credential
    store, which is the specific thing the API's sequence is arranged to
    prevent.
    """
    # Imported here rather than at module scope: ``..app`` imports THIS module
    # to mount it, so a module-scope import would be a cycle. By the time any
    # request runs, ``..app`` is fully initialised.
    from ..app import _in_credential_pool, _require_active_device

    state = request.app.state
    settings = state.settings

    # (1) The edge. A request that reached the loopback port directly did not
    # pass the private protection layer, and is refused before anything else
    # happens - before the session map is touched, before the credential store
    # is opened. compare_digest because this is a secret comparison and the
    # timing of a plain == is a (small, but free to remove) side channel.
    if not _edge_marker_ok(request, settings.web_edge_secret):
        LOGGER.info("web request refused: edge marker absent or wrong")
        raise _Refused()

    sessions: SessionStore = state.web_sessions
    session = sessions.resolve(request.cookies.get(SESSION_COOKIE_NAME))

    if session is None:
        # A state-changing request may NOT mint a session, and that restriction
        # is what makes SameSite=Strict mean something.
        #
        # While minting happened on any request, an absent cookie was not a
        # refusal - it was the trigger to create one. So a cross-site form POST
        # from a hostile page still succeeded: the browser withholds the
        # SameSite=Strict cookie, the edge injects its marker on every proxied
        # request regardless of who caused it, and the browser attaches the
        # basic-auth credentials it has cached for the site. The request then
        # minted a fresh session and ran. The attacker could not READ the
        # response (same-origin policy), but could spend the model-call budget
        # at will. Requiring a live session here closes that: the POST now needs
        # a cookie the browser will not send cross-site, and the only way to get
        # one is to load the page first.
        if not may_mint:
            raise _NeedsSession()
        # Mint from the SERVER-HELD token. This is the only place the token is
        # used, and it never leaves this process.
        token = settings.web_device_token
        if not token:
            LOGGER.error("web layer is enabled but no device token is configured")
            raise ApiError(ERROR_INTERNAL, "web layer is not fully configured")
        slots = state.auth_slots
        if not slots.acquire(blocking=False):
            # Same shed-before-scrypt rule the API applies: a full verifier is
            # answered without scheduling another derivation.
            raise ApiError(ERROR_RATE_LIMITED)
        try:
            device = await _in_credential_pool(
                request.app, state.device_store.authenticate, token
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as a served error
            LOGGER.warning("web credential verification failed: %s", type(exc).__name__)
            raise ApiError(
                ERROR_INTERNAL, "credential store is temporarily unavailable",
                status_code=503,
            ) from None
        finally:
            slots.release()
        if device is None:
            LOGGER.error("configured web device token was rejected by the store")
            raise ApiError(ERROR_UNAUTHORIZED)
        session = sessions.create(device)
        request.state.new_session = session
    else:
        # An existing session still has to prove the device is live, or the
        # session map would be a way to outlive revocation. The principal
        # itself comes from the session snapshot rather than from a second
        # credential verification: re-deriving scrypt on every page view would
        # make the browser surface far more expensive than the API for the same
        # work, and there is nothing to re-derive it FROM - the token is not in
        # the request. Liveness is the part that can change, and liveness is
        # exactly what is re-read here, through the API's own helper so the two
        # presentation layers cannot drift on what "revoked in flight" means.
        device = session.device
        try:
            await _require_active_device(request, device)
        except ApiError:
            # Drop the dead entry rather than leaving it to hold one of the
            # bounded slots for the rest of its TTL. The eviction loop removes
            # the OLDEST entry, not the deadest, so a revoked session left in
            # place can outlive a live one when the map is under pressure.
            sessions.discard(session.session_id)
            raise

    # (3) The per-device rate limit, on the PARENT'S limiter instance and keyed
    # by the same device id the API uses - so browser traffic and desktop
    # traffic for one device share one bucket instead of getting one each.
    limiter = state.rate_limiter
    if not limiter.allow(device.device_id):
        raise ApiError(ERROR_RATE_LIMITED)
    return device


# Methods permitted to CREATE a session. GET and HEAD only: see the comment at
# the minting branch in _establish_principal for why a POST must not.
_MINTING_METHODS = frozenset({"GET", "HEAD"})


def _harden(response: Response) -> Response:
    """The headers every response from this surface carries.

    Applied here rather than inside the page renderer so that the responses
    which carry no page - the refusal, the redirects - get them too. They were
    previously attached only on the rendered-page path, so the exact responses
    an unauthenticated caller could reach were the ones with nothing on them.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # No script, no frame, no external anything. Stated rather than assumed, so
    # that an injected string that somehow survived escaping still has nowhere
    # to execute.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response


def _bare(response: Response) -> Response:
    """A response that carries no page and mints no cookie."""
    return _harden(response)


def _refuse() -> Response:
    """The answer to anything that did not come through the edge.

    404 with an empty body, and identical for every path under the mount: a
    caller that bypassed the edge must not be able to tell a real route from an
    invented one, nor that anything is mounted here at all.
    """
    return _harden(Response(status_code=404))


def _finish(request: Request, response: Response) -> Response:
    """Attach a freshly minted session cookie, if this request created one."""
    session = getattr(request.state, "new_session", None)
    if session is not None:
        settings = request.app.state.settings
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session.session_id,
            max_age=settings.web_session_ttl_seconds,
            # Path-scoped rather than __Host- prefixed. __Host- would force
            # Path=/, which broadcasts the cookie to every other application
            # co-hosted on this site; scoping it here keeps it to this surface.
            # The cost is losing __Host-'s guarantee that no subdomain could
            # have set it, and docs/adr/0014 records that trade.
            path=WEB_MOUNT_PATH,
            httponly=True,
            secure=True,
            samesite="strict",
        )
    # A shared cache holding a rendered page would outlive the session that
    # authorised it, so this surface is never cacheable.
    return _harden(response)


def _html(request: Request, body: str, active: str, status: int = 200) -> Response:
    document = render.page(mount=WEB_MOUNT_PATH, active=active, body=body)
    return _finish(request, HTMLResponse(document, status_code=status))


def _error_page(request: Request, active: str, code: str, status: int) -> Response:
    """An error still renders the form, carrying whatever was typed.

    Measured in a browser before this existed: an error replaced the whole
    view with a message, so the text the owner had just typed was gone and the
    only way back was the browser's own back button. On a phone that is the
    difference between retrying and giving up.
    """
    typed = getattr(request.state, "submitted", "")
    if active == "translate":
        form = render.translate_view(mount=WEB_MOUNT_PATH, text=typed)
    else:
        form = render.lookup_view(mount=WEB_MOUNT_PATH, query=typed)
    body = render.error_block(_message_for(code)) + form
    return _html(request, body, active, status=status)


def _guarded(handler, active: str, scope: str):
    """Wrap a view with the admission sequence, the scope check and rendering.

    ``scope`` is not optional. The API gates dictionary reads on the meta scope
    and translation on the translate scope; a presentation layer over the same
    pipeline that skipped the check would be a way to exercise a capability the
    device was not granted, which is a bypass rather than a convenience.
    """

    async def endpoint(request: Request) -> Response:
        try:
            device = await _establish_principal(
                request, may_mint=request.method in _MINTING_METHODS
            )
            if not device.has_scope(scope):
                raise ApiError(ERROR_FORBIDDEN)
        except _Refused:
            return _refuse()
        except _NeedsSession:
            landing = f"{WEB_MOUNT_PATH}/" if active == "lookup" else f"{WEB_MOUNT_PATH}/translate"
            return _bare(RedirectResponse(landing, status_code=303))
        except ApiError as exc:
            return _error_page(request, active, exc.code, exc.status_code)
        except Exception:  # noqa: BLE001 - containment, see below
            # The failure domain stops HERE. ADR 0014 accepts that a defect in
            # this layer shares a process with the API the desktop client
            # depends on, and lists mitigations; letting an unexpected
            # exception escape into the parent's handler is how that acceptance
            # turns into an actual outage signal in the API's own logs. An
            # unhandled fault becomes this surface's 500 and nobody else's.
            LOGGER.exception("web layer fault contained")
            return _error_page(request, active, ERROR_INTERNAL, 500)
        try:
            return await handler(request, device)
        except ApiError as exc:
            return _error_page(request, active, exc.code, exc.status_code)
        except Exception:  # noqa: BLE001 - same containment on the handler
            LOGGER.exception("web layer fault contained")
            return _error_page(request, active, ERROR_INTERNAL, 500)

    return endpoint


async def _form_value(request: Request, field: str, limit: int) -> str:
    """Read one field from a urlencoded form body.

    Parsed here rather than through ``request.form()`` for two reasons. The
    framework helper requires the python-multipart package, and this process
    otherwise needs nothing beyond httpx + fastapi + uvicorn - a surface added
    to avoid raising the deployment's ceiling should not raise its dependency
    count either. More usefully, refusing every content type except urlencoded
    means this application has no multipart parser reachable from the network
    at all, so file upload is not a thing that can be attempted here.

    The body itself is already bounded: the parent application's body-limit
    middleware wraps the whole ASGI app, mounted sub-applications included, so
    there is no separate size check to forget here.
    """
    declared = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if declared != "application/x-www-form-urlencoded":
        raise ApiError(ERROR_INVALID_REQUEST)
    try:
        text = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        raise ApiError(ERROR_INVALID_REQUEST) from None
    # keep_blank_values so that clearing the box and submitting reads as "empty
    # query" rather than as "field absent", which the views render differently.
    # Last value wins on a repeated field, matching how form parsers behave for
    # a single-valued input.
    parsed = parse_qs(text, keep_blank_values=True)
    values = parsed.get(field) or [""]
    value = values[-1].strip()
    # Stashed BEFORE the length check, so that the error page for text that was
    # too long still shows the text - otherwise the one error most likely to
    # need editing is the one that discards what there was to edit.
    request.state.submitted = value
    if len(value) > limit:
        raise ApiError("input_too_long", status_code=422)
    return value


async def _lookup_get(request: Request, device) -> Response:
    return _html(request, render.lookup_view(mount=WEB_MOUNT_PATH), "lookup")


async def _lookup_post(request: Request, device) -> Response:
    query = await _form_value(request, "q", TERM_QUERY_MAX_LENGTH)
    if not query:
        return _html(request, render.lookup_view(mount=WEB_MOUNT_PATH), "lookup")
    matches = await asyncio.to_thread(
        lookup_exact_terms, request.app.state.term_service, query
    )
    return _html(
        request,
        render.lookup_view(
            mount=WEB_MOUNT_PATH, query=query, matches=matches, searched=True
        ),
        "lookup",
    )


async def _translate_get(request: Request, device) -> Response:
    return _html(request, render.translate_view(mount=WEB_MOUNT_PATH), "translate")


async def _translate_post(request: Request, device) -> Response:
    text = await _form_value(request, "text", TEXT_MAX_LENGTH)
    if not text:
        return _html(request, render.translate_view(mount=WEB_MOUNT_PATH), "translate")
    state = request.app.state
    outcome = await translate_request_async(
        state.term_service,
        state.translator,
        TranslationJob(text=text, forced_to_chinese=None),
        # The PARENT'S budget object. Not a new one, and not a copy: the whole
        # in-process argument rests on this being the same instance the API
        # route spends from.
        before_llm_call=state.llm_budget,
        offload=asyncio.to_thread,
    )
    if outcome.kind == KIND_ERROR:
        raise ApiError(outcome.error_code or ERROR_INTERNAL)
    if outcome.kind == KIND_LLM and not llm_configured():
        # Same refusal the API makes, for the same reason: with no model
        # configured the pipeline returns the source text with official terms
        # substituted, which over a translation surface would read as a
        # successful translation rather than as "nothing translated it".
        raise ApiError("llm_unavailable")
    # The post-model seam, matching what the API route does at the same point.
    # Without it a revocation that committed while the model call was in flight
    # was still served here while the JSON surface refused it - the two
    # presentation layers disagreeing about what "revoked in flight" means,
    # which is precisely the drift this layer claims not to have.
    # serve_on_store_error=True for the same reason the API uses it: the budget
    # slot and the round trip are already spent, so a transient store hiccup
    # must not discard a finished translation and invite a paid retry. A
    # definitive revocation still refuses.
    from ..app import _require_active_device

    await _require_active_device(request, device, serve_on_store_error=True)
    return _html(
        request,
        render.translate_view(
            mount=WEB_MOUNT_PATH, text=text, result=outcome, translated=True
        ),
        "translate",
    )


async def _lookup_redirect_view(request: Request, device) -> Response:
    """A GET of the lookup POST target lands on the lookup page.

    Reached by reloading after a search, or by a browser replaying history.
    Redirecting rather than 405-ing keeps the back button from producing an
    error page on a surface that has no way to explain one.
    """
    return _bare(RedirectResponse(f"{WEB_MOUNT_PATH}/", status_code=303))


class _EdgeGate:
    """Refuse anything without the edge marker BEFORE routing happens.

    Doing this per-endpoint left the router itself answering off-edge callers,
    and the router has several ways to say something other than "no": a 307 for
    a trailing slash on a path that exists, a 405 for a known path with the
    wrong method, and a `Not Found` body for an unknown one - each of which
    distinguishes a real route from an invented one to a caller that never
    presented the marker. Refusing above the router collapses all of that into
    one answer, so the only thing an off-edge caller can learn is that
    something answered 404, which is what an unmounted path says too.

    The per-request check inside _establish_principal is deliberately KEPT as
    well. It is cheap, and it means the guarantee does not depend on a future
    edit remembering to keep this middleware installed.
    """

    def __init__(self, app, parent) -> None:
        self.app = app
        self.parent = parent

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        secret = self.parent.state.settings.web_edge_secret
        if not _edge_marker_ok(Request(scope), secret):
            LOGGER.info("web request refused at the edge gate")
            await _refuse()(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_web_app(parent_app) -> Starlette:
    """Build the sub-application. The caller mounts it; this does not.

    ``parent_app`` is required and is not optional-with-a-default on purpose:
    a web application constructed without one would build its own services and
    silently become the third budget this design exists to avoid.
    """
    routes = [
        Route("/", _guarded(_lookup_get, "lookup", SCOPE_META), methods=["GET"]),
        Route("/lookup", _guarded(_lookup_post, "lookup", SCOPE_META), methods=["POST"]),
        # GUARDED, like every other route. Registered unguarded, it answered a
        # 303 to a caller that had never presented the edge marker - which told
        # that caller both that something is mounted here and that the switch is
        # on, on a surface whose refusal is otherwise indistinguishable from an
        # unmounted path.
        Route(
            "/lookup",
            _guarded(_lookup_redirect_view, "lookup", SCOPE_META),
            methods=["GET"],
        ),
        Route(
            "/translate",
            _guarded(_translate_get, "translate", SCOPE_TRANSLATE),
            methods=["GET"],
        ),
        Route(
            "/translate",
            _guarded(_translate_post, "translate", SCOPE_TRANSLATE),
            methods=["POST"],
        ),
    ]
    # redirect_slashes OFF. Starlette's default answers `/wuwaterm-web/x/` with
    # a 307 to `/wuwaterm-web/x` when route `x` exists, and that redirect is
    # produced by the ROUTER, before any endpoint and therefore before the edge
    # check. It made the whole route table enumerable without ever presenting
    # the marker: 307 meant the path exists, 404 meant it does not. With it off
    # a trailing slash is simply not a route, and every unmatched path under the
    # mount answers alike.
    web = Starlette(
        routes=routes,
        middleware=[Middleware(_EdgeGate, parent=parent_app)],
    )
    # Set on the ROUTER, not passed to Starlette: this Starlette version does
    # not accept it as a constructor argument, and assigning it afterwards is
    # the form that works across both.
    web.router.redirect_slashes = False
    # THE shared-state assignment. See this module's docstring: the parent's
    # State object itself, not a copy of its attributes.
    web.state = parent_app.state
    settings = parent_app.state.settings
    web.state.web_sessions = SessionStore(
        ttl_seconds=settings.web_session_ttl_seconds,
        max_sessions=settings.web_max_sessions,
    )
    return web
