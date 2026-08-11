"""FastAPI application for the wuwaterm HTTP adapter.

Design constraints that show up all over this module:

* Plain text only. The Telegram HTML path is an adapter extension injected by
  the bot; this adapter injects no markup translator, so rich-text markup can
  never leak into the HTTP contract.
* Every failure renders the same envelope with an enumerated code
  (see :mod:`wuwaterm_api.errors`), so a client can branch on ``code`` and
  never on prose.
* Budgets are per process. This adapter has its own translator instance,
  its own LLM concurrency slot count and its own per-minute call budget; they
  are never described as global.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from wuwaterm.application import (
    ERROR_FORBIDDEN,
    ERROR_LLM_UNAVAILABLE,
    ERROR_INTERNAL,
    ERROR_INVALID_REQUEST,
    ERROR_PAYLOAD_TOO_LARGE,
    ERROR_RATE_LIMITED,
    ERROR_UNAUTHORIZED,
    KIND_ERROR,
    KIND_LLM,
    LlmCallBudget,
    SlidingWindowRateLimiter,
    TranslationJob,
    build_term_service,
    build_translator,
    llm_configured,
    lookup_exact_terms,
    probe_database,
    service_metadata,
    translate_request_async,
)
from wuwaterm.logging_utils import redact_id

from . import API_VERSION
from .auth import SCOPE_META, SCOPE_TRANSLATE, Device, DeviceStore
from .errors import MESSAGE_BY_CODE, ApiError, error_body

# Framework-raised routing failures mapped onto the published vocabulary.
_ROUTING_ERROR_CODES = {
    400: ERROR_INVALID_REQUEST,
    401: ERROR_UNAUTHORIZED,
    403: ERROR_FORBIDDEN,
    404: ERROR_INVALID_REQUEST,
    405: ERROR_INVALID_REQUEST,
    413: ERROR_PAYLOAD_TOO_LARGE,
    429: ERROR_RATE_LIMITED,
}

LOGGER = logging.getLogger("wuwaterm_api")

REQUEST_ID_HEADER = "X-Request-Id"

TERM_QUERY_MAX_LENGTH = 200


def service_version() -> str:
    try:
        return package_version("wuwaterm")
    except PackageNotFoundError:  # pragma: no cover - only in odd installs
        return "unknown"


# --------------------------------------------------------------------------
# Wire models (field names ARE the wire names; no renaming layer anywhere)
# --------------------------------------------------------------------------


class TranslationRequestBody(BaseModel):
    text: str = Field(description="Source text to translate. Plain text only.")
    to: Literal["en", "zh"] | None = Field(
        default=None,
        description=(
            "Force the target language. Omit or send null to auto-detect from "
            "the source text."
        ),
    )


class TranslationResponseBody(BaseModel):
    kind: Literal["noop", "exact", "fuzzy", "llm"] = Field(
        description=(
            "Which stage answered: noop (nothing translatable), exact "
            "(official dictionary hit), fuzzy (trusted pinyin hit) or llm."
        )
    )
    text: str = Field(description="Translated text.")
    direction: Literal["en", "zh"] = Field(description="Target language used.")
    dictionary_miss: bool = Field(
        description=(
            "True when a short query was answered by the model with no "
            "official term locked, so the answer is not authoritative."
        )
    )
    request_id: str


class TermMatchBody(BaseModel):
    zh: str
    en: str
    category: str
    score: float
    reason: str


class TermsResponseBody(BaseModel):
    query: str
    matches: list[TermMatchBody]
    request_id: str


class MetaResponseBody(BaseModel):
    service_version: str
    api_version: str
    schema_version: str | None
    source_profile: str | None
    source_commit: str | None
    term_count: int
    llm_configured: bool
    request_id: str


class HealthResponseBody(BaseModel):
    status: Literal["ok", "ready"]


# The wire vocabulary, spelled out so a generated client can model every
# branch exhaustively and so the contract gate notices a code being added or
# removed. It must stay equal to the application layer's set.
ErrorCode = Literal[
    "unauthorized",
    "forbidden",
    "rate_limited",
    "payload_too_large",
    "invalid_request",
    "input_too_long",
    "llm_unavailable",
    "llm_budget_exhausted",
    "internal",
]


class ErrorDetailBody(BaseModel):
    code: ErrorCode = Field(
        description="Enumerated, stable failure classification."
    )
    message: str = Field(description="Short operator-facing text.")


class ErrorResponseBody(BaseModel):
    error: ErrorDetailBody
    request_id: str


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponseBody, "description": "Invalid request"},
    401: {"model": ErrorResponseBody, "description": "Missing or invalid credential"},
    403: {"model": ErrorResponseBody, "description": "Scope not granted"},
    413: {"model": ErrorResponseBody, "description": "Body too large"},
    422: {"model": ErrorResponseBody, "description": "Input rejected"},
    429: {"model": ErrorResponseBody, "description": "Rate limited"},
    500: {"model": ErrorResponseBody, "description": "Internal error"},
    503: {"model": ErrorResponseBody, "description": "Translation unavailable"},
    504: {
        "model": ErrorResponseBody,
        "description": (
            "Request exceeded the server time budget. Classified as `internal`:"
            " the enumerated code set is closed, so the HTTP status is what"
            " distinguishes a timeout from a genuine 500."
        ),
    },
}

# The bearer scheme is declared so a generated client can configure the
# credential. auto_error is off because this API answers with its own envelope
# instead of the framework's default body.
BEARER_SCHEME = HTTPBearer(auto_error=False, description="Device token.")


# --------------------------------------------------------------------------
# Middleware
# --------------------------------------------------------------------------


def _new_request_id() -> str:
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a freshly minted request id; never trust an inbound one.

    A caller-supplied ``X-Request-Id`` used to be accepted when it matched a
    charset that ALSO matches the token shape ``wtd1.<id>.<secret>``, and it was
    then echoed into the auth-reject log line and into the HTTP error envelope.
    A request could therefore route its own credential straight into the logs
    and into a response body. The correlation id is now always generated
    server-side and the inbound header is ignored entirely, so nothing a caller
    sends can be logged or echoed. The generated id is still returned in the
    response header for correlation.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = _new_request_id()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def _error_response(exc: ApiError, request: Request) -> JSONResponse:
    """Render the stable envelope from middleware.

    Middleware sits OUTSIDE the exception-handler middleware, so a raised
    ApiError would surface as a bare 500 here; middleware returns the response
    itself instead.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, _request_id(request)),
    )


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Bound both the size and the arrival time of a request body.

    Size alone is not enough: a caller can stay under the cap and still hold a
    request open by trickling bytes. The read therefore carries its own
    deadline, because the body arrives before the handler timeout can apply to
    it.
    """

    def __init__(self, app, max_body_bytes: int, read_timeout_seconds: float):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes
        self.read_timeout_seconds = read_timeout_seconds

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                declared_length = int(declared)
            except ValueError:
                return _error_response(
                    ApiError(ERROR_INVALID_REQUEST, "content-length is not a number"),
                    request,
                )
            if declared_length < 0:
                return _error_response(
                    ApiError(ERROR_INVALID_REQUEST, "content-length is negative"),
                    request,
                )
            if declared_length > self.max_body_bytes:
                return _error_response(ApiError(ERROR_PAYLOAD_TOO_LARGE), request)
        # A caller can omit content-length or use chunked encoding, so the
        # header is a hint, not the enforcement point. Read incrementally and
        # abort at the cap, so an unauthenticated caller can never make this
        # process hold more than max_body_bytes of request payload.
        received = bytearray()
        try:
            async with asyncio.timeout(self.read_timeout_seconds):
                async for chunk in request.stream():
                    received.extend(chunk)
                    if len(received) > self.max_body_bytes:
                        return _error_response(
                            ApiError(ERROR_PAYLOAD_TOO_LARGE), request
                        )
        except (asyncio.TimeoutError, TimeoutError):
            LOGGER.warning(
                "request body read timed out path=%s request_id=%s",
                request.url.path,
                _request_id(request),
            )
            return _error_response(
                ApiError(
                    ERROR_INTERNAL,
                    "request body did not arrive within the server time budget",
                    status_code=504,
                ),
                request,
            )
        # Hand the bytes we consumed back to the cached request so the route
        # still sees its own body.
        request._body = bytes(received)
        return await call_next(request)


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Bound the wall-clock cost of a single request."""

    def __init__(self, app, timeout_seconds: float):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds

    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), self.timeout_seconds)
        except (asyncio.TimeoutError, TimeoutError):
            LOGGER.warning(
                "request timed out path=%s request_id=%s",
                request.url.path,
                _request_id(request),
            )
            return _error_response(
                ApiError(
                    ERROR_INTERNAL,
                    "request exceeded the server time budget",
                    status_code=504,
                ),
                request,
            )


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unset")


