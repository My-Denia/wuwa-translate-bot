# Architecture

Maintainer entry for the **current** production Wuthering Waves terminology
service (`wuwaterm`). Claims map to source, tests, deploy scripts, and Compose
topology. This is not an aspirational redesign.

The service has two inbound adapters over one application layer: the Telegram
bot and a loopback-only HTTP API. Operational detail lives in sibling guides.
Decision rationale lives in [ADRs](adr/). Automated import-direction, contract
and non-goal gates live under `scripts/` and CI.

## System context

```
Telegram users / groups / linked        Owner desktop (WuwaTerm client)
discussion groups                               │
        │                                       │  SSH -L tunnel to loopback
        │  Bot API (long polling getUpdates)    │  (no public port published)
        ▼                                       ▼
┌───────────────────────────────┐   ┌───────────────────────────────┐
│ Compose: wuwaterm-bot         │   │ Compose: wuwaterm-api         │
│ commands: bot.py              │   │ adapter: src/wuwaterm_api/    │
│ linked channel: channel.py    │   │ injects no markup translator  │
│   (its own translate path)    │   │ (plain text only)             │
│ injects markup translator +   │   │ state-api/ read-write         │
│ UTF-16 splitter               │   │                               │
│ state/ read-write             │   │                               │
└───────────────┬───────────────┘   └───────────────┬───────────────┘
                │ commands only                     │
                └──────────► wuwaterm.application ◄─┘
                             (dictionary-first pipeline for
                              Telegram commands + HTTP)
                                    │            │
                 lookup / sentence / normalize   │ HTTP (optional)
                       ▲            ▼            ▼
   channel.py ─────────┘     data/terms.db   OpenAI-compatible LLM
   (direct, not via     (read-only in both containers)
    application)                    ▲
                                    │ promote only via deploy/vps-update.sh
┌───────────────────────────────────┴───────────┐
│  Builder jobs (Compose profile: builder)      │
│  refresh-data → build-db → verify-*           │
│  never holds runtime secrets (no env_file)    │
└───────────────────────────────────────────────┘
```

### Users and actors

| Actor | Role | Evidence |
|-------|------|----------|
| Owner (`OWNER_USER_ID`) | Private `/tr`, `/authorize` / `/revoke`, `/status`; passes the translate gate in any authorized chat without an admin check | `bot.py` `_is_owner`, `authorize_command`, `revoke_command`, `status_command`, `_translation_actor` |
| Group admins | `/tr` when chat is allowlisted (unless public mode); `/public` on/off | `bot.py` `_is_authorized_group_sender`, `_is_group_admin`, `public_command`, `ChatSettings` |
| Group members | `/tr` only when public mode is on for that chat | `settings.py` public map; `docs/telegram-behavior.md` |
| Linked channel posts | Auto-translated into the discussion group when gated | `channel.py` `channel_post_handler`; single channel auto-forward listener pin in `bot.py` |
| API device | Bearer-authenticated principal for `POST /v1/translations`, `GET /v1/terms`, `GET /v1/meta` | `wuwaterm_api/auth.py` `DeviceStore`, `wuwaterm_api/app.py` `authenticated_device` |
| Operator on VPS | Deploy, data refresh, Compose up/down, device registration and revocation | `deploy/vps-update.sh`, `wuwaterm_api/cli.py`, `docs/deployment.md` |

`/public` is **not** an owner command. `public_command` works only in a group
and authorizes on a fresh, uncached `_is_group_admin` check; it never reads
`OWNER_USER_ID`. So any admin of that group may toggle public mode, and a
configured owner who is not an admin of that group may not. The commands that
do gate on the owner identity are `/authorize`, `/revoke` and `/status`.

### External systems

| System | Direction | Purpose |
|--------|-----------|---------|
| Telegram Bot API | Outbound long polling + send/edit/delete | Telegram adapter only |
| Owner desktop client (`client/`) | Inbound over an SSH tunnel to the loopback port | Downstream consumer of the HTTP contract |
| OpenAI-compatible HTTP API | Outbound when LLM configured and dictionary miss | Sentence translation after term lock |
| GitHub game-data repos | Builder sparse checkout only | Source for `terms.db` (`data_source.py`, `constants.py` pins) |
| Local Docker Compose host | Runtime containers + optional builder jobs | Supported single-host topology |

### Trust boundaries

1. **Inbound text is untrusted input on both adapters.** Command text, replied
   message HTML, channel posts and HTTP request bodies are treated as data, not
   instructions (`docs/privacy-and-llm.md`; sentence system prompt + HTML
   protect/restore). The HTTP adapter additionally bounds body size and body
   arrival time before a handler ever sees the payload
   (`wuwaterm_api/app.py` `BodyLimitMiddleware`).
2. **Secrets stay outside the image and the builder.** Runtime gets
   `TELEGRAM_BOT_TOKEN` / LLM keys via Compose `env_file`; builder has no
   `env_file` (`deploy/docker-compose.yml`, `docs/deployment.md`).
3. **Bot-only secrets are blanked for the API container.** The two services
   share one `env_file`, so `TELEGRAM_BOT_TOKEN`, `TELEGRAM_TEST_CHAT_ID`,
   `OWNER_USER_ID` and `WUWATERM_REDACTION_SECRET` are explicitly set to empty
   strings in the `wuwaterm-api` service. An environment dump from the API
   process cannot yield the chat token, the owner id, or the key that hashes
   the bot's chat ids (`deploy/docker-compose.yml`).
