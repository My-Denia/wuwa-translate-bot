[简体中文](README.md) | English

# WuWa Term Bot

Self-hosted Wuthering Waves official localization service: Chinese terms to
exact official English and the reverse, with term-locked sentence translation
in both directions. One protocol-neutral application layer serves two
presentation adapters — the Telegram bot and a versioned HTTP API — plus a
Windows desktop client that consumes the API.

The service is dictionary-first. An exact database hit returns the official
string from the local SQLite database byte-for-byte and does not call the LLM.
Direction is auto-detected by script: Chinese source text translates to English
by default, and English/Latin source text translates to Chinese. Free text in
either language goes through an OpenAI-compatible endpoint only after known DB
terms are locked, so official terms are restored verbatim in the target
language rather than paraphrased.

## Architecture Overview

- **Application layer.** `src/wuwaterm/application.py` holds the
  dictionary-first pipeline exactly once. It is protocol-neutral: it imports no
  presentation module and no chat SDK
  ([ADR 0009](docs/adr/0009-http-api-adapter.md)).
- **Two presentation adapters.** The Telegram bot (`src/wuwaterm/bot.py`,
  `channel.py`) owns commands, chat authorization, chat wording and markup; the
  versioned HTTP API (`src/wuwaterm_api/`) owns versioned routes, device
  authentication, one error envelope and plain-text responses. Both surfaces
  are served by that one pipeline ([Architecture](docs/architecture.md)).
- **Device-principal authentication.** Every `/v1` route requires a device
  credential. Credentials are revocable without touching the Telegram bot's own
  access controls, and the credential store keeps only salted scrypt verifiers
  ([ADR 0010](docs/adr/0010-device-principal-authentication.md)).
- **Windows desktop client.** The client under `client/` is deliberately *not*
  an adapter: it is a consumer of the API's published contract and holds no
  translation logic. It reaches the service over HTTPS
  ([ADR 0011](docs/adr/0011-pc-client-stack.md),
  [ADR 0012](docs/adr/0012-client-transport-selection.md)).
- **Published contract.** The API contract snapshot is committed at
  [`docs/api/openapi.json`](docs/api/openapi.json) and drift-gated by
  `scripts/check_api_contract.py`.

The 0.3.0 release notes frame this step as an "API-first release: the
Telegram-only bot becomes a multi-adapter system" ([Changelog](CHANGELOG.md)).
Decision rationale lives in the [ADR index](docs/adr/README.md); the
maintainer map of modules, request flows and trust boundaries is
[Architecture](docs/architecture.md).

## Quick Start

The commands below assume a POSIX shell. Under WSL, keep the checkout on the
WSL filesystem, for example under `~/projects/...`, so file watching,
permissions, line endings, and virtualenv scripts behave like Linux.

```bash
test -x .venv/bin/python || uv venv .venv
uv sync --locked --extra dev
```

If the WSL image has `python3-venv` and pip installed, the standard-library
path also works:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```

Use `uv venv --clear .venv` only when intentionally rebuilding the local
virtualenv from scratch.

## Common Commands

Build the local dictionary:

```bash
.venv/bin/python -m wuwaterm.cli refresh-data --dest data/wutheringdata --profile arikatsu
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.candidate.db --profile arikatsu --atomic
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
```

Look up terms and translate a sentence:

```bash
.venv/bin/python -m wuwaterm.cli lookup --db data/terms.db 声骸
.venv/bin/python -m wuwaterm.cli sentence --db data/terms.db "今汐装备了声骸"
```

Run the Telegram bot:

```bash
export TELEGRAM_BOT_TOKEN="..."
export WUWATERM_DB_PATH="data/terms.db"
.venv/bin/python -m wuwaterm.cli bot
```

Run the HTTP API adapter (needs the `api` extra, which carries FastAPI and
uvicorn; it binds loopback by default):

```bash
export WUWATERM_DB_PATH="data/terms.db"
.venv/bin/python -m wuwaterm_api.cli serve
```

Telegram command examples:

- `/tr 声骸` -> `Echo`
- `/tr Echo` -> `声骸`
- `/tr --to en 今汐装备了声骸` and `/tr -to en 今汐装备了声骸`
  force English output
- `/tr --to zh Jinhsi equipped an Echo` and
  `/tr -to zh Jinhsi equipped an Echo` force Chinese output
- `/sentence --to en 今汐装备了声骸` and `/sent --to en 今汐装备了声骸`
  force English sentence translation
- `/sentence --to zh Jinhsi equipped an Echo` and
  `/sent --to zh Jinhsi equipped an Echo` force Chinese sentence translation

The default remains auto-detected when no direction flag is supplied. To reply
to a message, send `/tr --to en`, `/tr -to en`, `/sentence --to zh`, or
`/sent --to zh`; the bot uses the replied-to text with the requested direction.
For validation, invalid --to values return usage and do not call the LLM; exact
dictionary hits do not call the LLM. For linked-channel posts, channel
auto-translation remains auto-detected and does not accept command direction
flags.

Run the standard validation set:

```bash
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python scripts/check_architecture_boundaries.py
.venv/bin/python -m pytest
```

## Data Source And License Boundary

Primary source:

- `https://github.com/Arikatsu/WutheringWaves_Data`
- pinned commit: `6ce8d5eda49f2930da84d8846c144432142c7465`
- pinned version: `GameVer 3.6.0 | ResVer 3.6.4 | Changelist 8464573`

