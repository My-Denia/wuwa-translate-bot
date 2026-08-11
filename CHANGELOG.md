# Changelog

All notable source changes for this project are tracked here. This repository
does not distribute generated game data or generated SQLite databases.

## Unreleased

### Maintainer Architecture

- New protocol-neutral application layer (`src/wuwaterm/application.py`) owns
  the dictionary-first translation pipeline exactly once. `bot.py` keeps the
  Telegram wording, HTML parse mode and UTF-16 splitter and delegates the
  pipeline; no intentional Telegram behaviour change.
- Boundary guard gains an `Application` layer that may not import presentation
  modules or the Telegram SDK, even under `TYPE_CHECKING`.

### HTTP Adapter (new)

- New `wuwaterm_api` package: a versioned, plain-text HTTP surface
  (`POST /v1/translations`, `GET /v1/terms`, `GET /v1/meta`, `GET /healthz`,
  `GET /readyz`) served by the same dictionary-first pipeline as the bot.
- Revocable device credentials in their own store (`state-api/devices.db`),
  registered and withdrawn by an operator through the new `wuwaterm-api device`
  commands. The operator supplies the secret on standard input and only a
  salted scrypt verifier is stored: the service never produces or prints
  credential material, so none can reach a log, a terminal recording or a
  captured command output through it.
- Stable error envelope with enumerated codes, request ids, per-device request
  limits, a streaming body-size cap, and a time budget applied to both the body
  read and the handler. Credential verification is itself bounded, so the
  deliberately expensive check cannot become the load.
- Committed contract snapshot `docs/api/openapi.json` with a drift gate
  (`scripts/check_api_contract.py`), which also re-applies the repo's product
  token bans to that JSON artifact.
- The published wheel now ships the `wuwaterm_api` package and a
  `wuwaterm-api` entry point; `scripts/check_package_artifacts.py` requires
  both, so packaging drift stays caught. The `api` extra carries FastAPI and
  uvicorn; the core dependency set is unchanged.
- API budgets are per process and separate from the bot's: its own translator
  instance, its own model concurrency cap (default 2) and its own per-minute
  call budget (default 30).
- The service now writes one structured completion record per request —
  correlation id, method, route, status, duration, and the redacted device
  principal when one authenticated — and `wuwaterm-api serve` installs the
  handler that emits it, on standard error (`WUWATERM_API_LOG_LEVEL`, default
  `INFO`). It had none: every `LOGGER` call in the adapter went nowhere in a
  deployed process, so a request id handed to a client matched nothing on the
  server. The adapter's other diagnostic lines are unchanged and are not part of
  the one-per-request guarantee. The operator subcommands and any program
  importing the application still configure no logging at all.
- Records name the matched route TEMPLATE, or an escaped and truncated target
  when nothing matched, instead of the decoded request path: that value is
  chosen by an unauthenticated caller and is read in a terminal. Credentials,
  raw device ids and request text appear in no record, which is asserted
  against captured records rather than by inspection.

### Desktop Client (new)

- New `client/` tree: a Windows desktop client for the HTTP adapter, with its
  own `pyproject.toml` outside `src/` so it is never part of the `wuwaterm`
  wheel. `scripts/check_package_artifacts.py` fails if `wuwaterm_client` ever
  appears in a built distribution.
- It calls the published contract and renders what comes back: plain-text
  translation with an automatic or forced direction and mid-flight
  cancellation, dictionary lookup, service status, and the error envelope's
  stable codes plus the transport states only a client can know (offline,
  timed out, cancelled). It contains no translation logic and no Telegram
  concept.
- One device credential, held only in the Windows Credential Manager, never
  written to a config file.
- `client/build.ps1` produces a one-folder PyInstaller build and then runs the
  artifact's own start-up self-check, so a build that cannot start fails the
  build. A `windows-latest` CI job runs the client suite, builds through the
  same script and uploads the artifact.

### Deployment

- New Compose service `wuwaterm-api` runs the same runtime image with
  `command: ["api"]`, mounts the terminology database read-only and keeps its
  own writable `state-api/`, a sibling of the bot's state directory rather than
  a child of it, so the bot's read-write state mount cannot reach the
  credential store. It binds loopback only and publishes no ports, so the
  service opens no listener anything outside the host can reach; a desktop
  client reaches it through a path route on an HTTPS site the host already
  serves, and authenticates every call with its own device credential.
