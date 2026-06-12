# WuWa Term Bot

Self-hosted Telegram bot for Wuthering Waves Chinese terms to exact official
English localization and term-locked sentence translation.

The bot is dictionary-first: exact database hits return the English string from
the local SQLite database byte-for-byte. Other Chinese input is translated
through an OpenAI-compatible endpoint after known DB terms are locked, so
official terms are not paraphrased.

## Data Source

Primary source:

- `https://github.com/Arikatsu/WutheringWaves_Data`
- pinned commit: `58ec43698d2b4e188cb285467ce1ae887612dd92`
- pinned version: `GameVer 3.4.0 | ResVer 3.4.13`

Fallback mirrors to try manually if the primary source is unavailable:

- `https://github.com/Dimbreath/WutheringData` is frozen at 3.1.0
  (`e9234ffe094b2d944d16b222d31102e8ab32d954`, 2026-03-13) and is kept only
  as a legacy fallback.

The active Arikatsu source profile uses sparse checkout for only:

- `BinData`
- `Textmaps`

Bulk TextMap data and generated `terms.db` are local artifacts and are ignored
by Git.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Build Dictionary

```powershell
.venv\Scripts\python -m wuwaterm.cli refresh-data --dest data\wutheringdata --profile arikatsu
.venv\Scripts\python -m wuwaterm.cli build-db --data-dir data\wutheringdata --db data\terms.db --profile arikatsu
.venv\Scripts\python scripts\verify_db.py data\terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location
```

## Lookup

```powershell
.venv\Scripts\python -m wuwaterm.cli lookup --db data\terms.db 声骸
.venv\Scripts\python -m wuwaterm.cli sentence --db data\terms.db "今汐装备了声骸"
```

## Telegram Bot

Create the bot and token in BotFather yourself, then run:

```powershell
$env:TELEGRAM_BOT_TOKEN="..."
$env:WUWATERM_DB_PATH="data\terms.db"
.venv\Scripts\python -m wuwaterm.cli bot
```

Commands:

- `/tr 声骸` returns `Echo`
- `/tr 今汐装备了声骸` translates the sentence after locking `Jinhsi` and `Echo`
- `/term 今汐` returns `Jinhsi`
- `/sentence 今汐装备了声骸` locks known DB terms before translation

Optional LLM environment variables:

- `WUWATERM_OPENAI_BASE_URL`, set explicitly to your LiteLLM gateway URL
- `WUWATERM_OPENAI_API_KEY`
- `WUWATERM_OPENAI_MODEL`
- `WUWATERM_RATE_LIMIT_PER_MINUTE`, default `10`
- `WUWATERM_GROUP_TR_REJECT_TEXT`, default `仅群管理员可用 /tr`
- `WUWATERM_PRIVATE_TR_REJECT_TEXT`, default `此 bot 仅限群内由管理员使用`
- `WUWATERM_TR_REJECT_SILENT`, default `0`; set `1` to drop unauthorized
  `/tr` calls without replying
- `OWNER_USER_ID`, no default; the only Telegram user id allowed to use
  `/tr` in private chat — missing or empty means private `/tr` rejects
  everyone (fail-closed) and a startup warning is logged
- `WUWATERM_SOURCE_PROFILE`, default `arikatsu`; supported profiles are listed
  by `refresh-data --help` and `build-db --help`

No Telegram token, LLM key, endpoint, or model is hardcoded.

### Group Chats

In groups, slash commands work with Telegram privacy mode left on. The bot only
handles commands, not free-text messages.

- All translate commands — `/tr`, `/term`, `/sentence`, `/sent` — share one
  authorization gate and are admin-only in groups: each call resolves the
  sender via `getChatMember`, and only `creator`/`administrator` may use
  them. Anonymous group admins (posting as the group itself) are allowed.
  Membership verdicts are cached about 5 minutes per (chat, user).
- Unauthorized callers get a one-line reply, default `仅群管理员可用 /tr`;
  the wording is configurable, and a config flag switches to silent ignore
  (default replies). Rejected calls never invoke the LLM but still consume
  the per-chat throttle budget.
- Authorized `/tr 声骸` and `/tr@<botusername> 声骸` return dictionary hits;
  `/tr <Chinese sentence>` translates with DB terms locked.
- Group replies quote the asking message.
- Private chat: all translate commands answer only the configured owner
  user id; everyone else gets a one-line reply, default
  `此 bot 仅限群内由管理员使用`. With the owner id unset, private chat
  rejects everyone (fail-closed). Channel-type chats are rejected entirely.
- Per-chat throttling defaults to 10 lookups per minute.
- LLM-path input is capped at 1000 characters.

## VPS Docker Compose

The VPS target uses Docker Compose because the current system Python there is
older than the project target. Copy the repo to `/opt/wuwaterm/current`, create
`/opt/wuwaterm/current/.env` from `deploy/env.example`, and set it to mode
`600`. Secrets are injected only through Compose `env_file`; `.env` is ignored
and excluded from the image build context.

Prepare or refresh data without starting the service:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm build-db
docker compose -f deploy/docker-compose.yml run --rm wuwaterm verify-db
```

For each real game-version refresh, pick at least one term that exists only in
the new game data and run a live `/tr <term>` check in Telegram after the DB
build. Counts and hashes prove rebuild mechanics; a new-term live check proves
the running bot is serving the refreshed content.

The compose service uses long polling (`wuwaterm bot`) and
`restart: unless-stopped`. No webhook, inline mode, or alias layer is configured.
Starting the service is owner-gated:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml up -d
```

## Validation

```powershell
.venv\Scripts\python scripts\verify_seed_terms.py data\terms.db --discrepancies goal-runs\wuwaterm-v2-translator\seed-discrepancies.json
.venv\Scripts\python scripts\verify_exact_hits.py data\terms.db --sample-size 500
.venv\Scripts\python scripts\verify_idempotent_build.py --data-dir data\wutheringdata --out-dir goal-runs\wuwaterm-v2-translator --profile arikatsu
.venv\Scripts\python scripts\check_repo_hygiene.py
pytest
```

`verify_idempotent_build.py` compares SHA256 over LF-normalized SQLite logical
dumps, not raw database bytes, so Windows/Linux SQLite formatting differences
do not create false mismatches.

Live Telegram smoke is owner-gated. If `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_TEST_CHAT_ID` are not supplied, only the live smoke criterion is
blocked; offline handler tests still validate the bot code.
