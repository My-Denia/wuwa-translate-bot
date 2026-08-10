# ADR 0010: Device-principal authentication for the HTTP adapter

- Status: Accepted
- Date: 2026-08-10

## Context

The HTTP adapter ([ADR 0009](0009-http-api-adapter.md)) needs an identity model.
Telegram's does not transfer: a chat id is issued by Telegram, is meaningful
only inside Telegram, and revoking it is a chat-allowlist operation. The HTTP
surface has exactly one human user today, but "one user" is not a reason to ship
a single shared key: a shared key cannot be withdrawn from one machine, and
nothing distinguishes a laptop that was lost from a laptop that is still in use.

The surface is loopback-only and reached over SSH, so the credential is not the
only barrier — but it is the barrier that decides *which* client is talking, and
it is the one that survives a mistake in the ingress configuration.

## Decision

Authenticate a **revocable device principal** per client machine.

- **Token format** `wtd1.<device_id>.<secret>`, presented as an HTTP bearer
  credential. The parser splits at exactly two separators, so a secret may
  itself contain dots (base64, PEM-ish and UUID-ish material all do) and still
  round-trip.
- **The operator supplies the secret; the service never produces or prints
  one.** `wuwaterm-api device issue --name "..."` reads the secret from standard
  input — hidden prompt on a terminal, one line when piped — and prints only the
  device id, name, scopes and creation time. No code path in this service emits
  credential material, so none can reach a log, a terminal recording or a
  captured command output through it. The token is
  `wtd1.<device_id>.<the secret the operator supplied>`, assembled by the
  operator on the machine where it will be stored.
- **Supplied material is validated, not trusted.** It must be printable ASCII
  with no spaces or control characters, so a credential that registers cleanly
  can always be sent in a request header; and at least `MIN_SECRET_LENGTH` (32)
  characters, so an operator cannot register something trivially guessable.
- **At rest: salted `scrypt`, not a bare digest.** Each device gets a fresh
  16-byte salt; the verifier is `scrypt(secret, salt, n=2**14, r=8, p=1,
  dklen=32)` — roughly 16 MiB and tens of milliseconds per verification.
  Comparison is `hmac.compare_digest`. An unknown device still spends one
  derivation, so a missing device and a wrong secret cost similar work.
- **Verification is bounded.** The derivation is deliberately expensive and runs
  *before* any per-device limit can apply, so `WUWATERM_API_AUTH_MAX_CONCURRENCY`
  (default 2) caps how many can run at once. The bound is held and released
  inside the worker thread, so a request cancelled by the time budget or by a
  client that walked away cannot leave a slot behind.
- **Uniform rejection.** Unknown device, wrong secret, malformed token, revoked
  device and an unusable store are all indistinguishable to the caller
  (`unauthorized`), so the endpoint cannot be used to enumerate device ids or to
  probe the server's state. An operator who needs to know *why* a store is
  unusable gets that at startup and from the CLI, where the message has an
  audience.
- **Scopes** are `translate` (`POST /v1/translations`) and `meta`
  (`GET /v1/terms`, `GET /v1/meta`); both are granted by default. A missing
  scope is `forbidden`, distinct from `unauthorized`.
- **Revocation stamps `revoked_at` and keeps the row**, together with
  `created_at` and `last_used_at`, so an operator can still see that a device
  existed and when it was withdrawn. `last_used_at` is written only for an
  **admitted** request — after the rate limit has passed — so a refused caller
  cannot drive an unbounded stream of writes into the credential store.
- **No registration endpoint.** Devices are registered and revoked by an
  operator with shell access on the host, over SSH
  (`docker compose ... run --rm wuwaterm-api device ...`).
- **Store location.** `state-api/devices.db`, a **sibling** of the bot's
  `state/` and never a child of it, because the bot mounts the whole of `state/`
  read-write; a child directory would have handed the bot process read-write
  access to the credential store. The file is created `0600` inside a `0700`
  directory, and the same restriction is applied to the SQLite `-wal` and `-shm`
  sidecars, which carry the same rows as the main file.