- Documented operator commands read `WUWATERM_API_PORT` from the serving
  container instead of assuming the default, so a deployment that configured
  another port is not read back against a closed one.
- The runtime entry point accepts `bot`, `api` and `device` and still exits 64
  for every data-build command. CI asserts both halves.
- `deploy/vps-update.sh` now stops, restarts, smokes and reads back BOTH
  serving containers. The API smoke runs inside its own container over
  loopback, so nothing has to be exposed to run it.
- Rollback restores what was RUNNING, not what merely existed: both surfaces
  record their running state before the deployment, both go down before the
  database is rolled back, and a stop that fails aborts the restoration
  entirely rather than reverting a database underneath a container that may
  still be serving it.
- The device store refuses to start an empty store at the new
  `state-api/devices.db` while an older `state/api/devices.db` still holds the
  verifiers; that would have looked like a clean start while every registered
  device stopped authenticating.
- `scripts/check_non_goals.py` skips nested virtual environments and build
  output at any depth, so a per-component venv cannot drown the product gate in
  third-party matches.
- The API's default port is now **8788**. The previous default was already
  bound on the deployment target by an unrelated service and was the upstream
  of that host's existing routes, so the old default would have taken over a
  running service rather than adding one. The port remains a setting; nothing
  in the contract or the client depends on the number. An existing `.env` that
  sets `WUWATERM_API_PORT` explicitly still wins over both defaults, so the
  deployment guide now says to update that line and read the port back from the
  recreated container: a changed default does not reach a host that pinned the
  old one.

### Documentation

- `docs/architecture.md` rewritten for the system that now exists: two
  presentation adapters over one application layer plus a client that consumes
  the API, the component map including the adapter's import allowlist, the
  trust boundaries including the public HTTPS edge, the two separate identity
  models and the explicit statement that neither can grant the other, and a
  cost-topology section stating that the per-process budgets are never global
  and that the worst case is their sum.
- Four new decision records: [0009](docs/adr/0009-http-api-adapter.md) the HTTP
  adapter (amending the context of 0001 and 0003, whose long-polling decision
  is unchanged), [0010](docs/adr/0010-device-principal-authentication.md)
  device-principal authentication,
  [0011](docs/adr/0011-pc-client-stack.md) the PC client stack and its
  transport policy, and
  [0012](docs/adr/0012-client-transport-selection.md) the transport selection
  itself, with its trust boundary, threat model, credential lifecycle,
  endpoint configuration, verification, deployment and rollback, and the
  documented future migration path.
- `docs/deployment.md` gains the route that publishes the API, its backup,
  reload and one-block rollback, and the readback that has to happen on the
  client machine to mean anything. `docs/validation.md` gains the contract,
  client-transport and client-build gates and where each runs.

## 0.2.1 - 2026-08-06

Maintenance release packaging post-v0.2.0 production hardening already merged
as PRs #41–#45. Game-data pin stays Wuthering Waves 3.5.0 / resource 3.5.5 /
changelist 8059200 at `dae29691c04ef0f48d0810b5d244fb0b37288c60`.

### Telegram Runtime (#41–#43)

- Channel LLM content-shape failures (`invalid_response` / `html_integrity`)
  now retry once as plain text instead of dropping the post, within the
  per-minute call budget (including correct behaviour when the budget is 1).
- Malformed API envelopes are reported as `invalid_api_response`, separate
  from content-shape `invalid_response`.
- Inline `/tr <text>` preserves Telegram rich-text entities; PTB error
  handling covers NetworkError-style update failures.
- Normalization fixes: spoiler markers and version tags no longer eat
  mid-sentence prose or mangle URLs; term-lock / fuzzy design flaws that
  produced wrong translations are repaired with regression tests.
- Delivery and observability hardening for linked-channel paths.

### Maintainer Architecture (#44–#45)

- Formal architecture map (`docs/architecture.md`) and ADRs 0001–0008.
- Fail-closed import-boundary guard (`scripts/check_architecture_boundaries.py`)
  wired into local validation and CI, with Codex P2 follow-ups for nested
  packages and TYPE_CHECKING / presentation import rules.
- No intentional runtime behaviour change in #44–#45.

### Upgrading From 0.2.0

