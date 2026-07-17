# Changelog

All notable source changes for this project are tracked here. This repository
does not distribute generated game data or generated SQLite databases.

## Unreleased

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
