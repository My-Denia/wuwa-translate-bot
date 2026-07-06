# WuWa Term Bot

Self-hosted Telegram bot for Wuthering Waves official localization: Chinese
terms to exact official English and the reverse, with term-locked sentence
translation in both directions.

The bot is dictionary-first. An exact database hit returns the official string
from the local SQLite database byte-for-byte and does not call the LLM.
Direction is auto-detected by script: Chinese source text translates to English
by default, and English/Latin source text translates to Chinese. Free text in
either language goes through an OpenAI-compatible endpoint only after known DB
terms are locked, so official terms are restored verbatim in the target
language rather than paraphrased.

## Quick Start

WSL/Linux is the primary development environment. Keep the checkout on the WSL
filesystem, for example under `~/projects/...`, so file watching, permissions,
line endings, and virtualenv scripts behave like Linux.

```bash
test -x .venv/bin/python || uv venv .venv
uv pip install -e ".[dev]"
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
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.db --profile arikatsu
.venv/bin/python scripts/verify_db.py data/terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location
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

The default remains auto-detected when no direction flag is supplied. Replying
to a message with `/tr --to en`, `/tr -to en`, `/sentence --to zh`, or
`/sent --to zh` uses the replied-to text with the requested direction. For
validation, invalid --to values return usage and do not call the LLM. For exact dictionary hits, the bot does not call the LLM. For linked-channel posts,
channel auto-translation remains auto-detected and does not accept command
direction flags.

Run the standard validation set:

```bash
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python -m pytest
```

## Data Source And License Boundary

Primary source:

- `https://github.com/Arikatsu/WutheringWaves_Data`
- pinned commit: `58ec43698d2b4e188cb285467ce1ae887612dd92`
- pinned version: `GameVer 3.4.0 | ResVer 3.4.13`

Fallback mirror to try manually if the primary source is unavailable:

- `https://github.com/Dimbreath/WutheringData` is frozen at 3.1.0
  (`e9234ffe094b2d944d16b222d31102e8ab32d954`, 2026-03-13) and is kept only
  as a legacy fallback.

The active Arikatsu source profile uses sparse checkout for only `BinData` and
`Textmaps`. Bulk TextMap data and generated `terms.db` are local artifacts and
are ignored by Git. This project does not redistribute Wuthering Waves game
data; only a small derived term dictionary is built locally from the public
source above. All Wuthering Waves game data and in-game terminology are
© Kuro Games.

See [Data Refresh](docs/data-refresh.md) for refresh, build, and verification
details.

## Guides

- [Changelog](CHANGELOG.md): notable source changes by release.
- [Deployment](docs/deployment.md): Docker Compose service on the VPS, `.env`
  handling, data refresh commands, and smoke checks.
- [Data Refresh](docs/data-refresh.md): source profiles, local setup, DB build,
  lookup commands, and data licensing boundaries.
- [Telegram Behavior](docs/telegram-behavior.md): commands, group authorization,
  public mode, linked-channel auto-translation, and Telegram-specific limits.
- [Privacy And LLM](docs/privacy-and-llm.md): dictionary-first privacy boundary,
  LLM configuration, prompt-injection guard, placeholder integrity, fail-closed
  settings, and secret handling.
- [Validation](docs/validation.md): offline validation commands, live smoke
  caveats, and Windows reference commands.
- [Release Checklist](docs/release-checklist.md): release metadata, validation,
  privacy notes, distribution boundaries, and release note template.

## Deployment Entry

The VPS target uses Docker Compose because the current system Python there is
older than the project target. Copy the repo to `/opt/wuwaterm/current`, create
`/opt/wuwaterm/current/.env` from `deploy/env.example`, set it to mode `600`,
and run Compose through `deploy/docker-compose.yml`.

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm wuwaterm verify-db
docker compose -f deploy/docker-compose.yml up -d
```

Secrets are injected only through Compose `env_file`; `.env` is ignored and
excluded from the image build context. Full deployment notes are in
[Deployment](docs/deployment.md).

## Validation Entry

For a full local validation pass, run:

```bash
.venv/bin/python scripts/verify_seed_terms.py data/terms.db --discrepancies goal-runs/wuwaterm-v2-translator/seed-discrepancies.json
.venv/bin/python scripts/verify_exact_hits.py data/terms.db --sample-size 500
.venv/bin/python scripts/verify_idempotent_build.py --data-dir data/wutheringdata --out-dir goal-runs/wuwaterm-v2-translator --profile arikatsu
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python -m pytest
```

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
