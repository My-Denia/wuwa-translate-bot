# Self-Hosting

This is the generic path: how a stranger runs this service on their own machine.
It makes no assumption about the operating system beyond Linux with a container
runtime, and it names no host, path or reverse proxy that belongs to anyone in
particular. The owner's own production runbook — a specific host, a specific
directory, a transactional updater — is [Deployment](deployment.md), and it is
not a prerequisite for anything below.

## What You Get, And What You Still Have To Do

You get a dictionary-first Wuthering Waves terminology service: exact
Chinese-to-English term lookup and the reverse, plus term-locked sentence
translation in both directions, reachable through a Telegram bot, a versioned
HTTP API, or both. An exact dictionary hit returns the official string
byte-for-byte and never calls a model.

You still have to do these things yourself, because this project cannot do them
for you:

- **Build the terminology database.** It is derived locally from a public
  upstream data repository pinned in `src/wuwaterm/constants.py`. It is never
  distributed — not in a release, not in a container image. A fresh install has
  no database until you build one.
- **Bring your own Telegram bot token**, if you want the bot surface.
- **Bring your own OpenAI-compatible endpoint**, if you want sentence
  translation. Exact dictionary lookups work without it; see
  [With no model configured](#with-no-model-configured) for exactly what a
  request that would have needed a model does instead.
- **Terminate TLS yourself.** The API binds a loopback address and refuses to
  bind anything else. Publishing it is your reverse proxy's job.
- **Issue your own device credentials.** There is no registration endpoint and
  no sign-up.

## Requirements

Either path works; pick one.

| | Container path | Source path |
| --- | --- | --- |
| Host | Linux with Docker and Compose v2 | Any OS with Python 3.11 or newer |
| Python | supplied by the image (`python:3.11-slim`) | 3.11+ (`requires-python >=3.11`) |
| Disk | about 2 GB for the upstream data checkout, plus images | about 2 GB for the upstream data checkout |
| Git | needed for the source checkout | needed for the checkout and for the data refresh |

Optional, for either path:

- A **Telegram bot token** if you want the bot. Create the bot with Telegram's
  own BotFather; this project has no part in that step.
- An **OpenAI-compatible endpoint** (base URL, API key, model name) if you want
  sentence translation.

Linux is the supported development platform for the server. See
[Support Matrix](support-matrix.md) for what is tested where.

## Get The Source At A Release Tag

Both paths need the source, because the Compose files, the entrypoints, the
data-build commands and the verification scripts all live in it.

```bash
git clone --branch vX.Y.Z --depth 1 https://github.com/My-Denia/wuwa-translate-bot.git
cd wuwa-translate-bot
```

Replace `vX.Y.Z` with a published tag from
[Releases](https://github.com/My-Denia/wuwa-translate-bot/releases). Deploying
from a moving branch is possible and is a worse idea than it looks: the data pin
and the schema version travel with the source, so a tag is what makes a working
install describable later.

For the source path, create the environment and install the API extra:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[api,build]"
```

`api` carries FastAPI and uvicorn; `build` carries what the data build needs.
The Telegram bot needs neither extra beyond the base install.

## Configure

```bash
cp deploy/env.example .env
chmod 600 .env
```

`.env` is git-ignored and is excluded from the container build context. Edit it
before the first start. The variables are documented inline in the file; these
are the ones that decide whether each surface works at all.

**For the Telegram bot:**

| variable | why it matters |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Without it the bot cannot start. |
| `OWNER_USER_ID` | Who may use the bot in a private chat. Empty means private chat rejects everyone, which is the fail-closed default. |
| `WUWATERM_STATE_DIR` | Where `chat_settings.json` and the channel reply index live. Default `state`. |

**For the HTTP API:**

| variable | why it matters |
| --- | --- |
| `WUWATERM_DB_PATH` | The terminology database the service reads. Default `data/terms.db`. |
| `WUWATERM_API_PORT` | The loopback port. Default `8788`. Under Compose the bind address itself is fixed in the Compose file and is deliberately not an environment knob. |
| `WUWATERM_API_STATE_DIR` | Where the device credential store lives. Default `state-api`, a **sibling** of the bot's state directory and never a child of it, because the bot mounts its own state directory writable. |
| `WUWATERM_API_RATE_LIMIT_PER_MINUTE`, `WUWATERM_API_LLM_CALLS_PER_MINUTE`, `WUWATERM_API_MAX_BODY_BYTES`, `WUWATERM_API_REQUEST_TIMEOUT_SECONDS` | Per-process admission and spending bounds. They are not shared with the bot; the documented worst case is the sum of the two processes. |

**For sentence translation (optional, shared by both surfaces):**
`WUWATERM_OPENAI_BASE_URL`, `WUWATERM_OPENAI_API_KEY`, `WUWATERM_OPENAI_MODEL`,
plus the timeout and concurrency bounds beside them.

**For the owner-private web layer (optional, off by default):**
`WUWATERM_API_WEB_ENABLED`, `WUWATERM_API_WEB_DEVICE_TOKEN`,
`WUWATERM_API_WEB_EDGE_SECRET`, `WUWATERM_API_WEB_SESSION_TTL_SECONDS`,
`WUWATERM_API_WEB_MAX_SESSIONS`. Leave `WUWATERM_API_WEB_ENABLED` unset unless
you have read [The owner-private web presentation layer](web-presentation-layer.md):
the layer expects a reverse proxy in front of it that both authenticates the
visitor and injects the edge marker, and it refuses every request that arrives
without that marker. With the switch off there is no route and no
sub-application at all.

Note where those values land: if one environment file feeds several processes,
the web variables reach processes that have no web surface. The Compose file in
this repository blanks all three in the bot service for exactly that reason. If
you deploy some other way, reproduce that.

## Build The Terminology Database

Three steps, always in this order: fetch the pinned upstream data, build a
candidate database, verify it. The refresh is fail-closed by design — it checks
that the checkout is the pinned commit of the pinned repository and that the
upstream version-provenance file says the version the profile expects, and it
stops rather than building from something else.

**Container path:**

```bash
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder verify-db
```

**Source path:**

```bash
.venv/bin/python -m wuwaterm.cli refresh-data --dest data/wutheringdata --profile arikatsu
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.candidate.db --profile arikatsu --atomic
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
```

`verify_db.py` checks integrity, the schema, the recorded source provenance
against the profile, the required categories, and representative exact hits. On
a first install, once it passes, put the candidate in place:

```bash
mv data/terms.candidate.db data/terms.db
```

On an install that is already serving, do not do that by hand while the service
is running — stop it first, or use the transactional updater described in
[Deployment](deployment.md).

## Start The Service

**Container path** starts whichever services you want:

```bash
docker compose -f deploy/docker-compose.yml up -d
```

**Source path**, one process each:

```bash
.venv/bin/python -m wuwaterm.cli bot --db data/terms.db
.venv/bin/python -m wuwaterm_api.cli serve
```

The API binds `127.0.0.1` and port `8788` unless you say otherwise, and it
refuses to bind a non-loopback address. Check it:

```bash
curl -sS http://127.0.0.1:8788/healthz
curl -sS http://127.0.0.1:8788/readyz
```

`/healthz` answers `{"status":"ok"}` as soon as the process is up. `/readyz`
answers `{"status":"ready"}` only when the terminology database is readable, and
`503` with an `internal` error code when it is not. Neither endpoint needs a
credential; every `/v1` route does.

## Issue The First Device Credential

There is no registration endpoint. The operator issues credentials, and the
secret is read from standard input so it is never printed and never appears in
a process listing.

**Container path.** A one-shot container of the API service. On a fresh
install with one runtime image this is enough; on a host that carries several
runtime image tags, pin the reference to the image the service is actually
running, because an unpinned run can start a stale image whose refusal reads
like a broken runbook — the resolution is written out in
[Deployment](deployment.md).

```bash
docker compose -f deploy/docker-compose.yml run --rm -T wuwaterm-api device issue --name "my desktop" < /path/to/secret
```

**Source path:**

```bash
.venv/bin/python -m wuwaterm_api.cli device issue --name "my desktop" < /path/to/secret
```

Both accept `--scopes translate,meta`, which is also the default: `translate`
admits `POST /v1/translations`, `meta` admits `GET /v1/terms` and
`GET /v1/meta`. The command prints the device id, the name, the scopes and the
creation time — nothing secret. The token you hand to the desktop client is:

```
wtd1.<device id>.<the secret you supplied>
```

Only a salted scrypt verifier is stored, in `state-api/devices.db` — a `0600`
file in a `0700` directory, together with its write-ahead log. The other two
subcommands are `device list` and `device revoke --device-id <id>`; revoking
keeps the row and stamps a revocation time, so a withdrawal stays auditable.
Deleting `state-api/devices.db` revokes every device at once and the next start
recreates an empty store.

Create the state directory yourself before the first container start, so the
container runtime does not create it owned by another user:

```bash
mkdir -p state-api && chmod 700 state-api
```

## First Lookup, First Translation

Both `/v1` routes take the token as a bearer credential. `request_id` is a fresh
hex identifier on every response and is also returned in the `X-Request-Id`
header; the values below are illustrative.

An exact dictionary lookup — no model involved, at any setting:

```bash
curl -sS "http://127.0.0.1:8788/v1/terms?q=%E4%BB%8A%E6%B1%90" \
  -H "Authorization: Bearer wtd1.<device id>.<secret>"
```

```json
{
  "query": "今汐",
  "matches": [
    {"zh": "今汐", "en": "Jinhsi", "category": "resonator", "score": 100.0, "reason": "exact"}
  ],
  "request_id": "3f7c1a9e5b2d4c8a9e0f1b2c3d4e5f60"
}
```

A sentence, which reaches the model only after the known terms in it have been
locked:

```bash
curl -sS http://127.0.0.1:8788/v1/translations \
  -H "Authorization: Bearer wtd1.<device id>.<secret>" \
  -H "Content-Type: application/json" \
  -d '{"text": "今汐装备了声骸", "to": "en"}'
```

```json
{
  "kind": "llm",
  "text": "Jinhsi equipped an Echo.",
  "direction": "en",
  "dictionary_miss": false,
  "request_id": "b41d9e07c3a24f15ae6d8c2b0f739e5a"
}
```

`kind` says which stage answered: `noop` (nothing translatable), `exact` (an
official dictionary hit), `fuzzy` (a trusted pinyin hit) or `llm`. `to` may be
`"en"`, `"zh"`, or omitted to auto-detect from the source text.

Every failure uses one envelope:

```json
{"error": {"code": "unauthorized", "message": "..."}, "request_id": "..."}
```

The codes are `unauthorized`, `forbidden`, `rate_limited`, `payload_too_large`,
`invalid_request`, `input_too_long`, `llm_unavailable`, `llm_budget_exhausted`
and `internal`.

### With no model configured

Leaving `WUWATERM_OPENAI_BASE_URL` and its siblings empty is a supported way to
run this. What changes is narrow:

- An **exact dictionary hit still answers**, on every surface. That path never
  calls a model.
- A request that would have needed the model is **refused rather than
  answered**: `POST /v1/translations` returns HTTP `503` with the error code
  `llm_unavailable` and the message `no translation model is configured`. The
  pipeline could have returned the source text with official terms substituted,
  which is a reasonable fallback in a chat window but over HTTP would look like
  a successful translation, so this surface declines to pretend.

## Publish It Over HTTPS

**The requirement, not the recipe:** the API binds a loopback address and
refuses anything else, so nothing outside the host can reach it until you put a
TLS terminator in front of it. The desktop client verifies certificates and
cannot be told not to; plaintext `http://` is accepted by it only for loopback.
So the terminator must serve a valid certificate for a name you control, and it
must pass the request through to the loopback port unchanged, including the
`Authorization` header.

Any of the usual terminators does this. Below is **one minimal example**, not a
recommendation and not the only shape that works:

```nginx
location /wuwaterm-api/ {
    proxy_pass http://127.0.0.1:8788/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Two things to get right whatever you use:

- Decide whether the public prefix is stripped before the request reaches the
  service, and make the client's configured base address agree with that
  decision. A base address that resolves to no route answers `404`, which reads
  like a broken deployment and is not one.
- Do not publish anything the surface does not need. The service declares five
  paths; a rule that forwards a whole host to it publishes more than that.

The owner's own installation uses one particular terminator with a path route,
and the exact block, the validation command and the rollback for it are in
[Deployment](deployment.md). Treat that as one worked example rather than as the
supported configuration.

## Optional: Container Images From GHCR

From the next release (v0.4.0) onward there are two published images:
`ghcr.io/my-denia/wuwaterm` (runtime) and `ghcr.io/my-denia/wuwaterm-builder`
(builder). They exist to save the local image build; they do not replace the
source checkout, because the Compose files, the entrypoints, the data build and
the verification scripts are in the source.

Verify that the pull succeeds before you plan around it. If it is denied, build
from source — that is the path the rest of this document already describes, and
nothing here depends on the images.

```bash
docker pull ghcr.io/my-denia/wuwaterm:v0.4.0
docker pull ghcr.io/my-denia/wuwaterm-builder:v0.4.0
```

To use them, point the runtime services at the pulled image instead of the
locally built one. The Compose file already reads the runtime reference from an
environment variable:

```bash
WUWATERM_RUNTIME_IMAGE=ghcr.io/my-denia/wuwaterm:v0.4.0 \
  docker compose -f deploy/docker-compose.yml up -d --no-build
```

A later release may add a `deploy/docker-compose.ghcr.yml` overlay that does the
same for both services; until then, an `image:` override in a Compose overlay
file of your own is the equivalent.

## Upgrade

```bash
git fetch --tags
git checkout vX.Y.Z
docker compose -f deploy/docker-compose.yml build
```

Then rebuild and re-verify the data before restarting, because a release can
move the data pin or the schema version:

```bash
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder verify-db
```

Stop the services, promote the verified candidate over `data/terms.db`, start
again, and re-check `/readyz`. Take the backup below first: promotion is the one
step in this document that overwrites something.

An installation old enough to predate the separate `state/` and `state-api/`
directories needs a one-time state migration rather than a restart; that path,
including the requirement to stop the old runtime first, is the state-migration
section of [Deployment](deployment.md).

## Backup And Restore

Three things are worth backing up, and one is not.

| what | how |
| --- | --- |
| `.env` | Copy it. It holds every credential the service is given. |
| `state/` | Bot settings and the channel reply index. Plain JSON; copy it while the bot is stopped. |
| `state-api/` | The device credential store. **A SQLite database in write-ahead-log mode.** Stop the API first, or take an online backup through SQLite's own backup API. Copying the file while the process holds it open can silently lose the most recent commits. |
| `data/terms.db` | **Not worth backing up.** It is regenerable from the pinned upstream source with the build commands above, and rebuilding it is the only way to be sure of what is in it. |

An online backup of the credential store, without stopping anything, using
SQLite's own backup call — here through the `sqlite3` command-line shell, which
is not a dependency of this project and may need installing:

```bash
mkdir -p backup
sqlite3 state-api/devices.db ".backup 'backup/devices.db'"
```

Verify a restored credential store by a business count — `device list` should
show the devices you expect — rather than by an integrity check alone: an
integrity check passes on a database that is intact but stale.

To restore: stop the services, put `.env`, `state/` and `state-api/` back with
their original permissions (`.env` at `600`, `state-api/` at `700`), rebuild
`data/terms.db` from the pinned source if it is missing, and start again.

## Rollback

```bash
docker compose -f deploy/docker-compose.yml down
git checkout vPREVIOUS
docker compose -f deploy/docker-compose.yml build
```

Restore `state/` and `state-api/` from the backup taken before the upgrade,
rebuild and verify the database at that tag's pin, then start and re-check
`/readyz`. Rolling the source back without rolling the database back is the
mistake to avoid: the schema version travels with the source.

## Troubleshooting

| symptom | likely cause | what to do |
| --- | --- | --- |
| Bot starts and answers nothing | `TELEGRAM_BOT_TOKEN` empty, wrong, or revoked | Check the process log for the startup failure. The token is read from `.env`; a wrong token fails at startup rather than silently. |
| Bot answers in a private chat to nobody | `OWNER_USER_ID` unset | Empty means private chat rejects everyone, on purpose. Set it, or use the bot in a group. |
| `/readyz` returns `503` | The terminology database is missing or unreadable | Confirm `WUWATERM_DB_PATH` points at a file that exists and that the process can read; build it if it does not. |
| `verify-db` fails on a fresh build | The upstream checkout is not the pinned commit, or its version-provenance file does not say the expected version | This is fail-closed by design. Re-run `refresh-data`; if it still fails, the profile in `src/wuwaterm/constants.py` and the upstream repository have diverged and the pin has to be updated deliberately, not worked around. |
| `refresh-data` fails and writes nothing | Same fail-closed check, or no network access to the upstream repository | Read the message: it names which check failed. A partial data directory is not left behind for the build to pick up. |
| `401` with `unauthorized` from `/v1` | No token, a malformed token, or a revoked device | The header is `Authorization: Bearer wtd1.<device id>.<secret>`. Run `device list` to see whether that device is still active. |
| `403` with `forbidden` from `/v1` | The device has the wrong scopes | `translate` admits `POST /v1/translations`; `meta` admits `GET /v1/terms` and `GET /v1/meta`. Issue a device with both. |
| `429` with `rate_limited` | The per-device admission bound, per process | Raise `WUWATERM_API_RATE_LIMIT_PER_MINUTE`, or slow down. |
| `503` with `llm_unavailable` | No model configured, or the model call failed or timed out | If you configured none, this is the documented behaviour. If you did, check the endpoint, the key and `WUWATERM_API_LLM_TIMEOUT_SECONDS`. |
| `503` with `llm_budget_exhausted` | The per-minute model call budget for that process | Raise `WUWATERM_API_LLM_CALLS_PER_MINUTE`, or wait. Budgets are per process and are not shared between the bot and the API. |
| The API will not start: bind error | `WUWATERM_API_BIND` set to something that is not a numeric loopback address | It is refused on purpose. Bind loopback and publish through a TLS terminator. |
| The API will not start: port in use | Something else holds the port | Change `WUWATERM_API_PORT`, and change the reverse proxy target with it. |
| The desktop client says the endpoint is unreachable | The base address, the certificate, or the proxy route | The client verifies certificates and that cannot be turned off. Check the base address resolves to a real route under the prefix, not to the site root. |

Server-side, every HTTP request leaves exactly one completion record carrying
the same `request_id` the caller was given, written to standard error at `INFO`.
Correlating a client report with that record is the fastest way to tell a
request that never arrived from one that arrived and failed. See
[Validation](validation.md) for what those records may and may not contain.
