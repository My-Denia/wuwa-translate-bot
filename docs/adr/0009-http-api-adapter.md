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
- **Versioned path prefix `/v1` for everything that carries a credential.**
  `POST /v1/translations`, `GET /v1/terms`, `GET /v1/meta`. Three further routes
  sit outside the prefix and take no credential: `GET /healthz` and
  `GET /readyz`, because a probe must not be versioned with the product surface,
  and `GET /openapi.json`, which FastAPI generates from `openapi_url`. The
  schema route is kept deliberately — it serves the same *document* as the
  committed `docs/api/openapi.json`, though not the same bytes: the snapshot is
  written by `check_api_contract.render()` with `indent=2, sort_keys=True`,
  while the endpoint serializes through FastAPI's own JSON response, so
  whitespace and key order differ and a checksum comparison against the
  committed file will not match. Meanwhile `docs_url` and `redoc_url` are both
  set to `None`, so no interactive documentation UI is served. Six routes in
  total; that is the whole inbound surface.
- **Plain text only.** The application layer's markup translator is an
  adapter-injected seam; this adapter injects none, so Telegram HTML cannot
  reach the HTTP contract by construction rather than by review.
- **One stable error envelope**, `{"error": {"code", "message"}, "request_id"}`,
  for every non-2xx response **this application produces** — from an exception
  handler (`ApiError`, validation errors, the framework's own 404 and 405) and
  equally from the middleware that answers 413, the malformed-`content-length`
  400 and both 504s, which sits outside the handler stack and therefore renders
  the envelope itself (`_error_response`). The one documented exception is a
  response the application does not produce: the router's trailing-slash
  redirect. `GET /healthz/`, `/readyz/` or `/v1/meta/` returns a 307 with a
  `Location` header and an empty body, because `redirect_slashes` is left at its
  default and the router answers it before any route handler or exception
  handler runs. The middlewares do run, so that 307 still carries its
  `X-Request-Id`. Clients should follow redirects or use the exact paths, and
  should not assume a redirect body parses as the envelope. The
  code set is closed and enumerated in the schema: `unauthorized`, `forbidden`,
  `rate_limited`, `payload_too_large`, `invalid_request`, `input_too_long`,
  `llm_unavailable`, `llm_budget_exhausted`, `internal`. The pipeline's own
  failures come from `wuwaterm.application`, so where an outcome already carries
  an `error_code` both adapters classify it the same way; the bot's
  Telegram-worded notices are never reused. That shared vocabulary does not mean
  the two surfaces behave identically. The transport codes — `unauthorized`,
  `forbidden`, `rate_limited`, `payload_too_large` — are produced only by this
  adapter; the bot enforces comparable rules (its own per-chat limiter, the
  allowlist and the owner gate) but expresses them as Telegram-worded notices
  that carry no `error_code`. And the two deliberately diverge on one outcome:
  with no model configured and a dictionary miss, the application returns a
  `kind == "llm"` result holding term-substituted source text, which the bot
  renders and this adapter refuses as `llm_unavailable`, because over HTTP the
  fallback would read as a successful translation.
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
  (`scripts/check_architecture_boundaries.py` `check_api_package`). The gate
  reads import statements, so what it guarantees is dependency **direction**:
  the adapter cannot reach into the domain modules and drift from them. It does
  not inspect behavior — an adapter that used `sqlite3` or `httpx` to build its
  own lookup or its own model call would pass — so "one pipeline" is a gate for
  the accidental case and a review question for the deliberate one.
- **Packaging: the public wheel now ships the adapter.** `wuwaterm_api` is
  included in the wheel and sdist, and `wuwaterm-api = wuwaterm_api.cli:main` is
  a console entry point. This is explicitly accepted rather than worked around
  with a second distribution: the distribution boundary that matters is "no
  generated database, no game data, no runtime state", and adapter source code
  does not touch it. `scripts/check_package_artifacts.py` **requires** the
  `wuwaterm_api` members in both artifacts and the `wuwaterm-api` entry point in
  the wheel's `entry_points.txt` (an sdist carries no generated entry-point
  metadata to audit; CI installs the built sdist into a clean virtualenv and
  runs `wuwaterm-api --help` instead). A packaging change that drops the package
  therefore fails a gate rather than silently shipping an entry point that
  cannot import itself.

Operational placement (loopback bind, separate state directory, device
credentials) is decided in [ADR 0010](0010-device-principal-auth.md) and
`docs/deployment.md`.

## Consequences

- Positive: the HTTP adapter runs the same dictionary-first pipeline as the
  Telegram commands, and the cheap way to diverge from it — importing `lookup`
  or `sentence` and going around `application` — fails the boundary guard rather
  than a reviewer's attention.
- Limit, recorded so it is not mistaken for a guarantee: "exactly once" is true
  of the command and HTTP paths, not of the whole system. The linked-channel
  adapter (`wuwaterm/channel.py`) still has its own direction, exact-lookup and
  translate sequence and does not call `application` at all
  (`docs/architecture.md`, "Current coupling"). And the guard checks imports,
  not behavior, so it cannot stop an adapter that reimplements the pipeline out
  of `sqlite3` and `httpx`.
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
  HTTP contract or the snapshot. Only part of that is machine-checked:
  `scripts/check_api_contract.py` re-applies the same four product token bans
  `scripts/check_non_goals.py` uses — the callback-registration family, the
  inline-mode handler, the name-mapping term and the free-text listener — to the
  artifact, and nothing more. Markup, Telegram vocabulary in general and
  chat-identifier fields are held by review and by the plain-text-only
  construction: a `<b>` inside a description, or a chat-identifier property,
  would pass the contract gate today.

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