- **Break-glass: delete `state-api/devices.db`.** That revokes every device at
  once; the next start recreates an empty store.
- **The device id is the principal id.** There is no users table and no mapping
  from device to human. The documented trigger for adding a principals table is a
  second human user, or quotas/audit that must be attributed per person rather
  than per device. It is not implemented.

### Rejected alternative: server-generated secret with a bare `sha256` verifier

The original design had the service mint a 32-byte URL-safe secret, print the
full token once at issue, and store `sha256(secret)` — the standard argument
being that a high-entropy machine-generated secret makes a KDF unnecessary.

It was rejected for two independent reasons:

1. **Emitting the secret was flagged, at high severity, by the code scanning
   this repository already runs and merges on.** A code path that writes
   credential material to standard output is exactly the pattern such analysis
   looks for, and the finding is correct: the value then
   lands wherever that output lands — scrollback, a CI log, a terminal
   recording, a captured `docker compose run` transcript. Suppressing the alert
   would have been arguing with a true positive. Inverting the flow removes the
   category: with no code path that emits a secret, there is nothing to leak.
2. **Once the operator chooses the material, the premise for a bare digest is
   gone.** `sha256` without a KDF is only defensible for values the *server*
   generated with known entropy. Operator-chosen material has unknown entropy
   and may be reused from elsewhere, so a stolen store would be cheap to search.
   The length floor plus salted `scrypt` is what replaces the guarantee the
   server used to provide by construction.

## Consequences

- Positive: access is per machine and revocable per machine. A lost laptop is
  one `device revoke`, not a re-key of every client.
- Positive: no credential material exists in this service's output, at any point
  in its lifecycle. The store can only be searched offline, at scrypt cost.
- Positive: the failure surface is uniform, so the endpoint leaks nothing about
  which devices exist.
- Negative — **usability cost to the operator, accepted deliberately**: the
  operator must generate the secret themselves and feed it in on standard
  input, which is more work than reading one back from the command. Piping it
  requires the right invocation (`run --rm -T ... < file`), and a forgotten
  secret cannot be recovered — the only remedy is to revoke that device and
  register a new one. This is the direct trade for never emitting credential
  material.
- Negative: verification is intentionally slow. That is why it is bounded, and
  why the bound is a documented per-process budget rather than an implicit one.
- Negative: an operator who chooses weak material below the entropy the length
  floor implies is not fully protected by it. The floor and the KDF raise the
  cost of a stolen store; they cannot make a bad choice good.
- Constraint: the credential store must never become reachable from the bot
  container. That is a Compose-file property (`state-api/` is mounted only into
  `wuwaterm-api`) and is pinned by a deploy-script test.

## Evidence

- `src/wuwaterm_api/auth.py` — `DeviceStore`, `parse_token`, `_derive`,
  `_is_presentable`, `MIN_SECRET_LENGTH`, `SCRYPT_*`, `_restrict_permissions`,
  `authenticate`, `record_use`, `revoke`
- `src/wuwaterm_api/cli.py` — `device issue` reads the secret from stdin and
  prints only the device id
- `src/wuwaterm_api/app.py` — `authenticated_device`, `require_scope`,
  `_verify_credential`, `auth_slots`
- `src/wuwaterm_api/settings.py` — `DEFAULT_STATE_DIR = "state-api"`,
  `DEFAULT_AUTH_MAX_CONCURRENCY`
- `deploy/docker-compose.yml` — `../state-api:/app/state-api` on the API service
  only; `WUWATERM_API_DEVICE_DB_PATH` pinned empty
- `tests/test_api.py` — stored verifier is salted and slow to search; bad
  credentials are indistinguishable; revoked device rejected; scope enforced;
  rate-limited and unauthenticated requests never write to the store; every file
  carrying verifier material is restricted; a cancelled request does not leak a
  verification slot
- `tests/test_deploy_scripts.py`
  `test_api_state_directory_is_not_inside_the_bot_state_tree`
- `docs/deployment.md` "Device Credentials"
