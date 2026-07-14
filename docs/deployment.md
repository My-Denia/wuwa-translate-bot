# Deployment

## VPS Docker Compose

The VPS target uses Docker Compose because the current system Python there is
older than the project target. Copy the repo to `/opt/wuwaterm/current`, create
`/opt/wuwaterm/current/.env` from `.env.example` or `deploy/env.example`, and
set it to mode `600`. These two template files are intentionally identical and
tested for drift. Secrets are injected only through Compose `env_file`; `.env`
is ignored and excluded from the image build context.

The Compose file has two image roles:

- `wuwaterm` builds the `runtime` target and only runs the Telegram bot. It
  mounts `data/` at `/app/data` read-only and `state/` at `/app/state` writable.
- `wuwaterm-builder` builds the `builder` target. It has `git` and build-only
  dependencies, mounts `data/` writable, and runs `refresh-data`, `build-db`,
  and `verify-db`.

Prepare or refresh data without starting the service:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder verify-db
```

For each real game-version refresh, pick at least one term that exists only in
the new game data and run a live `/tr <term>` check in Telegram after the DB
build. Counts and hashes prove rebuild mechanics; a new-term live check proves
the running bot is serving the refreshed content.

The compose service uses long polling (`wuwaterm bot`) and
`restart: unless-stopped`. It does not configure webhook delivery, inline query
handling, or any extra command-routing layer. Starting the service is
owner-gated:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml up -d
```

## State Migration

Older deployments stored `chat_settings.json` and `channel_replies.json` in
`data/` beside `terms.db`. The current runtime keeps `terms.db` read-only under
`data/` and writes runtime state under `state/`.

For a normal live upgrade, run `deploy/vps-update.sh`. It refreshes, atomically
builds, and verifies `terms.db` while the old bot can remain live. It then stops
the runtime, copies both legacy state files with validation and atomic
create-if-absent publication, and starts the new runtime. Stopping the old bot
before the copy is required: otherwise an authorization or reply-index update
written after the copy could be lost.

For a state-only migration using an existing `terms.db`, let the new runtime
perform the same validated, atomic one-time migration during startup:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml stop wuwaterm
mkdir -p state
docker compose -f deploy/docker-compose.yml up -d --build wuwaterm
```

Do not manually copy these files while the old bot is running. Do not delete or
pre-create `state/*.json`: an existing target is deliberately never overwritten.

If the existing `.env` explicitly sets either state file path to the old data
directory, remove those lines or update them to:

```bash
WUWATERM_SETTINGS_PATH=state/chat_settings.json
WUWATERM_CHANNEL_REPLY_INDEX_PATH=state/channel_replies.json
```

The Compose runtime also overrides these paths to `/app/state/...` so an old
`.env` cannot make the read-only runtime write under `/app/data`.

Startup performs the one-time copy when `WUWATERM_STATE_DIR` is set,
the new file is missing, and the legacy DB-adjacent file exists. This includes
old explicit settings that still point at the DB-adjacent files. It never
overwrites an existing file in `state/`; copy failure stops startup instead of
silently dropping the allowlist, public-mode state, or channel reply index.

## Deployment Smoke

After the service starts, use `scripts/deploy_smoke.py` as a deployment
reachability check. It verifies `getMe`, and when `TELEGRAM_TEST_CHAT_ID` is set
it sends one diagnostic message without printing the token or chat id. See
[Validation](validation.md) for live smoke caveats.