- Deploy only through `deploy/vps-update.sh` on a clean Git checkout where
  `HEAD == origin/main` (see `docs/deployment.md`). Runtime fixes from
  #41–#43 may already be live on long-running hosts; this release still
  advances package version metadata and ships the architecture guard.
- No state-directory migration and no game-data pin change from 0.2.0.

## 0.2.0 - 2026-07-17

Production hardening release: Wuthering Waves 3.5 data pin, transactional VPS
deployment, Telegram structural safety, and default CI packaging gates.

### Upgrading From 0.1.0

- The deployment target must be a clean Git checkout with an `origin` remote
  and a `main` ref; exported non-Git source copies are intentionally not
  deployable. Live upgrades go through `deploy/vps-update.sh`; see
  `docs/deployment.md`.
- Runtime state (`chat_settings.json`, `channel_replies.json`) moved from
  `data/` to `state/`. The updater and runtime perform a validated one-time
  migration and never overwrite existing state files.
- The runtime image only runs the bot; data refresh/build/verify commands
  moved to the builder image. Rebuild the terms database with the 3.5 pin via
  the transactional updater (`docs/data-refresh.md`).

### CI And Packaging

- Added `scripts/check_package_artifacts.py`, a wheel/sdist content audit that
  fails on generated databases, game data, runtime state, environment files,
  deployment internals, secret-looking files, missing package members, and
  version-metadata drift against `pyproject.toml`.
- Extended default CI with lockfile drift checking (`uv lock --check` pinned to
  the deploy image's uv version), wheel/sdist build with strict metadata
  validation and clean-venv install/import/CLI smoke for both artifacts,
  deploy shell script syntax checks, compose config validation, and Docker
  runtime/builder boundary assertions (runtime refuses non-bot commands and
  ships without git/uv).

### Data And Deployment

- Updated the active source profile to Wuthering Waves 3.5.0 / resource 3.5.5
  / changelist 8059200 at exact upstream commit
  `dae29691c04ef0f48d0810b5d244fb0b37288c60`, with observed checkout and
  README provenance recorded in generated DB metadata.
- Added read-only strong candidate verification, including schema, integrity,
  metadata, category, and `穗穗 -> Suisui` exact-hit gates.
- Made VPS updates transactional around a separately verified candidate and
  immutable revision-labelled image, with DB/image/pointer rollback and an
  immutable deployment manifest. Builder containers no longer receive the
  runtime `.env`.

### Telegram Runtime

- Protected every Telegram HTML tag, attribute, link, custom emoji id, and
  entity with opaque structural placeholders so only visible text reaches the
  translator; structural drift now fails closed.
- Added bounded linked-channel admission, atomic multi-chunk LLM call budgets,
  post-queue freshness/authorization checks, privacy-safe outcome telemetry,
  and owner status counters.
- Hardened linked-channel reply-index schema validation and persistence
  diagnostics, including explicit reporting when directory durability is
  uncertain.
- Avoided fuzzy dictionary scans for long translation inputs while retaining
  exact and short ASCII/pinyin lookup behavior.

## 0.1.0 - 2026-07-04

Initial release preparation for the self-hosted WuWa Term Bot.

### Added

- Dictionary-first Chinese to official English and English to Chinese term
  lookup from a locally built SQLite database.
- Term-locked sentence translation through an OpenAI-compatible endpoint, with
  exact database hits returned before any LLM call.
- Telegram bot commands for private and group use, including group allowlists,
  public-mode controls, linked-channel auto-translation, rate limits, and
  privacy-safe operational logging.
- Local validation and hygiene scripts that guard against committing generated
  game data, generated databases, and unsupported runtime surfaces.

### Data Source

- Supported source profile: `arikatsu`
- Supported game data version: `GameVer 3.4.0 | ResVer 3.4.13`
- Pinned source repository: `https://github.com/Arikatsu/WutheringWaves_Data`
- Pinned source commit: `58ec43698d2b4e188cb285467ce1ae887612dd92`

### Known Limitations

- The bot is self-hosted; no public hosted service is provided by this
  repository.
- Live Telegram behavior still requires owner-provided bot credentials and
  chat configuration.
- Free-text sentence translation requires a separately configured
  OpenAI-compatible endpoint.
- Generated TextMap data and generated term databases are local artifacts and
  are not published in this source repository.
