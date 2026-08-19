# Support Matrix

What is actually tested, on what, and what "supported" means for a project of
this size. Every row here is a measured fact about this repository rather than
an intention.

## Server

| | |
| --- | --- |
| Declared range | `requires-python >=3.11`, no upper bound (`pyproject.toml`) |
| Tested in CI | Python **3.11, 3.12, 3.13 and 3.14**, on `ubuntu-latest` — the `pytest (py3.x)` matrix, added in pull request #82. No version in that range is skipped. |
| Entry point CI runs | `python scripts/validate.py` — the same command a contributor runs locally, so a green local run and a green pull request are the same claim ([Validation](validation.md)) |
| Container image | `python:3.11-slim` (`deploy/Dockerfile`). The image pins the low end of the range; the matrix covers the rest. |
| Supported development platform | **Linux.** The suite is green there. |

### The server test suite on a Windows host is not supported

This is a platform limitation, not a preference, and it is worth naming
precisely so nobody spends an afternoon on it:

- **Directory `fsync`.** `scripts/deployment_manifest.py` makes a manifest
  replacement durable by opening the containing *directory* with `os.open` and
  syncing it. Windows does not allow a directory to be opened that way, so the
  call raises before the test body runs. That accounts for about 29 errors in
  `tests/test_deploy_scripts.py`, all through the same fixture.
- **Invalid handle when tests spawn subprocesses.** Tests that shell out to
  `git` or `python` fail nondeterministically with
  `OSError: [WinError 6] The handle is invalid`, depending on how the parent
  shell's standard handles are arranged. The same tests pass when run another
  way in the same checkout, which is what makes it a host property rather than
  a repository defect.

Neither is guarded by a platform skip today, so a Windows contributor running
`python -m pytest` sees red on a tree that CI reports green. Run the server
suite on Linux — a WSL checkout is enough, and a Linux run of the suite is
green at roughly 1028 tests.

The *client* suite is the opposite case: it runs on Windows, in the client's own
virtual environment, and everything about the client that can be checked by
reading text is in the server suite instead so that it runs on every pull
request without a Windows runner.

## Desktop client

| | |
| --- | --- |
| Operating system | Windows 10 and Windows 11, x64 — the **intended targets**, not a measured claim. CI builds and tests the client on GitHub's `windows-latest` runner image, which is not pinned to either desktop release, so a defect specific to one of them would not turn CI red. |
| Python to build | 3.12 or newer (`client/pyproject.toml` declares `requires-python >=3.12`); the client keeps its own virtual environment, separate from the server's |
| GUI toolkit | PySide6 (`>=6.7,<7`) |
| Build | one-folder PyInstaller build from the committed spec, via `client/build.ps1` |
| Signing | **None.** The build is unsigned and there is no installer, so Windows SmartScreen shows a warning the user has to click through. |
| Byte-for-byte equality between two builds | Not claimed and not checked. There is no lock file for the client and no comparison of build outputs. |
| Distribution today | a CI workflow artifact from the `desktop client build (windows)` job, retained for 90 days and requiring a GitHub login |
| Distribution from the next release (v0.4.0) onward | a release asset, `WuwaTerm-<version>-windows-x64.zip`, listed in `SHA256SUMS` |
| Tested in CI | the client's own suite on `windows-latest` with Python 3.12, followed by the build |

## Compatibility contract

| | |
| --- | --- |
| Client 0.1.x — what the tree carries today (`client/pyproject.toml`, `client/src/wuwaterm_client/__init__.py`), and what the Windows CI job builds and tests | speaks HTTP API `v1`, served by wuwaterm **>= 0.3.0** |
| Client 0.2.x — the first publicly distributed build, not cut yet | the same contract; the version bump travels with the release change, so until it lands there is no package metadata saying 0.2.0 |
| How the client learns the server's version | it reads `api_version` from `GET /v1/meta` |
| Where the contract lives | [`docs/api/openapi.json`](api/openapi.json), committed and drift-gated by `scripts/check_api_contract.py`, so a route, model or error code cannot change without the published contract changing in the same commit |

The owner-private web presentation layer is deliberately outside this contract:
it is off by default and has no entry in the published API document
([Web Presentation Layer](web-presentation-layer.md)).

## Telegram

| | |
| --- | --- |
| Library | `python-telegram-bot` 22.x (`>=22.7,<23`) |
| Delivery | long polling |

Long polling is a decision with a record, not an accident of setup:
[ADR 0003](adr/0003-long-polling-not-webhook.md).

## Game data

- Upstream: `https://github.com/Arikatsu/WutheringWaves_Data`
- Pinned commit: `6ce8d5eda49f2930da84d8846c144432142c7465`
- Pinned version: `GameVer 3.6.0 / ResVer 3.6.4 / Changelist 8464573`

The pin is enforced, not documented: the refresh checks the remote, the commit
and the upstream version-provenance file and stops rather than building from
anything else. The database itself is built locally and is never distributed.
Details are in [Data Refresh](data-refresh.md); the licence boundary is in the
README.

## What "supported" means here

This is a personal hobby project maintained on a best-effort basis. "Supported"
on this page means one thing only: **there is a test, running in CI, that would
go red if it broke.** It does not mean a response time, a maintenance window, or
a commitment to keep anything working for anyone in particular. There is no
service-level guarantee and no guarantee of a reply to an issue or a pull
request.

Issues are welcome anyway, and a good one is much more likely to get an answer
than a vague one. Where to send what — a question, a bug, a security report, a
change — is in [SUPPORT.md](../SUPPORT.md).