4. **Terminology DB is trusted after candidate verification**, then mounted
   read-only in **both** serving containers (`../data:/app/data:ro`). Promotion
   is owner-scripted, not in-process self-update.
5. **The credential store is reachable only by the API process.** Devices live
   in `state-api/devices.db`, and `state-api/` is a **sibling** of `state/`,
   not a child of it: the bot mounts the whole of `state/` read-write, so a
   child directory would have handed the bot process read-write access to the
   credential store. Only the `wuwaterm-api` service mounts `../state-api`
   (`deploy/docker-compose.yml`; `tests/test_deploy_scripts.py`
   `test_api_state_directory_is_not_inside_the_bot_state_tree`).
   `WUWATERM_API_DEVICE_DB_PATH` is pinned to empty in that service so a
   one-off container cannot redirect the store somewhere the serving container
   never reads.
6. **Mutable allowlist / reply-index state is local and private.** Paths under
   `state/`; must not be committed (`scripts/check_repo_hygiene.py`).
7. **The API adds no public surface.** `WUWATERM_API_BIND` is fixed to
   `127.0.0.1` **in the Compose file**, not interpolated from `.env`. Under
   `network_mode: host` an environment knob would have made public exposure a
   one-line edit to a file operators change in a hurry; as a literal it is a
   reviewed ingress decision. No ports are published. The supported transport
   from the owner desktop is an SSH tunnel to that loopback port
   (`docs/deployment.md`).
