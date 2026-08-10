"""Async HTTP client for the wuwaterm API.

This module only calls the API and parses what comes back. It never
re-implements dictionary lookup, direction detection, or any other
translation pipeline step; every field below is a direct pass-through of the
service's response (docs/api/openapi.json).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .config import ClientConfig
from .credentials import read_token
from .errors import (
    ERROR_CANCELLED,
    ERROR_OFFLINE,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    ClientError,
)

TokenProvider = Callable[[], "str | None"]

_BEARER_PREFIX = "Bearer "


def default_token_provider() -> str | None:
    return read_token()


# -- Response models (direct pass-through of the wire schema) --------------


@dataclass(frozen=True)
class TranslationResult:
    kind: str
    text: str
    direction: str
    dictionary_miss: bool
    request_id: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TranslationResult":
        return cls(
            kind=data["kind"],
            text=data["text"],
            direction=data["direction"],
            dictionary_miss=bool(data["dictionary_miss"]),
            request_id=data["request_id"],
        )


@dataclass(frozen=True)
class TermMatch:
    zh: str
    en: str
    category: str
    score: float
    reason: str


@dataclass(frozen=True)
class TermsResult:
    query: str
    matches: tuple[TermMatch, ...]
    request_id: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TermsResult":
        return cls(
            query=data["query"],
            matches=tuple(
                TermMatch(
                    zh=match["zh"],
                    en=match["en"],
                    category=match["category"],
                    score=float(match["score"]),
                    reason=match["reason"],
                )
                for match in data["matches"]
            ),
            request_id=data["request_id"],
        )


@dataclass(frozen=True)
class MetaResult:
    service_version: str
    api_version: str
    schema_version: str | None
    source_profile: str | None
    source_commit: str | None
    term_count: int
    llm_configured: bool
    request_id: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MetaResult":
        return cls(
            service_version=data["service_version"],
            api_version=data["api_version"],
            schema_version=data.get("schema_version"),
            source_profile=data.get("source_profile"),
            source_commit=data.get("source_commit"),
            term_count=int(data["term_count"]),
            llm_configured=bool(data["llm_configured"]),
            request_id=data["request_id"],
        )


class ApiClient:
    """Bearer-authenticated async client for the wuwaterm HTTP API."""

    def __init__(
        self,
        base_url: str,
        *,
        token_provider: TokenProvider = default_token_provider,
        timeout: float = 10.0,
        translate_timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._timeout = timeout
        self._translate_timeout = translate_timeout if translate_timeout is not None else timeout
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport
        )

    @classmethod
    def from_config(cls, config: ClientConfig) -> "ApiClient":
        return cls(
            config.base_url,
            timeout=config.request_timeout_seconds,
            translate_timeout=config.translate_timeout_seconds,
        )

    def update_base_url(self, base_url: str) -> None:
        self._client.base_url = httpx.URL(base_url)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        token = self._token_provider()
        if not token:
            return {}
        return {"Authorization": f"{_BEARER_PREFIX}{token}"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                url,
                headers=self._headers(),
                timeout=timeout if timeout is not None else self._timeout,
                **kwargs,
            )
        except asyncio.CancelledError:
            # A caller cancelled the in-flight task (e.g. the Cancel button).
            # Report it as a distinct, non-alarming state rather than a
            # generic transport failure.
            raise ClientError(ERROR_CANCELLED) from None
        except httpx.TimeoutException as exc:
            raise ClientError(ERROR_TIMEOUT) from exc
        except httpx.TransportError as exc:
            # Includes connect-refused and every other network-level failure.
            raise ClientError(ERROR_OFFLINE) from exc
        if response.status_code >= 400:
            raise self._error_from_response(response)
        return response

    def _error_from_response(self, response: httpx.Response) -> ClientError:
        try:
            payload = response.json()
            code = payload["error"]["code"]
            request_id = payload.get("request_id")
        except (ValueError, KeyError, TypeError):
            return ClientError(ERROR_UNKNOWN, status_code=response.status_code)
        if not isinstance(code, str):
            return ClientError(ERROR_UNKNOWN, status_code=response.status_code)
        return ClientError(
            code,
            request_id=request_id if isinstance(request_id, str) else None,
            status_code=response.status_code,
        )

    async def translate(
        self, text: str, *, to: str | None = None
    ) -> TranslationResult:
        body: dict[str, Any] = {"text": text}
        if to is not None:
            body["to"] = to
        response = await self._request(
            "POST",
            "/v1/translations",
            json=body,
            timeout=self._translate_timeout,
        )
        return TranslationResult.from_json(response.json())

    async def lookup_terms(self, query: str) -> TermsResult:
        response = await self._request("GET", "/v1/terms", params={"q": query})
        return TermsResult.from_json(response.json())

    async def get_meta(self) -> MetaResult:
        response = await self._request("GET", "/v1/meta")
        return MetaResult.from_json(response.json())

    async def health(self) -> bool:
        response = await self._request("GET", "/healthz")
        return response.json().get("status") == "ok"
