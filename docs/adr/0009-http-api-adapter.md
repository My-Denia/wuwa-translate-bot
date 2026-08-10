# ADR 0009: HTTP API as a second inbound adapter

- Status: Accepted
- Date: 2026-08-10

## Context

The service was reachable only through Telegram. The owner needs a desktop
client on their own machine, and a chat transport is a poor fit for it: message
formatting, chat auth and Telegram's own rate limits all leak into what is
otherwise a plain request/response translation.

Two earlier records constrain how this is allowed to be added, and both are
amended here in **context only** — neither decision is reversed:

- **[ADR 0001](0001-telegram-as-presentation-layer.md)** said "a separate web UI
  or generic HTTP API would add auth, hosting, and client surface without
  improving dictionary fidelity", and listed Telegram modules as *the*
  presentation layer. That reasoning was about a *public* generic API. The
  amendment: Telegram is now **one** presentation edge, not the only one. The
  structural rule it protects is unchanged and is now enforced harder — domain
  and application code still must not know about any transport, and the new
  adapter is machine-forbidden from importing any Telegram module.
- **[ADR 0003](0003-long-polling-not-webhook.md)** decided Telegram updates
  arrive by long polling and that no inbound HTTP listener exists for Telegram.
  The amendment: an inbound HTTP listener now exists, but it is **not a Telegram
  callback endpoint**. It serves its own versioned surface to the owner's own
  client, holds no Telegram token, and receives nothing from Telegram. ADR
  0003's delivery decision is **unchanged**: `Application.run_polling()` is
  still how updates arrive, `deploy/docker-compose.yml` still runs the bot with
  `command: ["bot"]` and no callback-URL environment, and the non-goal guard
  still forbids those markers.

The adapter also had to be addable without letting a second, divergent
translation pipeline grow beside the first.

## Decision

Add a second inbound adapter as a separate top-level package
`src/wuwaterm_api/`, served by its own process, over the shared application
layer.

- **FastAPI + uvicorn**, chosen over a bare HTTP framework because the
  machine-readable contract is a requirement, not a nicety: FastAPI generates
  the OpenAPI document from the same models that validate requests, so the
  contract cannot describe a shape the code does not accept. Pydantic supplies
  request validation for free.
- **Dependency growth is confined to an `api` extra.** `wuwaterm`'s core
  dependency set stays `httpx` + `python-telegram-bot`; the Telegram runtime
  installs without FastAPI or uvicorn. The deploy image adds `--extra api`.
- **Versioned path prefix `/v1`.** `POST /v1/translations`, `GET /v1/terms`,
  `GET /v1/meta`, plus unauthenticated `GET /healthz` and `GET /readyz` outside
  the version prefix because a liveness probe must not be versioned with the
  product surface.
- **Plain text only.** The application layer's markup translator is an
  adapter-injected seam; this adapter injects none, so Telegram HTML cannot
  reach the HTTP contract by construction rather than by review.
- **One stable error envelope**, `{"error": {"code", "message"}, "request_id"}`,
  for every non-2xx response including framework-raised routing failures. The
  code set is closed and enumerated in the schema: `unauthorized`, `forbidden`,
  `rate_limited`, `payload_too_large`, `invalid_request`, `input_too_long`,
  `llm_unavailable`, `llm_budget_exhausted`, `internal`. Codes come from
  `wuwaterm.application` so both adapters classify failures identically; the
  bot's Telegram-worded notices are never reused.
- **Committed contract snapshot + drift gate.** `docs/api/openapi.json` is
  regenerated and byte-compared by `scripts/check_api_contract.py`, which runs
  in CI. That script also re-applies the repo's product token bans to the
  snapshot, because `scripts/check_non_goals.py` does not scan `.json` and the
  snapshot would otherwise be the one file where a banned marker could hide.
- **Narrow, enforced import allowlist.** `wuwaterm_api` may import only
  `wuwaterm.application`, `wuwaterm.models`, `wuwaterm.translation_policy` and
  `wuwaterm.logging_utils` — never `bot`, `channel`, `sentence`, `lookup`, `db`,
  the `telegram_*` helpers, the bare `wuwaterm` package root or the Telegram
  SDK, and `TYPE_CHECKING` is not an exemption
  (`scripts/check_architecture_boundaries.py` `check_api_package`).
- **Packaging: the public wheel now ships the adapter.** `wuwaterm_api` is
  included in the wheel and sdist, and `wuwaterm-api = wuwaterm_api.cli:main` is
  a console entry point. This is explicitly accepted rather than worked around
  with a second distribution: the distribution boundary that matters is "no
  generated database, no game data, no runtime state", and adapter source code
  does not touch it. `scripts/check_package_artifacts.py` **requires** the
  `wuwaterm_api` members and the entry point, so a packaging change that drops
  the package fails the gate instead of silently shipping an entry point that
  cannot import itself.

Operational placement (loopback bind, separate state directory, device
credentials) is decided in [ADR 0010](0010-device-principal-auth.md) and
`docs/deployment.md`.

## Consequences

- Positive: dictionary-first behavior exists exactly once. A divergent pipeline
  in the API is not a review question; it is a boundary-guard failure.
- Positive: clients build against a byte-pinned contract, and a change to the
  wire shape cannot merge without updating the snapshot in the same commit.
- Positive: Telegram is untouched at runtime. The bot container's command,
  environment and state mounts are unchanged; only the image is rebuilt.
- Negative: the public wheel of a public repository now ships API server code
  and a second entry point. Accepted, and pinned by the packaging audit so the
  decision stays visible.
- Negative: two serving processes mean two independent budgets. The worst case
  is their sum, documented in `docs/architecture.md` "Cost topology"; no shared
  cross-process budget is built.
- Negative: the `api` extra adds FastAPI and uvicorn to the runtime image, so
  the runtime image is larger than a Telegram-only one.
- Constraint: no markup, no Telegram vocabulary and no chat identifiers in the
  HTTP contract or the snapshot; `scripts/check_api_contract.py` enforces the
  product token bans over the artifact.

## Evidence

- `src/wuwaterm_api/__init__.py` (`API_VERSION`), `app.py`, `errors.py`,
  `settings.py`, `cli.py`
- `src/wuwaterm/application.py` — shared pipeline, error-code constants,
  `build_translator`, `lookup_exact_terms`, `service_metadata`, `probe_database`
- `scripts/check_architecture_boundaries.py` `API_ALLOWED_WUWATERM_MODULES`,
  `check_api_package`; `tests/test_architecture_boundaries.py`
- `scripts/check_api_contract.py`, `docs/api/openapi.json`
- `scripts/check_package_artifacts.py` `REQUIRED_WHEEL_MEMBERS`,
  `REQUIRED_SDIST_MEMBERS`, `ENTRY_POINT_LINES`; `pyproject.toml`
  `[project.optional-dependencies] api`, `[project.scripts]`
- `deploy/entrypoint.sh` (`bot` / `api` / `device`, exit 64 otherwise),
  `.github/workflows/ci.yml` jobs `test`, `package`, `deploy-boundary`
- `tests/test_api.py`
