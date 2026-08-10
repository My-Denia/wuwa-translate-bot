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

## API And Desktop Client Gates

`check_api_contract.py` regenerates the OpenAPI document from the live
application and byte-compares it against the committed snapshot
`docs/api/openapi.json`. A wire-shape change that is not committed in the same
change fails the gate. Refresh the snapshot deliberately and review the diff:

```bash
.venv/bin/python scripts/check_api_contract.py --write
```

The same script re-applies the repository's product token bans to that JSON
artifact. `check_non_goals.py` scans text suffixes only and does not read
`.json`, so without this the committed snapshot would be the one file where a
banned marker could hide.

`check_architecture_boundaries.py` additionally enforces the HTTP adapter's
import allowlist: `src/wuwaterm_api` may import only `wuwaterm.application`,
`wuwaterm.models`, `wuwaterm.translation_policy` and `wuwaterm.logging_utils`,
never the bare `wuwaterm` package root and never the Telegram SDK, with
`TYPE_CHECKING` given no exemption. What that guard checks is import
statements — so it makes the *dependency direction* checkable rather than
reviewable. It does not read behavior: an adapter that reached the database
through `sqlite3` or a model endpoint through `httpx` directly would satisfy it.
Read it as "the API cannot quietly reuse the domain modules and drift", not as
"a second pipeline is impossible".

Packaging is audited on built artifacts, not on the source tree. The audit
requires the `wuwaterm_api` package members in **both** the wheel and the sdist,
and the `wuwaterm-api` entry point in the **wheel** — `audit_sdist` does not
check entry points, because an sdist contains no generated entry-point metadata
to check. The sdist's entry point is covered instead by the CI step below, which
installs the built sdist into a clean virtualenv and runs the console script. So
a packaging change that drops the HTTP adapter fails the audit, and one that
drops only the script declaration fails the clean-venv smoke. The audit also
fails if `wuwaterm_client` ever appears in a distribution:

```bash
.venv/bin/python -m build
.venv/bin/python scripts/check_package_artifacts.py dist/*.whl dist/*.tar.gz
```

Deploy topology is covered by `tests/test_deploy_scripts.py`, which runs as part
of `pytest`. It pins the loopback-only bind and the separate API state
directory, that `state-api/` is not inside the bot's state tree, that both
serving containers are stopped, restarted and read back together, that a
rollback restores only the surfaces that were RUNNING and never restores a
database underneath a container it could not stop, that the Docker context
excludes the API state and the SQLite sidecars, that `.env.example` and
`deploy/env.example` stay byte-identical and cover the API surface, and that
the documented operator commands use the same endpoint and the same configured
port the updater gates on. Compose and shell syntax are checked separately:

```bash
sh -n deploy/*.sh
cp .env.example .env && docker compose -f deploy/docker-compose.yml config -q && rm .env
```

The desktop client has its own test suite and its own virtual environment. It
targets Python 3.12, but nothing pins it: `client/pyproject.toml` declares a
`>=3.12` floor, `build.ps1` checks that the venv can import PySide6 and
PyInstaller without checking the interpreter's version, and CI selects the 3.12
series without a patch release. It is Windows-only and is **not** part of
`python -m pytest` at the repository root:

```powershell
client\.venv\Scripts\python.exe -m pytest
client\build.ps1
```

The client tests use a mocked HTTP transport and an in-memory credential-store
stand-in: no network access, no running server, and no real Windows Credential
Manager writes. `client\build.ps1` produces `client\dist\WuwaTerm\WuwaTerm.exe`,
performs no code signing, and then runs that artifact's own `--self-check`: an
off-screen start-up rehearsal that builds the `QApplication`, installs the
qasync event loop and constructs the `MainWindow`, then exits without showing a
window, requesting a credential or sending a request. That covers the failures
that made this gate necessary — a frozen build that cannot import its own
package, or is missing a Qt plugin, or ships an SSL library its interpreter
cannot load. It stops short of the credential flow: `--self-check` returns
before `ensure_credential()`, so a first-run dialog that failed to construct
would still pass the rehearsal and surface at the owner's first launch.

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
presentation, domain, and builder modules (see [Architecture](architecture.md)).

## Where Each Gate Runs

| Gate | CI | Local | VPS only |
|------|----|-------|----------|
| `check_repo_hygiene.py`, `check_non_goals.py`, `check_architecture_boundaries.py` | yes (`test`, py3.11 + py3.12) | yes | — |
| `check_api_contract.py` (OpenAPI drift + token bans on the snapshot) | yes (`test`) | yes | — |
| `pytest` incl. `tests/test_api.py` and `tests/test_deploy_scripts.py` | yes (`test`) | yes, except the deploy harness, which needs POSIX directory fsync | — |
| `uv lock --check` | yes (`lock`) | yes | — |
| wheel/sdist build, `twine check --strict`, `check_package_artifacts.py`, clean-venv install of both artifacts and `wuwaterm-api --help` | yes (`package`) | yes | — |
| `sh -n deploy/*.sh`, `docker compose config -q`, runtime/builder image build | yes (`deploy-boundary`) | yes, with Docker | — |
| Runtime image refuses `build-db` / `refresh-data` / `verify-db` with exit 64, and serves `bot`, `api` and `device` | yes (`deploy-boundary`) | yes, with Docker | — |
| Desktop client `pytest`, `build.ps1` and the artifact's start-up self-check | yes (`client`, `windows-latest`) | yes, Windows only | — |
| API readiness over loopback inside the running container (`/readyz`) | no | only against a locally served API | yes, run by `deploy/vps-update.sh` |
| Both container image ids match the validated image; `.deploy_commit` matches the deployed revision | no | no | yes |
| Live Telegram smoke (`scripts/deploy_smoke.py`) | no | owner-gated | yes |
| Device credential issue / revoke round trip, and a real client request over the SSH tunnel | no | no | yes |

CI never touches the VPS, never holds a device credential, and never opens a
tunnel. The "VPS only" column is not uniform, though, and the difference
matters when something goes wrong:

- **Run by `deploy/vps-update.sh`, and gating on it**: the in-container
  `/readyz` wait, the image-id and `.deploy_commit` readback, and the bot smoke
  (`scripts/deploy_smoke.py`, invoked with `TELEGRAM_TEST_CHAT_ID` blanked so
  the updater path sends no diagnostic message). A failure aborts the deployment
  and triggers the transactional rollback. Note what the record actually is:
  `.deployments/<commit>.json` carries image and database provenance and has no
  readiness or smoke field, but it is written only after those steps have
  passed — so a manifest existing for a commit is the evidence that they did,
  and a failed deployment leaves no manifest rather than a failing one.
- **Manual operator validation, not recorded anywhere by the updater**: the
  device issue/revoke round trip and a real client request over the SSH tunnel.
  Nothing automates them and nothing fails if they are skipped; they are how an
  operator convinces themselves the surface works end to end, and the record is
  whatever the operator writes down.

## Windows Reference

Windows commands are still supported when needed:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
$env:TELEGRAM_BOT_TOKEN="..."
$env:WUWATERM_DB_PATH="data\terms.db"
```
