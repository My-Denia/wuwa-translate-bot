# Contributing

Thanks for looking. WuwaTerm is a personal, MIT-licensed hobby project with one
maintainer, so contributions are welcome but reviewed best-effort — there is no
promised turnaround. Small, focused changes are the ones that get merged.

Before you build something, read [Non-Goals](#non-goals). Some things this
project has deliberately refused are enforced by a gate, and a pull request that
adds one will be rejected no matter how well it is written.

- Bug reports and questions: [SUPPORT.md](SUPPORT.md).
- Vulnerabilities: [SECURITY.md](SECURITY.md) — never a public issue.

## What This Repository Contains

Four surfaces share one application layer:

- a Telegram bot,
- an HTTP API under `/v1`,
- an owner-private web presentation layer that lives inside the API process and
  is off by default,
- a Windows desktop client under `client/`, which only calls the API.

Terminology comes from a pinned upstream game-data repository and is built into
a local SQLite dictionary. Wuthering Waves game data and terminology are
copyright Kuro Games; this project never redistributes them, and no generated
database or upstream text may be committed.

[Architecture](docs/architecture.md) is the map, and
[docs/adr/README.md](docs/adr/README.md) records why the shape is what it is.

## Getting The Code

```bash
git clone https://github.com/My-Denia/wuwa-translate-bot.git
cd wuwa-translate-bot
```

Work on a branch and open a pull request against `main`.

## Setting Up

The server needs Python 3.11 or newer. Either path works; use the first if you
have [uv](https://docs.astral.sh/uv/).

With uv:

```bash
uv venv .venv
uv sync --locked --extra dev
```

With a plain virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

`--locked` makes uv honour `uv.lock` rather than re-resolving it; CI has a job
that fails when the lock has drifted from `pyproject.toml`, so do not edit one
without the other.

**Host support.** The server test suite is green on Linux and is not supported
on a Windows host — several tests exercise POSIX-only behaviour and fail there
for platform reasons rather than code reasons. If you develop on Windows, run
the suite under WSL or a Linux container. See
[docs/support-matrix.md](docs/support-matrix.md) for what is supported where.

## The One Command

```bash
python scripts/validate.py
```

That is the whole local gate, and it is exactly what the server test matrix
runs in CI — the same file, not a second list that drifts from this one. It is
not the whole pull request, though: CI also runs the lock-drift check, the
wheel and sdist build with its install audit, the Windows desktop-client build,
and the Docker runtime and builder boundary, and none of those is in this
command. If your change touches the lock file, the packaging metadata, the
client, or anything under `deploy/`, a green run here is necessary and not
sufficient — see the job list further down.

```bash
python scripts/validate.py --list        # what it would run; runs nothing
python scripts/validate.py --quick       # everything except the test suites
python scripts/validate.py --only ruff   # a single step by name
python scripts/validate.py --client      # additionally: the desktop client suite
```

The run stops at the first failing step, names it, and exits with that step's
status. The steps, in order:

| Step | What it is for | What a failure means |
|---|---|---|
| `hygiene` | Keeps generated data out of the index | A generated database, upstream game text, or runtime state would be committed |
| `non-goals` | Pins four product decisions as text | A boundary this project has refused was crossed in a file |
| `architecture` | Guards import directions between layers | An import crosses a boundary the architecture forbids |
| `api-contract` | Ties the published contract to the code | The committed `docs/api/openapi.json` no longer equals the document the application generates |
| `ruff` | Correctness linting only — rules `E4`, `E7`, `E9`, `F` | An unused import, an undefined name, a broken f-string, or similar. Formatting is not enforced, so do not send a formatting sweep |
| `pytest` | The repository test suite | The suite is red |

[docs/validation.md](docs/validation.md) has the longer account, including the
candidate-database checks that belong to a data refresh rather than to every
commit.

Continuous integration runs `python scripts/validate.py` on Python 3.11, 3.12,
3.13 and 3.14 on `ubuntu-latest`, plus four more jobs: a uv lock-drift check, a
wheel and sdist packaging audit with a clean-environment install smoke, a
Windows desktop-client build, and a Docker runtime/builder boundary check. CI
publishes nothing.

## Working On The Desktop Client

The client is a separate package with its own virtual environment. It needs
Python 3.12 or newer, and it pulls in PySide6, `keyring` and `qasync` — none of
which the server environment carries, and it carries none of the server's
dependencies either. No single interpreter can run both suites, which is why
`--client` runs under `client/.venv`.

On Windows:

```powershell
py "-V:Astral\CPython3.12.13" -m venv client\.venv
client\.venv\Scripts\python.exe -m pip install -e "client[dev,build]"
client\.venv\Scripts\python.exe -m pytest
client\build.ps1
```

`client/build.ps1` produces the one-folder PyInstaller artifact. From the
repository root, `python scripts/validate.py --client` runs the same client
suite through the entry point. Everything about the client that can be checked
by reading text — its transport policy, its documentation claims — is in the
main suite instead, so it runs on every pull request without a Windows runner.
[client/README.md](client/README.md) is the client's own document.

## What Makes A Pull Request Easy To Accept

- **One concern.** A small change that does one thing is reviewed in one pass. A
  large change that does four is reviewed in four, if at all.
- **Tests that would fail without it.** For a fix, the test should be red on the
  code as it stands and green with the change. A test that passes either way
  documents nothing.
- **An entry under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md).** Say what
  changed and why, in the voice of the surrounding entries.
- **README parity.** `README.md` (Chinese, the front page) and
  `README.en.md` (English) are kept aligned: if you touch one, touch the other,
  and keep the headings and code fences matching between them.
- **Documentation updated when behaviour changed.** A document that promises
  something the code does not do is a defect here, and there are text gates that
  say so.
- **No secrets and no generated data.** Never commit a generated SQLite
  database, upstream TextMap or game data, runtime state (`state/`,
  `state-api/`, `chat_settings.json`, `channel_replies.json`), or a `.env` file.
  The `hygiene` gate catches most of this; it is not a reason to stop looking.
- **`python scripts/validate.py` run locally**, with the result in the pull
  request description.
- Signed commits are welcome. The maintainer signs; you are not required to.

## Non-Goals

Four product decisions are settled and enforced as text by
[`scripts/check_non_goals.py`](scripts/check_non_goals.py). Please do not send a
change that adds any of them — the gate goes red and the pull request cannot
merge:

1. **No callback-URL registration for Telegram.** The bot uses long polling; see
   the decision records in [docs/adr/README.md](docs/adr/README.md).
2. **No inline-query surface.** The bot answers commands and one channel
   listener, not inline queries.
3. **No synonym layer over the dictionary.** Lookups resolve against official
   terminology, and adding a second, unofficial naming layer defeats the point
   of the project.
4. **No free-text listener beyond the single linked-channel one.** Exactly one
   listener exists — automatic forwards from the linked channel — and the gate
   asserts that it is that one and nothing else.

The gate reads the whole tree, so the banned identifiers cannot appear in prose
either. If you believe one of these decisions should change, open an issue and
argue it before writing code.

## How Review Works

Pull requests are reviewed by automated reviewers and by the maintainer:

- A Claude code-review workflow runs on every pull request, alongside
  repository-level review bots.
- CodeQL default setup scans the repository, weekly and on pull requests.
- The maintainer reads it when they can. This is a spare-time project; a quiet
  week is not a rejection, and a nudge on the pull request after a while is
  fine.

Automated review is advisory. A bot comment that is wrong should be answered,
not silently obeyed — say why in the thread.

## Reporting Instead Of Fixing

Reporting is a contribution. The issue forms ask for the fields that make a
report actionable — version, surface, environment, steps, expected versus
actual, and logs with the secrets removed:
<https://github.com/My-Denia/wuwa-translate-bot/issues/new/choose>

## Licence

By contributing you agree that your contribution is licensed under this
project's [MIT licence](LICENSE).