async def _require_active_device(request: Request, device: Device) -> None:
    """Refuse if the device was revoked while this request was in flight.

    Verification takes a snapshot of the device; a revocation that commits
    after it but before an expensive call or before the response would
    otherwise be served on a credential that is no longer valid. This is a
    cheap read run at the TOCTOU seams: before the model call and before
    returning. It NARROWS the window best-effort; it does not close it — a
    revocation that commits after this read is still served.
    """
    try:
        active = await asyncio.to_thread(
            request.app.state.device_store.is_active, device.device_id
        )
    except (sqlite3.Error, OSError):
        # A transient store failure on the POST-auth re-check (database is
        # locked, a disk I/O error) is infrastructure, not a credential
        # rejection: answering 401 here would tell a valid device to re-pair.
        # Reserve unauthorized for a genuine revocation; a hiccup is 503.
        LOGGER.warning(
            "device re-check store error path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        raise ApiError(
            ERROR_INTERNAL,
            "device re-check is temporarily unavailable",
            status_code=503,
        )
    if not active:
        LOGGER.info(
            "device revoked in flight path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        raise ApiError(ERROR_UNAUTHORIZED)


async def authenticated_device(
    request: Request,
    presented: Annotated[
        HTTPAuthorizationCredentials | None, Depends(BEARER_SCHEME)
    ] = None,
) -> Device:
    if presented is None or presented.scheme.lower() != "bearer":
        raise ApiError(ERROR_UNAUTHORIZED)
    store: DeviceStore = request.app.state.device_store
    slots = request.app.state.auth_slots
    token = presented.credentials.strip()
    if not slots.acquire(blocking=False):
        # The bounded verification executor is full. Shed load HERE, before
        # scheduling another expensive scrypt derivation: an unauthenticated
        # caller must not be able to make the credential check itself the load,
        # and queuing these requests behind the semaphore would do exactly
        # that. Non-queuing admission — the rate-limited code tells the caller
        # to back off, and no worker thread is ever left blocked on the slot.
        LOGGER.info(
            "auth admission shed path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        raise ApiError(ERROR_RATE_LIMITED)
    # The slot is owned by THIS coroutine and released here, on the loop, not
    # inside the worker: if the awaiting task is cancelled (time budget, client
    # disconnect) while the to_thread job is still QUEUED on a saturated
    # executor, the worker never runs — so a worker-side release would leak the
    # slot permanently and wedge every later request into 429. store.authenticate
    # touches no shared lock, so releasing while a started worker finishes only
    # relaxes the bound briefly; it never double-releases or strands a slot.
    try:
        device = await asyncio.to_thread(store.authenticate, token)
    finally:
        slots.release()
    if device is None:
        LOGGER.info(
            "auth rejected path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        raise ApiError(ERROR_UNAUTHORIZED)
    limiter: SlidingWindowRateLimiter = request.app.state.rate_limiter
    if not limiter.allow(device.device_id):
        LOGGER.info(
            "rate limited device=%s request_id=%s",
            redact_id(device.device_id),
            _request_id(request),
        )
        # Deliberately BEFORE record_use: a refused request must not be able to
        # drive an unbounded stream of writes into the credential store.
        raise ApiError(ERROR_RATE_LIMITED)
    # record_use only stamps a row that is still active. A zero count means the
    # device was revoked between verification and admission: reject it now
    # rather than serve a request for a credential that is no longer valid. A
    # transient store failure on this write is infrastructure, not a credential
    # problem, so it becomes 503 rather than a misleading unauthorized/500.
    try:
        updated = await asyncio.to_thread(store.record_use, device.device_id)
    except (sqlite3.Error, OSError):
        LOGGER.warning(
            "credential store error on admission path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        raise ApiError(
            ERROR_INTERNAL,
            "credential store is temporarily unavailable",
            status_code=503,
        )
    if updated != 1:
        LOGGER.info(
            "auth rejected: device revoked in flight path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        raise ApiError(ERROR_UNAUTHORIZED)
    request.state.device = device
    return device


def require_scope(scope: str):
    async def dependency(
        device: Annotated[Device, Depends(authenticated_device)],
    ) -> Device:
        if not device.has_scope(scope):
            raise ApiError(ERROR_FORBIDDEN)
        return device

    return dependency


# --------------------------------------------------------------------------
# Application factory
# --------------------------------------------------------------------------


OPENAPI_DESCRIPTION = """
Read-only translation surface for the Wuthering Waves terminology service.

The same dictionary-first pipeline answers here and in the chat adapter:
official dictionary hit first, then a trusted pinyin hit, then a term-locked
model call. Responses are plain text; rich-text markup is never part of this
contract.

Authentication is a bearer device token issued by the operator. There is no
registration endpoint: tokens are created out of band and can be revoked.
"""


def create_app(
    settings=None,
    *,
    device_store: DeviceStore | None = None,
    term_service=None,
    translator=None,
) -> FastAPI:
    from .settings import ApiSettings

    resolved = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        closer = getattr(app.state.translator, "aclose", None)
        if closer is not None:
            await closer()

    app = FastAPI(
        title="wuwaterm API",
        version=service_version(),
        description=OPENAPI_DESCRIPTION.strip(),
        openapi_url="/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.settings = resolved
    app.state.device_store = device_store or DeviceStore(
        resolved.device_db_path,
        guard_legacy_default=resolved.device_db_is_default,
    )
    app.state.term_service = term_service or build_term_service(resolved.db_path)
    app.state.translator = translator or build_translator(
        resolved.db_path,
        timeout=resolved.llm_timeout_seconds,
        max_concurrency=resolved.llm_max_concurrency,
    )
    app.state.rate_limiter = SlidingWindowRateLimiter(
        limit=resolved.rate_limit_per_minute
    )
    app.state.llm_budget = LlmCallBudget(resolved.llm_calls_per_minute)
    app.state.auth_slots = threading.BoundedSemaphore(resolved.auth_max_concurrency)

    # Added last == outermost: the request id wraps everything, so even a
    # failure produced by an inner middleware carries it. The body read and the
    # handler each carry the same time budget, applied where each of them
    # actually runs.
    app.add_middleware(TimeoutMiddleware, timeout_seconds=resolved.request_timeout_seconds)
    app.add_middleware(
        BodyLimitMiddleware,
        max_body_bytes=resolved.max_body_bytes,
        read_timeout_seconds=resolved.request_timeout_seconds,
    )
    app.add_middleware(RequestIdMiddleware)

    _register_error_handlers(app)
    _register_routes(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> Response:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, _request_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> Response:
        # The raw pydantic report can echo the submitted text back; the stable
        # envelope deliberately does not.
        return JSONResponse(
            status_code=400,
            content=error_body(
                ERROR_INVALID_REQUEST,
                "request payload is not valid",
                _request_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        # Unknown paths and unsupported methods are routing failures raised by
        # the framework, not by this application. Without this handler they
        # would answer with the framework's default body and break the one
        # documented non-2xx shape.
        code = _ROUTING_ERROR_CODES.get(exc.status_code, ERROR_INTERNAL)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code, MESSAGE_BY_CODE.get(code, "internal error"), _request_id(request)
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        LOGGER.exception(
            "unhandled error path=%s request_id=%s",
            request.url.path,
            _request_id(request),
        )
        return JSONResponse(
            status_code=500,
            content=error_body(ERROR_INTERNAL, "internal error", _request_id(request)),
        )


def _register_routes(app: FastAPI) -> None:
    prefix = f"/{API_VERSION}"

    @app.get("/healthz", response_model=HealthResponseBody, tags=["health"])
    async def healthz() -> HealthResponseBody:
        """Liveness. Answers as long as the process is serving; no auth."""
        return HealthResponseBody(status="ok")

    @app.get(
        "/readyz",
        response_model=HealthResponseBody,
        tags=["health"],
        responses={
            503: {
                "model": ErrorResponseBody,
                "description": (
                    "The terminology database is not readable. Classified as"
                    " `internal`; the 503 status is what distinguishes it."
                ),
            }
        },
    )
    async def readyz(request: Request) -> HealthResponseBody:
        """Readiness: the terminology database is readable right now. No auth."""
        ok = await asyncio.to_thread(probe_database, request.app.state.term_service)
        if not ok:
            raise ApiError(ERROR_INTERNAL, "dictionary is not readable", status_code=503)
        return HealthResponseBody(status="ready")

    @app.post(
        f"{prefix}/translations",
        response_model=TranslationResponseBody,
        tags=["translation"],
        responses=ERROR_RESPONSES,
    )
    async def create_translation(
        request: Request,
        body: TranslationRequestBody,
        device: Annotated[Device, Depends(require_scope(SCOPE_TRANSLATE))],
    ) -> TranslationResponseBody:
        """Translate plain text through the shared dictionary-first pipeline."""
        state = request.app.state
        forced_to_chinese = None if body.to is None else body.to == "zh"
        # Before the expensive model call: a device revoked since admission
        # must not spend an LLM budget slot or a model round trip.
        await _require_active_device(request, device)
        outcome = await translate_request_async(
            state.term_service,
            state.translator,
            TranslationJob(text=body.text, forced_to_chinese=forced_to_chinese),
            before_llm_call=state.llm_budget,
            # The dictionary stage opens SQLite and can score every term row.
            # This process serves many requests on one loop, so that work runs
            # on a worker thread instead of blocking every other request (and
            # the request time budget) while it runs.
            offload=asyncio.to_thread,
        )
        LOGGER.info(
            "translation device=%s kind=%s direction=%s request_id=%s",
            redact_id(device.device_id),
            outcome.kind,
            outcome.direction,
            _request_id(request),
        )
        if outcome.kind == KIND_ERROR:
            raise ApiError(outcome.error_code or ERROR_INTERNAL)
        if outcome.kind == KIND_LLM and not llm_configured():
            # With no model configured the pipeline returns the source text
            # with official terms substituted. That is a reasonable chat
            # fallback, but over HTTP it would look like a successful
            # translation, so this surface refuses instead of pretending.
            LOGGER.info(
                "translation refused: no model configured request_id=%s",
                _request_id(request),
            )
            raise ApiError(
                ERROR_LLM_UNAVAILABLE, "no translation model is configured"
            )
        # Before returning: close the window between the model call and the
        # response, so a revocation that commits mid-flight is not served.
        await _require_active_device(request, device)
        return TranslationResponseBody(
            kind=outcome.kind,
            text=outcome.text,
            direction=outcome.direction,
            dictionary_miss=outcome.dictionary_miss,
            request_id=_request_id(request),
        )

    @app.get(
        f"{prefix}/terms",
        response_model=TermsResponseBody,
        tags=["dictionary"],
        responses=ERROR_RESPONSES,
    )
    async def read_terms(
        request: Request,
        device: Annotated[Device, Depends(require_scope(SCOPE_META))],
        q: str = Query(description="Term to look up."),
    ) -> TermsResponseBody:
        """Exact dictionary candidates for a query. Official strings only."""
        query = q.strip()
        if not query:
            raise ApiError(ERROR_INVALID_REQUEST, "query parameter q is required")
        if len(query) > TERM_QUERY_MAX_LENGTH:
            raise ApiError(ERROR_INVALID_REQUEST, "query parameter q is too long")
        matches = await asyncio.to_thread(
            lookup_exact_terms, request.app.state.term_service, query
        )
        return TermsResponseBody(
            query=query,
            matches=[
                TermMatchBody(
                    zh=match.zh,
                    en=match.en,
                    category=match.category,
                    score=match.score,
                    reason=match.reason,
                )
                for match in matches
            ],
            request_id=_request_id(request),
        )

    @app.get(
        f"{prefix}/meta",
        response_model=MetaResponseBody,
        tags=["dictionary"],
        responses=ERROR_RESPONSES,
    )
    async def read_meta(
        request: Request,
        device: Annotated[Device, Depends(require_scope(SCOPE_META))],
    ) -> MetaResponseBody:
        """Service and data provenance. No paths, no secrets, no chat ids."""
        meta = await asyncio.to_thread(
            service_metadata, request.app.state.term_service
        )
        return MetaResponseBody(
            service_version=service_version(),
            api_version=API_VERSION,
            schema_version=meta.schema_version,
            source_profile=meta.source_profile,
            source_commit=meta.source_commit,
            term_count=meta.term_count,
            llm_configured=llm_configured(),
            request_id=_request_id(request),
        )
