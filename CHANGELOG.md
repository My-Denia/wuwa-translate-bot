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
- Revocable device credentials in their own store (`state/api/devices.db`),
  registered and withdrawn by an operator through the new `wuwaterm-api device`
  commands. The operator supplies the secret on standard input and only its
  hash is stored: the service never produces or prints credential material, so
  none can reach a log, a terminal recording or a captured command output
  through it.
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
