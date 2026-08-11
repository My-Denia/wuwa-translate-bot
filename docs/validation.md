# Validation

## Offline Validation

```bash
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
.venv/bin/python scripts/verify_seed_terms.py data/terms.candidate.db --discrepancies goal-runs/wuwaterm-v2-translator/seed-discrepancies.json
.venv/bin/python scripts/verify_exact_hits.py data/terms.candidate.db --sample-size 500
.venv/bin/python scripts/verify_idempotent_build.py --data-dir data/wutheringdata --out-dir goal-runs/wuwaterm-v2-translator --profile arikatsu
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python scripts/check_architecture_boundaries.py
.venv/bin/python scripts/check_api_contract.py
.venv/bin/python -m pytest
uv lock --check
```

`verify_idempotent_build.py` compares SHA256 over LF-normalized SQLite logical
dumps, not raw database bytes, so Windows/Linux SQLite formatting differences
do not create false mismatches.

All database checks above target the candidate. They do not promote or replace
`data/terms.db`; production promotion belongs to the transactional deployment
workflow after every candidate gate passes.

## Live Telegram Smoke

Live Telegram smoke is owner-gated. If `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_TEST_CHAT_ID` are not supplied, only the live smoke criterion is
blocked; offline handler tests still validate the bot code.

`scripts/deploy_smoke.py` is a deployment reachability check, not a polling
handler E2E test. It verifies `getMe`, and when `TELEGRAM_TEST_CHAT_ID` is set
it sends one diagnostic message without printing the token or chat id. A real
handler E2E still requires observing the bot's reply from Telegram.

`check_repo_hygiene.py` and `check_non_goals.py` guard against committing
generated data, TextMap content, SQLite DB files, runtime settings, channel
reply indexes, tokens, API keys, and real Telegram identifiers.
`check_architecture_boundaries.py` guards forbidden import directions between
presentation, domain, and builder modules, and enforces the HTTP adapter's
four-module import allowlist into `wuwaterm` (see
[Architecture](architecture.md)).

## Gates For The HTTP API And The Desktop Client

| Gate | What it proves | Where it runs |
|---|---|---|
| `scripts/check_api_contract.py` | The committed `docs/api/openapi.json` still equals the document the application generates, and the product token pins hold in that JSON artifact (which the text scanner does not read). A route, model or error code cannot change without the published contract changing in the same commit | Offline command above; CI `pytest (py3.11)` / `pytest (py3.12)` jobs, before the suite |
| `tests/test_client_transport_policy.py` | The desktop client cannot regress into reaching the service through the operator's administration channel, and certificate verification cannot be turned off by an edit anywhere in the client tree, the deploy scripts or this runbook. Text gates over the shipped client surface, `docs/deployment.md` and `deploy/*` | The repository `pytest` run — deliberately here rather than in the client suite, so it runs on every pull request without a Windows runner or the client's dependencies |
| `client/.venv/Scripts/python.exe -m pytest` (in `client/`) | The client's own behaviour: transport refusals, the request-target guard, credential storage, cancellation, error rendering, and that every displayed string comes from the strings module | CI `desktop client build (windows)` job on `windows-latest` |
| `client/build.ps1` | The one-folder PyInstaller artifact builds from the pinned spec and passes its own `--self-check`, and the build leaves nothing untracked in the working tree | Same CI job; the artifact is uploaded there |
| `tests/test_api_request_logging.py` | Every request leaves exactly one server-side record — on the authenticated path, on `401`, on a failure that never reaches a handler — carrying the same `request_id` the caller was given; and that no record holds a credential, a raw device id, submitted text, or a request target as the caller wrote it. Asserted against captured records rather than by reading the source, because a field added later is invisible to inspection | The repository `pytest` run |

The client gates are split on purpose. Everything that can be checked by
reading text runs in the main suite on Linux; only what genuinely needs Qt,
`keyring` and a Windows toolchain runs on the Windows runner.

Neither the API contract gate nor the client build proves anything about a
deployed service. A request answered on the service host is evidence about the
process; evidence about the published endpoint has to come from the client
machine (see [Deployment](deployment.md)).

## Server-Side Request Records

The HTTP adapter writes one line per request to standard output when it is
started by `wuwaterm-api serve`. `WUWATERM_API_LOG_LEVEL` (`CRITICAL`, `ERROR`,
`WARNING`, `INFO`, `DEBUG`; default `INFO`) sets the level; an unusable value
stops the serve path with exit code 2 and never blocks the credential
subcommands. Nothing is configured at import time, so a program that embeds the
application keeps its own logging arrangement.

Correlating a client's report with the server is the point of the record:
`request_id` in the response header and body is the `request_id` in the line,
and reading it back with `docker logs` is described in
[Deployment](deployment.md#reading-the-request-log). What the records may and
may not contain is a test, not a convention — see the table above.

## Windows Reference

Windows commands are still supported when needed:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
$env:TELEGRAM_BOT_TOKEN="..."
$env:WUWATERM_DB_PATH="data\terms.db"
```