8. **Inbound control planes are the Telegram Bot API and the HTTP surface.**
   Everything that needs a credential is under `/v1`; the three routes outside
   it — `GET /healthz`, `GET /readyz` and the FastAPI-generated
   `GET /openapi.json` — are unauthenticated by design and expose no data that
   is not already public in this repository (see
   [the complete route list](#the-complete-inbound-route-list)). There is no web
   admin, no self-service registration route, and no inbound listener for
   Telegram updates. Device registration and revocation require shell access on
   the host over SSH (`wuwaterm_api/cli.py`).

## Inbound adapters over one application layer

Telegram and HTTP are UI and transport, not the domain model. Each adapter owns
its own wording, auth model and delivery mechanics. Neither the Telegram command
handlers nor the HTTP API owns translation behavior — they delegate it to the
application layer. The linked-channel adapter is the exception and owns its own,
which is why it is called out below rather than folded into "the adapters".

| Adapter | Responsibility | Module |
|---------|----------------|--------|
| Telegram command bot | Handlers, auth, per-chat rate limits, replies, polling bootstrap | `src/wuwaterm/bot.py` |
| Telegram linked channel | Admission, **its own** direction/exact/translate sequence, deliver/edit, flood retry | `src/wuwaterm/channel.py` |
| Telegram HTML/text helpers | Protect/validate Telegram HTML; chunk limits | `telegram_html.py`, `telegram_text.py` |
| HTTP API | Routing, bearer device auth, per-device rate limit, body cap, time budget, stable error envelope | `src/wuwaterm_api/app.py`, `auth.py`, `errors.py`, `settings.py` |
| HTTP operator CLI | `serve`, `device issue` / `device list` / `device revoke` | `src/wuwaterm_api/cli.py` |

The application layer holds the dictionary-first pipeline (prepare → direction →
exact hit → trusted fuzzy hit → length gate → chunked, term-locked LLM call) and
knows nothing about Telegram, HTTP, chats, users or markup formats.

**Two of the three adapters go through it**: the Telegram command handlers
(`/tr`, `/term`, `/sentence`, via `bot.translate_query*` and
`telegram_translation_reply`) and the HTTP API (`POST /v1/translations`).

**The linked-channel adapter does not.** `channel.py` imports `TermService` and
`SentenceTranslator` directly and runs its own CJK/Latin direction threshold,
its own `lookup_exact` branch and its own call to the translator; it produces no
`TranslationOutcome` and never reaches the fuzzy stage. It is a partial
duplicate of the pipeline that predates the extraction and was deliberately left
alone by it: its translate step is interleaved with admission, reply-index
claiming and chunk editing, so moving it is a behavior-risking refactor of the
one path that writes to Telegram unprompted, not a lift-and-shift. The
consequence a maintainer must hold: **a change to `application.py` does not
automatically change linked-channel translations**, and a pipeline change that
must apply everywhere has to be made in `channel.py` too. See
[Current coupling](#current-coupling-honest).

The adapter-shaped steps of the shared pipeline are **injected by the caller**:

| Seam | Type | Telegram adapter | HTTP adapter |
|------|------|------------------|--------------|
| `markup_translator` | adapter-supplied async callable | injects `bot._telegram_markup_translator` (HTML term-lock, validate/strip, plain fallback) | **injects nothing** — the contract is plain text only |
| `splitter` | `(text, limit) -> chunks` | injects `bot._telegram_llm_splitter` (UTF-16 aware `split_telegram_text`) | leaves the default `application.split_plain_text` |
| `before_llm_call` | synchronous guard | not used | injects its per-minute `LlmCallBudget` |
| `offload` | how the blocking dictionary stage runs | not used (one loop already serialises handler work) | injects `asyncio.to_thread` so one request cannot stall the shared loop |

Adapters receive a `TranslationOutcome` (`kind`, `text`, `direction`,
`dictionary_miss`, `error_code`) and own their own wording, so protocol-specific
notices never leak downward. The HTTP adapter maps `error_code` to an HTTP
status and a short operator-facing message of its own; the bot's Telegram-worded
notices are never reused over HTTP (`wuwaterm_api/errors.py`).

Domain (`lookup`, `normalize`, `models`, term-lock policy in `sentence`) and
storage (`db` reads, `settings`, `channel_reply_index`) must remain usable from
CLI and tests without a live Telegram session or a running HTTP server. See
[ADR 0001](adr/0001-telegram-as-presentation-layer.md) and
[ADR 0009](adr/0009-http-api-adapter.md).

## Component map and dependency direction

Intended layers (modular monolith — [ADR 0002](adr/0002-modular-monolith.md)):

```
cli (bootstrap)
  ├─► bot / channel          presentation (python-telegram-bot)
  │     ├─► application                                          shared pipeline
  │     │     └─► lookup / sentence / normalize / translation_policy   domain
  │     ├─► settings / channel_reply_* / channel_runtime         local infra
  │     └─► telegram_html / telegram_text                        presentation helpers
  ├─► builder / data_source / db (write) / build_pinyin           builder path
  └─► lookup / sentence / db (read)                              offline CLI

wuwaterm_api.cli (bootstrap)
  └─► wuwaterm_api.app / auth / errors / settings   HTTP adapter (separate package)
        └─► application / models / translation_policy / logging_utils   ONLY
```

| Layer | Modules | Must not import |
|-------|---------|-----------------|
| Domain core | `lookup`, `normalize`, `models` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Domain + provider | `sentence` | `bot`, `channel` (may use `telegram_html` for HTML term-lock); builder-only modules |
| Application | `application` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Shared policy | `translation_policy`, `runtime_keys`, `constants` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Presentation | `bot`, `channel`, `telegram_html`, `telegram_text` | `builder`, `data_source`, `build_pinyin`, bootstrap `cli` |
| Local state | `settings`, `channel_reply_index`, `channel_reply_schema`, `channel_runtime`, `logging_utils` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Storage | `db` | presentation / Telegram SDK; `build_pinyin` only inside write helpers (lazy) |
| Builder | `builder`, `data_source`, `build_pinyin` | `bot`, `channel` |
| Bootstrap | `cli` | may wire both runtime and builder entrypoints |
| HTTP adapter (`src/wuwaterm_api`) | `app`, `auth`, `errors`, `settings`, `cli` | everything in `wuwaterm` except the four allowlisted modules below; the `wuwaterm` package root; the Telegram SDK |

### The HTTP adapter import allowlist

`src/wuwaterm_api` is a separate top-level package, and it may import **only**
these four modules from `wuwaterm`:

- `wuwaterm.application`
- `wuwaterm.models`
- `wuwaterm.translation_policy`
- `wuwaterm.logging_utils`

It may not import `bot`, `channel`, `telegram_html`, `telegram_text`,
`sentence`, `lookup`, `db`, any builder module, the bare `wuwaterm` package
root, or the Telegram SDK. `TYPE_CHECKING` is not an exemption: a type-only
import of the bot would still document a dependency the adapter is not allowed
to have.

The allowlist is enforced by `scripts/check_architecture_boundaries.py`
(`API_ALLOWED_WUWATERM_MODULES`, `check_api_package`) and covered by
`tests/test_architecture_boundaries.py`.

**What that gate does and does not prove.** It parses each file under
`src/wuwaterm_api/` and inspects import statements only: it fails on any
`wuwaterm.*` import outside the four allowlisted modules, on the bare `wuwaterm`
package root, and on the Telegram SDK. It says nothing about any other import
and nothing about what the code does — `sqlite3`, `httpx` and everything else in
the dependency set are unexamined. So the gate makes the *easy* divergence
impossible: the adapter cannot quietly start calling `lookup` or `sentence` and
drift from them. It does **not** make a second pipeline impossible: an adapter
that opened `terms.db` through `sqlite3` itself, or posted to the model endpoint
through `httpx` itself, would pass the gate. That form of divergence is caught by
review, not by a script.

To make the allowlist survivable, `application.py` exposes what an adapter
needs without reaching deeper: `build_term_service`, `build_translator`,
`lookup_exact_terms`, `service_metadata`, `probe_database`, `llm_configured`,
the stable error-code constants, `SlidingWindowRateLimiter` and `LlmCallBudget`.

Observed edges (static imports among `src/wuwaterm/*.py` and
`src/wuwaterm_api/*.py`):

- `bot` → `channel` (handler registration + flood retry); `channel` → `bot`
  only under `TYPE_CHECKING` for `BotConfig` (no runtime cycle).
- `bot` → `application` (shared pipeline + rate limiter); `application` imports
  no presentation module and no Telegram SDK, enforced by the boundary guard.
- `wuwaterm_api.app` → `wuwaterm.application`, `wuwaterm.logging_utils`;
  `wuwaterm_api.errors` → `wuwaterm.application`. Nothing else in
  `wuwaterm_api` reaches into `wuwaterm` at all.
- `sentence` → `lookup`, `normalize`, `telegram_html`, `httpx`.
- `lookup` → `db` (read helpers), `models`, `normalize`, `constants`.
- `db.insert_records` lazily imports `build_pinyin` (builder-only dependency).
- Runtime image refuses `build-db` and excludes builder tooling; it serves
  `bot`, `api` and `device` only (`tests/test_runtime_imports.py`,
  `deploy/entrypoint.sh`, CI `deploy-boundary` job).

Automated enforcement: `scripts/check_architecture_boundaries.py`,
`scripts/check_api_contract.py`, `scripts/check_non_goals.py`,
`scripts/check_package_artifacts.py`, and `tests/test_runtime_imports.py`.

### Current coupling (honest)

| Coupling | Status | Notes |
|----------|--------|-------|
| Large `bot.py` (config, auth, rate limit, translate orchestration, migration, polling) | Accepted concentration | Documented; not mass-split in this formalization |
| `translate_query*` in `bot.py` are thin wrappers over `application` | Resolved | The pipeline itself lives in `application.py`; `bot.py` only adds Telegram wording, HTML parse mode and the UTF-16 splitter |
| `channel.py` keeps its own direction/exact/translate sequence instead of calling `application` | **Open, accepted for now** | It uses `TermService` and `SentenceTranslator` directly, so pipeline changes do not reach linked-channel posts automatically. Not extracted here because the translate step is interleaved with admission, reply-index claiming and chunk editing. Trigger for extracting it: any change that must apply to *every* translation path, or a divergence between command and channel output that a user notices |
| `channel` TYPE_CHECKING-imports `BotConfig` from `bot` | Low aesthetic cycle | Runtime import graph is one-way; extraction deferred unless a check requires it |
| `sentence` → `telegram_html` | Accepted | HTML term-lock is part of Telegram-HTML translation fidelity; the HTTP adapter never reaches it |
| `db` top-level imports `data_source.SourceProvenance` | Mild storage/builder type coupling | Provenance metadata is shared with build path |
| The public wheel ships `wuwaterm_api` and a `wuwaterm-api` entry point | Explicitly accepted | The distribution boundary that matters (no DB, no TextMap, no game data, no state) is unaffected; `scripts/check_package_artifacts.py` requires the package members in both artifacts and the entry point in the wheel, and CI smokes the sdist's console script in a clean virtualenv, so packaging drift stays caught ([ADR 0009](adr/0009-http-api-adapter.md)) |

## Request flows

### Telegram command `/tr` (and `/term`, `/sentence` / `/sent`)

Evidence: `bot.py` `create_application`, `_translation_command`,
`translate_query` / `translate_query_async` (thin wrappers over
`application.translate_request` / `translate_request_async`); tests under
`tests/test_bot.py`.

1. `run_bot` → `create_application` → `app.run_polling()` ([ADR 0003](adr/0003-long-polling-not-webhook.md)).
2. `CommandHandler` for `tr`/`term` or `sentence`/`sent` → `_translation_command`.
3. **Auth**: owner private chat, or group admin (or public mode) + allowlist
   (`_translation_actor_or_reject`, `ChatSettings`).
4. **Rate limit**: per-chat `PerChatRateLimiter` (the shared
   `application.SlidingWindowRateLimiter` keyed by chat id).
5. Parse args / optional `--to` / replied text; invalid `--to` → usage, **no LLM**.
6. `application.translate_request_async` with the Telegram markup translator and
   UTF-16 splitter injected.
7. `reply_to_user` (HTML with plain fallback; flood retry via channel helper).

### Linked-channel auto-translation

Evidence: `channel.py` `channel_post_handler`; single listener pin in
`scripts/check_non_goals.py` / `bot.py`.

1. Update matches `filters.IS_AUTOMATIC_FORWARD & filters.SenderChat.CHANNEL`.
2. Age / size / CJK-Latin thresholds; kill switch `WUWATERM_CHANNEL_AUTOTRANSLATE`.
3. Auth: discussion group must still satisfy allowlist gate.
4. `ChannelRuntime.reserve` (process-local admission / budget).
5. Claim original in `ChannelReplyIndex` (file-backed).
6. Translate: exact DB / term lock / LLM with re-gates before send.
7. Deliver reply or edit existing chunks with `send_with_flood_retry`.
8. Telemetry outcomes via channel runtime counters.

Channel path is always auto-detected direction; it does not accept command
`--to` flags (`docs/telegram-behavior.md`).

### HTTP `POST /v1/translations`

Evidence: `wuwaterm_api/app.py`; `tests/test_api.py`; `docs/api/openapi.json`.

1. `RequestIdMiddleware` accepts a caller-supplied `X-Request-Id` only when it
   matches `^[A-Za-z0-9._-]{1,64}$`, otherwise mints one; the id is echoed on
   every response and is included in every log line the HTTP adapter emits
   (see [Request-id coverage](#request-id-coverage) for where it stops).
2. `BodyLimitMiddleware` refuses a declared or streamed body over
   `WUWATERM_API_MAX_BODY_BYTES` (`payload_too_large`) and gives the body read
   its own deadline of `WUWATERM_API_REQUEST_TIMEOUT_SECONDS`.
3. `TimeoutMiddleware` then gives the handler a **second, independent** deadline
   of the same length. Either deadline answers `504` with code `internal`.
   These are two consecutive budgets, not one: the body read completes before
   the handler deadline starts, so a slow upload followed by a slow handler can
   consume close to **twice** `WUWATERM_API_REQUEST_TIMEOUT_SECONDS` of
   wall-clock. Nothing in the adapter bounds the two together.
4. **Auth**: bearer `wtd1.<device_id>.<secret>` verified against
   `state-api/devices.db` under a bounded number of concurrent verifications.
   Every rejection reason is indistinguishable (`unauthorized`).
5. **Rate limit**: per-device sliding window
   (`WUWATERM_API_RATE_LIMIT_PER_MINUTE`), applied before `last_used_at` is
   stamped so a refused caller cannot drive writes into the credential store.
6. **Scope**: `translate` for translations, `meta` for `/v1/terms` and
   `/v1/meta`; a missing scope is `forbidden`.
7. `application.translate_request_async` runs the same pipeline, with the
   dictionary stage offloaded to a worker thread and the per-minute
   `LlmCallBudget` as the pre-call guard. No markup translator is injected.
8. A `kind == "llm"` outcome with no model configured is refused as
   `llm_unavailable` rather than returning term-substituted source text: over
   HTTP that would look like a successful translation.
9. Response: `{kind, text, direction, dictionary_miss, request_id}` — plain
   text, no markup.

`GET /healthz` (liveness) and `GET /readyz` (dictionary readable) take no
credential. `GET /v1/meta` reports service version, API version, schema
version, source profile, source commit, term count and `llm_configured`, and
deliberately excludes filesystem paths, credentials and chat identifiers
(`application.ServiceMetadata`).

### The complete inbound route list

Six routes, not five. Three carry a credential, three do not:

| Route | Versioned | Credential | Scope |
|-------|-----------|------------|-------|
| `POST /v1/translations` | yes | bearer device | `translate` |
| `GET /v1/terms` | yes | bearer device | `meta` |
| `GET /v1/meta` | yes | bearer device | `meta` |
| `GET /healthz` | no | none | — |
| `GET /readyz` | no | none | — |
| `GET /openapi.json` | no | none | — |

`GET /openapi.json` is generated by FastAPI itself, not registered by
`_register_routes`, and it is easy to forget when reasoning about the trust
boundary. It is deliberate: `openapi_url="/openapi.json"` is kept while
`docs_url` and `redoc_url` are both `None`, so the machine-readable contract is
served but no interactive documentation UI is. It discloses the same *document*
that is already committed at `docs/api/openapi.json` in a public repository, so
it adds no disclosure — not the same bytes, though: the snapshot is written with
sorted keys and two-space indentation while the endpoint serializes through
FastAPI, so do not checksum one against the other. It is an unauthenticated
route reachable by anything that can reach the loopback port, and it is part of
the surface an ingress decision would expose.

One more thing a client should know: the router's trailing-slash redirect is the
documented exception to the error envelope. `GET /healthz/` answers `307` with a
`Location` header and an empty body rather than the JSON envelope, because that
response never reaches an exception handler ([ADR 0009](adr/0009-http-api-adapter.md)).

### Request-id coverage

The request id is threaded by hand, not by a context variable or a logging
adapter, and it stops at the package boundary:

- **Carries the id**: every log line emitted by `src/wuwaterm_api/app.py` — body
  and handler timeouts, auth rejection, rate-limit rejection, unhandled
  exceptions, the translation result line, and the no-model refusal. Each call
  site passes `_request_id(request)` explicitly.
- **Does not carry the id**: log lines emitted from inside
  `src/wuwaterm/application.py` while serving that request — notably
  `llm translation failed reason=…` (`_llm_failure_outcome`), the unknown
  markup-error-code warning, and the readiness-probe warning
  (`probe_database`). The application layer is protocol-neutral and receives no
  request context.

So the practically important case — an LLM failure behind a `503` — leaves
an adapter line that has the id and a cause line that does not; correlating them
means using the timestamp. Making that automatic would mean putting a
request-scoped context variable into the shared layer, which is a change to
`application.py`'s contract and is not made here.

### Paths that never call the LLM

| Path | Why |
|------|-----|
| Exact dictionary hit (`lookup_exact` early return in `application._dictionary_stage`, channel exact branch) | Official string from SQLite |
| Fuzzy dictionary short answers (`application._fuzzy_dictionary_answer`) | DB-only |
| Invalid leading `--to` (Telegram) | Usage reply only |
| Unauthorized / rate-limited / silent reject (both adapters) | No translation work |
| HTTP body over the cap, body read over its deadline, missing scope | Refused before the handler runs |
| Channel below CJK/Latin thresholds, stale posts, admission reject, kill switch off | Skipped before translate |
| LLM env incomplete | Telegram: `SentenceTranslator` restores locked placeholders / fails closed without inventing terms. HTTP: `llm_unavailable` |
| `/about`, `/status`, authorize/revoke/public membership housekeeping, `/healthz`, `/readyz`, `/v1/terms`, `/v1/meta`, `/openapi.json` | No translation |

**The handler timeout is deliberately not in that table.** `TimeoutMiddleware`
fires *around* the handler, so by the time it fires the request has already
entered `translate_request_async` and may already have sent a request to the
model. Cancellation stops the adapter waiting for the answer; it does not
un-send what is already on the wire. A `504` therefore does **not** guarantee
that no LLM call was made or billed. Only the body-read deadline refuses a
request before any pipeline work begins.

## Identity and principals

The two adapters have **separate, non-overlapping** identity models. Neither can
grant access to the other.

| Adapter | Principal | Where it lives | Granted by | Withdrawn by |
|---------|-----------|----------------|------------|--------------|
| Telegram | Chat identity (owner id, group chat id, admin status, public mode) | `state/chat_settings.json`, `OWNER_USER_ID` | `/authorize`, `/public` | `/revoke`, `/public off` |
| HTTP | Device (`device_id` + secret, scopes `translate` / `meta`) | `state-api/devices.db` | operator CLI `wuwaterm-api device issue` over SSH | `wuwaterm-api device revoke` (sets `revoked_at`, keeps the row) |

**The device id IS the principal id today.** There is no users table, no
account system and no mapping from a device to a human. One person may register
several devices; the service cannot tell that they belong to the same person,
and does not need to.

The schema extends to multiple principals later without changing today's rows.
The documented trigger for introducing a separate principals table is:

- a second human user needs their own credentials and their own revocation, or
- quotas or audit have to be attributed per person rather than per device.

Until one of those is real, a principals table would be ceremony. **It is not
implemented** (`src/wuwaterm_api/auth.py` module docstring;
[ADR 0010](adr/0010-device-principal-auth.md)).

## Cost topology

Every budget in this system is **per process**. Nothing is shared across
processes, and no in-process counter is a global one.

| Budget | `wuwaterm-bot` | `wuwaterm-api` | Documented worst case |
|--------|----------------|----------------|-----------------------|
| Simultaneous LLM calls | `WUWATERM_LLM_MAX_CONCURRENCY`, default 4 | `WUWATERM_API_LLM_MAX_CONCURRENCY`, default 2 | **sum = 6** |
| LLM calls per minute | not capped separately (bounded by concurrency, per-chat limits and channel admission) | `WUWATERM_API_LLM_CALLS_PER_MINUTE`, default 30 | per process only |
| Requests per minute | `WUWATERM_RATE_LIMIT_PER_MINUTE`, default 10, per chat | `WUWATERM_API_RATE_LIMIT_PER_MINUTE`, default 30, per device | per process only |
| Credential verifications in flight | n/a | `WUWATERM_API_AUTH_MAX_CONCURRENCY`, default 2 | per process only |
| Channel admission / dedup | `ChannelRuntime`, in memory | n/a | per process only |

Each adapter builds its own `SentenceTranslator` through
`application.build_translator`, so the concurrency semaphore is per instance
(`application.build_translator` docstring). `LlmCallBudget` and
`SlidingWindowRateLimiter` are in-process data structures; two processes do not
share them, and restarting a process resets them.

Consequences a maintainer must keep in mind:

- The worst-case outbound LLM concurrency for the host is the **sum** of the
  per-process caps, not either number on its own. Sizing an upstream quota
  against 4 or against 2 is wrong; size it against 6.
- Raising `WUWATERM_API_LLM_MAX_CONCURRENCY` raises the host total. There is no
  ceiling that clamps the two processes together.
- A shared cross-process budget is deliberately **not built**. Single-owner load
  does not justify the coordination. The trigger for building one is a second
  human user of the API **or** an observed breach of the upstream quota that the
  per-process caps failed to prevent.

## Data model: immutable vs mutable

| Kind | Location | Mutability | Notes |
|------|----------|------------|-------|
| Terminology SQLite | `data/terms.db` (Compose: `/app/data` **ro** in both serving containers) | Immutable at runtime | Built offline; promoted by `deploy/vps-update.sh` ([ADR 0004](adr/0004-sqlite-terminology-data.md), [ADR 0008](adr/0008-candidate-verification-and-transactional-deployment.md)) |
| Chat allowlist + public mode | `state/chat_settings.json` | Mutable | Process `RLock` + `fcntl`/`msvcrt` file lock ([ADR 0005](adr/0005-file-backed-single-instance-state.md)) |
| Channel reply index | `state/channel_replies.json` | Mutable | Atomic replace + asyncio edit locks |
| Device credential store | `state-api/devices.db` (+ `-wal`, `-shm`) | Mutable | Created `0600` inside a `0700` directory; only the API container mounts it ([ADR 0010](adr/0010-device-principal-auth.md)) |
| Channel admission / budgets | `ChannelRuntime` in process memory | Mutable, lost on restart | Not shared across processes |
| Rate limiters, LLM call budget, admin cache | Process memory in `bot.py` / `wuwaterm_api/app.py` | Mutable, lost on restart | Per process, never shared |
| Source pins / profile | `constants.py` + DB metadata | Immutable until rebuild | |
| Secrets | Host `.env` mode `600` | Operator-managed | Not in image; not in builder; bot-only values blanked for the API service |

## Single-instance assumptions

Supported topology (also [supported vs unsupported](#supported-vs-unsupported-topology)):

- One Bot token (`TELEGRAM_BOT_TOKEN`).
- One active long-polling runtime container (`container_name: wuwaterm-bot`).
- One API container (`container_name: wuwaterm-api`) on the same host, bound to
  loopback, serving from the same read-only `terms.db`.
- One Compose host; `network_mode: host` for both services.
- Builder is profile-gated one-shot work, not a second always-on replica.

The API container is a second **process**, not a second instance of the bot. It
holds no Telegram token, opens no Telegram connection, and writes none of the
bot's state files.

### Dual-instance failure modes (unsupported)

If two processes share the same token and state files:

1. **getUpdates contention** — Telegram effectively serves one consumer; offsets
   race and updates are lost or double-handled.
2. **Allowlist / public split-brain** — file locks reduce corrupt JSON writes
   but not logical divergence between two writers with interleaved intent.
3. **Double channel work** — `ChannelRuntime` budgets and in-memory dedup are
   process-local → duplicate LLM spend and duplicate replies.
4. **Reply-index races** — claim/edit windows can conflict across processes even
   when individual file replaces are atomic.

Running two API containers against one `state-api/devices.db` is equally
unsupported: SQLite WAL tolerates the concurrent readers, but the per-device
rate limit and the per-minute LLM budget would each be enforced twice
independently, so the effective limits would double silently.

Docs and ops must not treat multi-instance as HA. File locks are durability
aids for a single writer, not a cluster protocol.

## Failure propagation

| Dependency | Failure mode | User-visible / recovery |
|------------|--------------|-------------------------|
| Telegram API | NetworkError, RetryAfter, BadRequest | Flood retry helper; HTML parse fallback to plain; missing reply target handling; process keeps polling (`restart: unless-stopped`) |
| LLM HTTP | Timeout, 4xx/5xx, empty content | `LLMTranslationError` mapped to a stable `error_code`; Telegram renders its notice, HTTP answers `llm_unavailable` / `llm_budget_exhausted`; placeholders not left raw when restore fails closed |
| SQLite terms DB | Missing/corrupt file | Startup/use fails when `TermService` cannot read; `GET /readyz` answers 503; deploy keeps previous DB on failed promotion |
| Device credential store — at startup | Missing / older shape / unreadable | `cli._serve` calls `DeviceStore.initialize()` **before** uvicorn binds. Missing is recreated empty and the service starts. An older-shape store raises `DeviceStoreError`, which `cli.main` catches and prints as a curated `error: …`. A file that is not a valid SQLite database at all raises `sqlite3.DatabaseError`, which `cli.main` does not catch, so it aborts on a raw traceback instead. Either way there is no listener and therefore no request to answer — nothing degrades silently |
| Device credential store — while serving, during authentication | Becomes unreadable, deleted, or corrupted | A uniform `unauthorized` (401), whatever the underlying cause: `DeviceStore.authenticate` folds every store error into "no device". The reason is not on the wire by design; the operator CLI is where a store is diagnosed |
| Device credential store — while serving, after authentication | Store fails on the `last_used_at` write | Not a 401. `record_use` runs unguarded after a credential has already verified, so a store that breaks in that window surfaces as the generic `internal` (500), not as `unauthorized`. The uniform-rejection property covers the verification path, not the bookkeeping write that follows it |
| Filesystem state | Disk full, lock errors, durability errors | Settings/reply-index raise typed errors; startup migration refuses overwrite of existing targets |
| SSH tunnel down (desktop client) | Connect refused / timeout | Client renders its own offline or timeout state; the service is unaffected |
| Docker / Compose | Image/build failure, wrong revision | `vps-update.sh` aborts before stop, or rolls back image + DB + pointer after failed post-promote steps, for **both** serving containers |

No automatic cross-region failover. Recovery is restart, restore from
timestamped backups under `data/deployment-backups`, or re-run transactional
deploy after fixing the root cause. Break-glass for API access is deleting
`state-api/devices.db`, which revokes every device at once; the next start
recreates an empty store.

## Data refresh and transactional deploy

From repo scripts and docs only (no live VPS mutation required to understand):

1. **Refresh** — `wuwaterm-builder refresh-data` sparse-checkouts pinned source
   (`data_source.refresh_data`, `docs/data-refresh.md`).
2. **Build candidate** — `build-db --atomic` → unique path under
   `data/candidates/` (updater), not live `terms.db`.
3. **Verify** — `verify-db`, seed/exact/idempotent scripts as wired in
   `deploy/vps-update.sh` and `docs/validation.md`.
4. **Build immutable runtime image** labeled with source commit revision. One
   image serves both `bot` and `api`.
5. **Promote** — stop both serving containers, swap DB, start the exact image,
   smoke: `scripts/deploy_smoke.py` for the bot (diagnostic send disabled in the
   updater path) and an in-container loopback `GET /readyz` for the API. Write
   immutable `.deployments/<commit>.json`, atomic `.deploy_commit`.
6. **Rollback** — on post-promote failure restore previous DB, rollback image
   tag, and pointer (`deploy/vps-update.sh` `rollback_on_failure`). The API
   surface is restarted only on hosts that were already running it, so a host
   still on a pre-API deployment is not handed a new container by a rollback.

Runtime vs builder separation: [ADR 0007](adr/0007-runtime-builder-separation.md).
Dictionary-before-LLM: [ADR 0006](adr/0006-dictionary-first-before-llm.md).

## Downstream consumer: the desktop client

`client/` holds an owner-only Windows desktop client
([ADR 0011](adr/0011-pc-client-stack.md)). It is a **consumer of the HTTP
contract and nothing more**:

- It contains no translation logic: no dictionary lookup, no direction
  detection, no term locking, no chunking. Every value it displays comes from a
  response documented in `docs/api/openapi.json`
  (`client/src/wuwaterm_client/api.py`); it formats and labels those values for
  the screen — a rounded score, a yes/no for `llm_configured`, a label for
  `kind` — but computes none of them.
- It contains no Telegram concepts. User-facing text is meant to live in
  `client/src/wuwaterm_client/strings.py`, and
  `client/tests/test_ui_strings_source.py` enforces the common case statically:
  a literal passed directly to one of the named text-setting methods fails. A
  literal buried inside an expression handed to such a method, or passed to a
  widget constructor, is not covered ([ADR 0011](adr/0011-pc-client-stack.md)).
- It stores exactly one credential, in the Windows Credential Manager via
  `keyring`, and `credentials.py` is the only module that talks to the
  credential store — though the dialogs, the settings view and the API wrapper
  all handle the token value itself. The config file holds only `base_url` and
  timeouts (`client/tests/test_config.py`).
- It is never published to any package index, and it is not part of the
  `wuwaterm` wheel: packaging only reads `src/`, `client/pyproject.toml`
  carries `Private :: Do Not Upload`, and
  `scripts/check_package_artifacts.py` fails if `wuwaterm_client` ever appears
  in a built distribution.
- It reaches the service over the SSH tunnel only. Plain `http://` is accepted
  only for this machine, because the device token travels in a request header;
  any other host must be `https://`.
- It is built and tested by a `windows-latest` CI job, the only job in this
  repository that is not Linux, and the build runs the artifact's own
  start-up self-check so a build that cannot start fails the build.

A change to the wire contract is therefore a change to `docs/api/openapi.json`,
caught by `scripts/check_api_contract.py` before a client ever sees it.

## Conditions that would justify later extensions

These are **not** current capabilities. They would need a new goal, ADRs, and
likely product/ops changes:

| Extension | Real trigger (examples) |
|-----------|-------------------------|
| Principals / users table | A second human user needs their own credentials and revocation, or quotas/audit must be attributed per person rather than per device |
| Public HTTP ingress (DNS, TLS, reverse-proxy route) | The client must reach the service from a network where an SSH tunnel is not available; requires an owner ingress decision, not a code change |
| Self-service device registration | Registration volume outgrows an operator running one CLI command over SSH |
| Shared cross-process LLM budget | A second API consumer exists, or an observed upstream quota breach that per-process caps failed to prevent |
| Web admin | Operators need bulk allowlist/audit without Telegram commands; multi-owner RBAC |
| Multi-instance / external state | Sustained load exceeds one process **and** Telegram delivery model is redesigned; shared durable admission for channels |
| Postgres / Redis / queue | State or job volume exceeds single-host JSON/SQLite operational comfort; multi-host deploy becomes a requirement |
| Signed client releases / auto-update | The client is distributed to anyone other than the owner |

Until those triggers are real, adding the machinery would be ceremony.

## Supported vs unsupported topology

| Supported | Unsupported |
|-----------|-------------|
| Single VPS, Compose, long polling | Webhook HA, multi-replica polling |
| Single bot token, one active runtime | Multi-token shard, active-active |
| One API container, loopback bind, reached over SSH tunnel | Public API ingress, port publishing, multiple API replicas |
| `data/terms.db` RO + `state/*.json` RW + `state-api/devices.db` RW | Runtime writing game TextMaps or self-promoting DB; bot writing the credential store |
| Operator-registered device credentials over SSH | Self-service registration, sign-up, password reset |
| Builder one-shot jobs | Always-on builder with runtime secrets |
| Dictionary-first + optional LLM | LLM-only glossary, secondary name-map layer, inline mode |
| Plain-text HTTP contract | Telegram HTML or any other markup in the HTTP or client contract |
| Owner-gated deploy from clean `HEAD == origin/main` | Unverified candidate copy over live DB |

### Explicitly unsupported

Named here so nobody has to infer it from an absence:

- No account system, sign-up, password reset, OAuth or session cookies.
- No multi-tenant separation, per-tenant data, organizations or billing.
- No public API endpoint, no DNS record, no TLS termination for this service.
- No self-service device registration endpoint of any kind.
- No shared budget, lease or lock across the two serving processes.
- No Redis, Postgres, Kafka, message queue, Kubernetes or microservice split.
- No markup in the HTTP contract; the client renders plain text only.
- No published desktop client, no code signing, no auto-update channel.
- No new Telegram features introduced by the HTTP adapter; Telegram behavior is
  unchanged by it.

## Related documents

- [Deployment](deployment.md)
- [Telegram Behavior](telegram-behavior.md)
- [Privacy And LLM](privacy-and-llm.md)
- [Data Refresh](data-refresh.md)
- [Validation](validation.md)
- [ADR index](adr/README.md)
- [HTTP contract snapshot](api/openapi.json)
- [Desktop client README](../client/README.md)