Fallback mirror to try manually if the primary source is unavailable:

- `https://github.com/Dimbreath/WutheringData`, kept only as a legacy fallback
  profile and pinned in `src/wuwaterm/constants.py` at
  `e9234ffe094b2d944d16b222d31102e8ab32d954`.

The active Arikatsu source profile uses sparse checkout for only `README.md`,
`BinData`, and `Textmaps`. The root README is a required version-provenance
file. Bulk TextMap data and generated databases are local artifacts and
are ignored by Git. This project does not redistribute Wuthering Waves game
data; only a small derived term dictionary is built locally from the public
source above. All Wuthering Waves game data and in-game terminology are
© Kuro Games.

See [Data Refresh](docs/data-refresh.md) for refresh, build, and verification
details.

## Guides

- [Architecture](docs/architecture.md): maintainer map of modules, request
  flows, trust boundaries, single-instance topology, and ADRs.
- [Changelog](CHANGELOG.md): notable source changes by release.
- [Deployment](docs/deployment.md): Docker Compose service on the VPS, `.env`
  handling, data refresh commands, and smoke checks.
- [Data Refresh](docs/data-refresh.md): source profiles, local setup, DB build,
  lookup commands, and data licensing boundaries.
- [Telegram Behavior](docs/telegram-behavior.md): commands, group authorization,
  public mode, linked-channel auto-translation, and Telegram-specific limits.
- [HTTP API Contract](docs/api/openapi.json): the committed contract snapshot
  for the versioned `/v1` routes.
- [Desktop Client](client/README.md): the Windows client for the HTTP API — its
  stack, settings, credential handling, and build.
- [Privacy And LLM](docs/privacy-and-llm.md): dictionary-first privacy boundary,
  LLM configuration, prompt-injection guard, placeholder integrity, fail-closed
  settings, and secret handling.
- [Validation](docs/validation.md): offline validation commands, live smoke
  caveats, and Windows reference commands.
- [Release Checklist](docs/release-checklist.md): release metadata, validation,
  privacy notes, distribution boundaries, and release note template.

## Deployment Entry

The VPS target uses Docker Compose because the current system Python there is
older than the project target. `/opt/wuwaterm/current` must be a clean Git
checkout whose `HEAD` can be verified against freshly fetched `origin/main`;
an exported source copy without `.git` is deliberately rejected. Create
`/opt/wuwaterm/current/.env` from `deploy/env.example`, set it to mode `600`,
and run Compose through `deploy/docker-compose.yml`.

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
WUWATERM_DEPLOY_ROOT=/opt/wuwaterm/current sh deploy/vps-update.sh
```

The updater builds and strongly verifies a separate candidate database and an
immutable source-revision image before it stops the old service. It then
promotes, starts, smokes, writes an immutable manifest, and atomically publishes
`.deploy_commit`; any post-promotion failure restores the previous database,
image, and pointer. `deploy/vps-update.sh` stops, restarts, smokes and reads
back both serving containers, and the rollback covers both.

Both serving containers come from the `runtime` Docker target and the same
image, and differ only in the entrypoint command: `bot` runs the Telegram bot
(`wuwaterm-bot`) and `api` runs the HTTP API (`wuwaterm-api`). The runtime
image also accepts the operator-only `device` command for credential
management as a one-shot container, and refuses every other command. Data
refresh/build/verify use the `builder` target through the `wuwaterm-builder`
service. Both serving containers mount `data/` read-only. The bot uses
writable `state/` for `chat_settings.json` and `channel_replies.json`; the API
uses `state-api/`, a sibling directory holding its own device credential
store, so the bot's read-write mount never covers it.
When upgrading an older deployment, use `deploy/vps-update.sh` or the
state-only migration in [Deployment](docs/deployment.md). Both stop the old
runtime before the validated, atomic one-time migration. Do not manually copy
state files while the old bot is running. Remove or update old `.env`
overrides that point those files at `data/`.
Runtime secrets are injected only into the serving services through Compose
`env_file`; the builder has no `env_file`, and `.env` is ignored and excluded
from the image build context. Full deployment notes are in
[Deployment](docs/deployment.md).

## Validation Entry

For a full local validation pass, run:

```bash
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
.venv/bin/python scripts/verify_seed_terms.py data/terms.candidate.db --discrepancies goal-runs/wuwaterm-v2-translator/seed-discrepancies.json
.venv/bin/python scripts/verify_exact_hits.py data/terms.candidate.db --sample-size 500
.venv/bin/python scripts/verify_idempotent_build.py --data-dir data/wutheringdata --out-dir goal-runs/wuwaterm-v2-translator --profile arikatsu
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python scripts/check_architecture_boundaries.py
.venv/bin/python -m pytest
```

The `goal-runs/` paths above are local working artifacts, ignored by Git; the
scripts create or read them on the machine that runs the validation pass.

`scripts/deploy_smoke.py` is a deployment reachability check, not a polling
handler E2E test. See [Validation](docs/validation.md) for the exact validation
scope and live Telegram smoke caveats.

## Maintenance

This is a personal hobby project, maintained on a best-effort basis. There is no
guarantee of responses to issues or pull requests.

## License

Released under the [MIT License](LICENSE), © 2026 My-Denia. The MIT license
covers this project's source code only, not the upstream Wuthering Waves game
data or in-game terminology, which are © Kuro Games. See
[Data Source And License Boundary](#data-source-and-license-boundary).
