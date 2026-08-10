# Deployment

## VPS Docker Compose

The VPS target uses Docker Compose because the current system Python there is
older than the project target. `/opt/wuwaterm/current` must be a clean Git
checkout with an `origin` remote and a `main` ref; the updater fetches and
requires `HEAD == origin/main`, so an exported source copy without `.git` is
intentionally not deployable. Create `/opt/wuwaterm/current/.env` from
`.env.example` or `deploy/env.example`, and set it to mode `600`. These two
template files are intentionally identical and tested for drift.

Both serving services read that one file through Compose `env_file`; the
builder has no `env_file` at all, and `.env` is ignored and excluded from the
image build context. What reaches each serving container is then narrowed by
its own `environment:` block. `wuwaterm-api` has `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_TEST_CHAT_ID`, `OWNER_USER_ID`, `WUWATERM_REDACTION_SECRET` and the
bot's state paths blanked, because none of them are its business. It does
receive the model credentials — `WUWATERM_OPENAI_API_KEY` and the other
`WUWATERM_OPENAI_*` settings — and that sharing is deliberate: both surfaces
translate through the same upstream account, and a second key would carry the
same power while doubling what has to be rotated. So the isolation boundary is
specific, not total: the Telegram identity, the owner identity and the log
redaction key stay with the bot; the model credential is shared.

The Compose file has two image roles across three services:

- `wuwaterm` builds the `runtime` target and runs the Telegram bot. It mounts
  `data/` at `/app/data` read-only and `state/` at `/app/state` writable.
- `wuwaterm-api` runs the SAME runtime image with `command: ["api"]`. It mounts
  the same `data/` read-only and its own `state-api/` at `/app/state-api`
  writable, so the two serving processes never share writable state. That
  directory is a SIBLING of `state/`, not a child: the bot mounts the whole of
  `state/` read-write, so a child directory would have given the bot process
  read-write access to the credential store. The API container also gets the
  bot's credentials blanked, because they are not its business. It binds
  loopback only, and that bind is fixed in the Compose file rather than read
  from `.env`: with host networking, an environment knob would turn a public
  exposure into a one-line edit.
- `wuwaterm-builder` builds the `builder` target. It has the data-build
  toolchain, mounts `data/` writable, and runs `refresh-data`, `build-db`,
  and `verify-db`.

The runtime image refuses every command except `bot`, `api` and `device`
(exit 64 otherwise), and still ships without the data-build toolchain.

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

## HTTP Adapter Service

`wuwaterm-api` serves the versioned HTTP surface documented by
`docs/api/openapi.json`. It adds **no new public surface**: it binds
`WUWATERM_API_BIND` (default `127.0.0.1`) on `WUWATERM_API_PORT`
(default `8787`), and the Compose file publishes no ports. The supported way
for an owner desktop to reach it today is the SSH entry point that already
exists for operating the host:

The remote end of the tunnel must be the port that deployment actually
configured, so read it from the running container rather than assuming the
default; the local end is your own choice.

```bash
api_port="$(ssh <vps> 'cd /opt/wuwaterm/current && docker compose -f deploy/docker-compose.yml exec -T wuwaterm-api printenv WUWATERM_API_PORT')"
ssh -N -L "8787:127.0.0.1:${api_port}" <vps>
```

Exposing the API on a public hostname would be a new ingress decision (DNS,
TLS, a reverse-proxy route) and is deliberately not part of this topology.

### Device Credentials

There is no registration endpoint. Credentials are issued by the operator on
the host and shown exactly once:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-api device issue --name "owner laptop"
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-api device list
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-api device revoke --device-id <id>
```

`device issue` reads the secret from standard input; the service never prints
credential material. Generate it where it will be stored, then register it:

```bash
docker compose -f deploy/docker-compose.yml run --rm -T wuwaterm-api   device issue --name "owner laptop" < /path/to/secret
```

The token is `wtd1.<device_id>.<that secret>`. Only a salted scrypt verifier is
stored, in `state-api/devices.db` (created 0600, in a 0700 directory, together
with its write-ahead log). Revoking keeps the row and stamps `revoked_at`, so a
withdrawal stays auditable. Break-glass: deleting `state-api/devices.db`
revokes every device at once, and the next start recreates an empty store.

`state-api/` must exist and be writable by the container user before the first
start; create it on the host rather than letting Docker create it root-owned:

```bash
mkdir -p state-api && chmod 700 state-api
```

### Cost Topology

The API's budgets are per process and are NOT shared with the bot:

| Process | Model concurrency | Model calls per minute |
|---|---|---|
| `wuwaterm` (bot) | `WUWATERM_LLM_MAX_CONCURRENCY` (default 4) | per-chat request limit only, plus the linked-channel budget |
| `wuwaterm-api` | `WUWATERM_API_LLM_MAX_CONCURRENCY` (default 2) | `WUWATERM_API_LLM_CALLS_PER_MINUTE` (default 30) |

The worst case for the host is the SUM of the two, never one shared ceiling.
Nothing in either process coordinates with the other; if a single global budget
is ever needed, that is a new mechanism, not a configuration change.

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
docker inspect --format '{{.Image}}' wuwaterm-api
sha256sum data/terms.db
docker compose -f deploy/docker-compose.yml exec -T wuwaterm-api \
  python -c "import os, urllib.request; port = os.environ.get('WUWATERM_API_PORT', '8787'); print(urllib.request.urlopen('http://127.0.0.1:' + port + '/readyz', timeout=10).status)"
```

The pointer must equal the intended source SHA exactly; BOTH running image IDs
and the DB hash must match the manifest, and the health check must print
`200`. These are runtime evidence only when read
from the actual VPS after deployment. Local tests and failure injection are
offline/deployment validation, not proof that production changed.
