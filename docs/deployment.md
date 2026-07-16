# Deployment

## VPS Docker Compose

The VPS target uses Docker Compose because the current system Python there is
older than the project target. `/opt/wuwaterm/current` must be a clean Git
checkout with an `origin` remote and a `main` ref; the updater fetches and
requires `HEAD == origin/main`, so an exported source copy without `.git` is
intentionally not deployable. Create `/opt/wuwaterm/current/.env` from
`.env.example` or `deploy/env.example`, and set it to mode `600`. These two
template files are intentionally identical and tested for drift. Runtime
secrets are injected only into `wuwaterm` through Compose `env_file`; the
builder has no `env_file`, and `.env` is ignored and excluded from the image
build context.

The Compose file has two image roles:

- `wuwaterm` builds the `runtime` target and only runs the Telegram bot. It
  mounts `data/` at `/app/data` read-only and `state/` at `/app/state` writable.
- `wuwaterm-builder` builds the `builder` target. It has `git` and build-only
  dependencies, mounts `data/` writable, and runs `refresh-data`, `build-db`,
  and `verify-db`.

Prepare and verify a candidate without changing the serving database:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder verify-db
```

Do not move this candidate over `data/terms.db` manually. The transactional
updater is the production promotion boundary.

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

For a normal live upgrade, run `deploy/vps-update.sh`. Before any stop it:

1. fetches `origin/main`, requires clean `HEAD == origin/main`, and records the
   full source commit;
2. rebuilds the mutable local builder image from that exact clean checkout,
   then proves the builder container has none of the runtime secret variables;
3. refreshes source data, builds a unique candidate, and runs the strong DB
   verifier against that candidate;
4. builds an immutable `wuwaterm-runtime:<source-commit>` image and verifies its
   `org.opencontainers.image.revision` label.

It then snapshots the old database and commit pointer, tags the old image for
rollback, stops the runtime, promotes the candidate, validates/migrates state,
starts the exact validated image, and runs `scripts/deploy_smoke.py` with
diagnostic message sending explicitly disabled. Only after smoke and running
image-ID checks pass does it create and read back
`.deployments/<source-commit>.json`, then atomically publish `.deploy_commit`.
The manifest contains source commit, image ref/ID/digest/revision, DB SHA-256,
DB source profile/commit/game/resource/changelist, deployment UTC, and backup
path. It is mode read-only and never overwritten with different content.
Re-running the same source commit is accepted only when its image and database
binding is byte-for-byte identical; a different rebuild must use a new source
commit instead of deleting or rewriting the historical manifest.

Any post-promotion state/start/smoke/manifest/pointer failure restores the old
DB, recreates the runtime from the tagged old image, and restores or removes
the commit pointer. Timestamped DB and pointer backups and rollback image tags
are retained; no historical snapshot or state file is deleted.

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

## Traceability Readback

After an owner-authorized deployment, read the pointer and its immutable
manifest together:

```bash
cd /opt/wuwaterm/current
cat .deploy_commit
python3 -m json.tool ".deployments/$(cat .deploy_commit).json"
docker inspect --format '{{.Image}}' wuwaterm-bot
sha256sum data/terms.db
```

The pointer must equal the intended source SHA exactly; the running image ID
and DB hash must match the manifest. These are runtime evidence only when read
from the actual VPS after deployment. Local tests and failure injection are
offline/deployment validation, not proof that production changed.
