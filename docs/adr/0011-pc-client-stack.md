# ADR 0011: Desktop client stack and contract boundary

- Status: Accepted
- Date: 2026-08-10

## Context

With a versioned HTTP surface in place ([ADR 0009](0009-http-api-adapter.md)),
the owner needs a desktop application on Windows 11 rather than a chat client or
a terminal. The risk in adding a second codebase is not the UI toolkit — it is
that a client slowly grows its own opinions about translation (a local term
cache, its own direction detection, its own retry-and-substitute logic) until
two implementations disagree and the dictionary-first guarantee stops meaning
anything.

The client is also a credential holder, which makes where the token lives a
design decision rather than an implementation detail.

## Decision

Build `client/` as a separate project in this repository, with its own
`pyproject.toml`, never added to the `wuwaterm` distribution.

**Stack, as built:**

- **Python 3.12-compatible**, not pinned. `requires-python = ">=3.12"` is a
  floor, so 3.13 and later satisfy it; `build.ps1` checks that `client/.venv`
  exists and can import PySide6 and PyInstaller, but never checks the
  interpreter's version; and CI selects the `3.12` series without pinning a
  patch release. 3.12 is what is developed and tested against, and it is what
  the venv instruction in `build.ps1` suggests — nothing enforces it.
- **PySide6** (`>=6.7,<7`) for the GUI, with **qasync** (`>=0.27,<1`) driving
  asyncio on the Qt event loop, so HTTP work and the UI share one loop and an
  in-flight request can be cancelled from a button.
- **httpx** (`>=0.27,<1`) `AsyncClient` for transport.
- **keyring** (`>=25,<26`) for the credential, resolving to the Windows
  Credential Manager backend (`WinVaultKeyring`) on this platform; the backend
  actually in effect is displayed in the Status view rather than assumed.
- **PyInstaller** (`>=6.10,<7`, `build` extra) producing a **one-folder** build,
  `client/dist/WuwaTerm/WuwaTerm.exe`, driven by `client/build.ps1` from
  `client/WuwaTerm.spec`. `build.ps1` fails loudly if the client venv, PySide6
  or PyInstaller are missing rather than falling back to a system interpreter or
  another toolkit.
- **No code signing** (`codesign_identity=None`; stated in `build.ps1` and the
  spec) and **no publication** to any package index — `client/pyproject.toml`
  carries the `Private :: Do Not Upload` classifier, and packaging for the
  server wheel only reads `src/`, so `client/` cannot leak into it.
  `scripts/check_package_artifacts.py` fails if `wuwaterm_client` ever appears
  in a built distribution, so that stays true by gate rather than by intent.

**Contract boundary, as built:**

- **The client holds no translation logic.** `client/src/wuwaterm_client/api.py`
  issues requests and parses responses. There is no dictionary lookup, no
  direction detection, no term locking, no chunking and no local cache of terms.
  Direction is a request parameter (`to`), not a client decision: "Auto" simply
  omits it. What the client *does* add is presentation: `TermsView` formats
  `score` to two decimals, `StatusView` renders a null profile or commit as an
  unknown-value label and `llm_configured` as yes/no text, and `TranslateView`
  maps `kind` to a label and `dictionary_miss` to a note. Those are display
  decisions about values the server chose; no value shown is one the client
  computed, looked up or translated.
- **The client holds no Telegram concepts.** Every user-facing literal is meant
  to live in `client/src/wuwaterm_client/strings.py`, and
  `client/tests/test_ui_strings_source.py` enforces part of that statically: it
  fails a string constant or f-string passed **directly** as an argument to one
  of twenty named text-setting methods on `ui/` widgets. It does not see a
  literal buried in an expression — `setText(" | ".join(parts))` in
  `TranslateView` is exactly that shape, and it passes — nor a literal handed to
  a widget constructor. So the gate reliably catches the obvious way a Telegram
  word would arrive, and review still has to catch the rest.
- **The credential lives only in the OS credential store.**
  `client/src/wuwaterm_client/credentials.py` is the only module that touches
  `keyring`, and it delegates entirely to it. It is **not** the only module that
  handles the token value: the first-run and token dialogs collect it,
  `main_window.py` and `settings_dialog.py` hand it to `store_token`, and
  `api.py` reads it back to build the bearer header. The boundary that holds is
  "one module talks to the credential store", not "one module ever sees the
  secret". The config file never contains it — `config.py` does not see it, and
  `client/tests/test_config.py::test_config_file_never_contains_the_credential`
  pins that. Lifecycle: first-run dialog on launch when nothing is stored
  (masked entry), Settings → enter/change token, Settings → forget token
  (removes the Credential Manager entry). The token is never displayed again
  after entry.
- **`base_url` is configuration, not a constant.** It is persisted in
  `%APPDATA%/WuwaTerm/config.json` with the request timeouts, defaulting to
  `http://127.0.0.1:8787` — the local end of the SSH tunnel to the service's
  loopback port. Changing it is a Settings action; nothing else in the client
  assumes a host. One predicate decides whether an address is usable, and it is
  applied both where the address is entered and where it is read back from
  disk: scheme, host and port must parse (with the parser the client itself
  uses), no query, fragment or embedded credentials are allowed, and plain
  `http://` is accepted ONLY for this machine. The device token travels in a
  request header, so an `http://` address to any other host would put it on a
  network in the clear; `https://` is accepted anywhere.
- **Cancellation and error rendering come from the server's envelope.** The
  translate view keeps the in-flight `asyncio.Task` and cancels it on demand;
  the API wrapper turns `CancelledError` into a distinct `cancelled` state
  rather than a generic failure. Non-2xx responses are read as
  `{"error": {"code": ...}, "request_id": ...}` and rendered through a mapping
  keyed by the server's nine enumerated codes; four further codes (`offline`,
  `timeout`, `cancelled`, `unknown`) are produced only by the client for
  transport failures and user cancellation and are never sent by the server.
  Mirroring an external contract's constant names is naming this side of the
  same envelope — it is not translation logic.
- **Configuration failure is non-fatal, but not unchecked.** A missing,
  unreadable, malformed or unrecognized config falls back to defaults rather
  than refusing to start, and each recognized value is checked on the way in:
  timeouts are clamped to the range the Settings dialog enforces and rejected
  if non-finite, and an unusable address falls back to the default. "Never
  raises" must not mean a hand-edited zero or a `NaN` reaches the HTTP client.
- **The credential store is a boundary, not an assumption.** Every failure of
  the OS vault - including the native Windows errors its backend re-raises,
  which are not `keyring` exceptions - is converted into one application error
  at that boundary. A request reports it as an unusable credential; the
  first-run and Settings paths say the token was not stored, or that it may
  still be stored when forgetting fails. One place deliberately does not
  propagate it: `has_token()` answers `False` when the vault cannot be read,
  because the question it is asked ("should the first-run dialog open?") has the
  same answer either way. The cost is that a vault which is failing rather than
  empty prompts the owner to enter a credential again, and the underlying
  failure is not shown at that moment - it surfaces on the next store or
  request instead.
- **Environment proxies are not trusted.** The supported address is loopback,
  and httpx would otherwise route the bearer credential through an
  `HTTP_PROXY` that no `NO_PROXY` entry covers.

**Tests.** `client/tests/` runs against a mocked httpx transport and an
in-memory credential-store stand-in: no network, no running server, no real
Credential Manager writes. Coverage includes the bearer header, the `to`
parameter in both modes, every stable error code rendering its mapped message,
an unrecognized code falling back, cancellation (including a task cancelled
before it starts), connect-refused, timeout, a server-side deadline reported as
`504`, bodies that are not this service's, wire fields of the wrong type, an
unavailable credential store, address validation, the config/credential
separation, the packaging entry point, and widget construction smoke tests.

## Consequences

- Positive: **this client adds no second implementation of anything.** A
  question about translation behavior on the desktop is answered by reading the
  server, never by reading the client. (Scoped deliberately: it is not a claim
  that the repository has exactly one implementation. The linked-channel adapter
  keeps its own direction and exact-lookup sequence and does not call
  `application` — see [ADR 0009](0009-http-api-adapter.md) and
  `docs/architecture.md`, "Current coupling".)
- Positive: a contract change is visible as a diff in `docs/api/openapi.json`
  before the client is touched, because the drift gate runs server-side.
- Positive: forgetting a machine is an OS-level action the owner can also
  perform from Windows Credential Manager directly, independent of this app.
- Negative: an unsigned one-folder build triggers Windows reputation warnings on
  first run. Accepted — code signing is not authorized, and the artifact is
  never distributed to anyone else.
- Negative: PySide6 makes the artifact large and ties it to a desktop platform.
  Accepted for a single-owner tool; no web UI is planned or supported.
- Negative: the client cannot do anything while the tunnel is down. That is the
  intended consequence of a loopback-only service, and it is rendered as an
  explicit offline state rather than a retry loop.
- Constraint: the client is verified by a separate `windows-latest` CI job,
  because it is the only part of this repository that cannot be built or run on
  the Linux runners the server uses. That job creates the same client-local
  virtual environment an operator would, runs the client suite, builds through
  `build.ps1`, and uploads the artifact.
- The build gates itself: `build.ps1` runs the artifact's own `--self-check`
  after producing it. That rehearsal builds the `QApplication`, installs the
  qasync loop and constructs the `MainWindow`, off-screen, then exits without
  showing a window, requesting a credential or sending a request. It exists
  because the first real launch of a successfully built artifact failed on an
  entry point that could not import its own package, and then on an OpenSSL pair
  its own `_ssl` could not load — both of which this catches. It returns before
  `ensure_credential()`, so the first-run path is **not** rehearsed; a failure
  confined to that dialog would still reach the owner first. A produced file is
  not a working program, and CI now says so about the part it can see.

## Evidence

- `client/pyproject.toml` — dependencies, `Private :: Do Not Upload`,
  `requires-python`
- `client/build.ps1`, `client/WuwaTerm.spec` — one-folder build, no signing,
  `keyring.backends.Windows` hidden import
- `client/src/wuwaterm_client/app.py` — PySide6 + qasync loop wiring
- `client/src/wuwaterm_client/api.py` — pass-through models, bearer header,
  cancellation and transport mapping
- `client/src/wuwaterm_client/credentials.py`, `config.py` — credential in the
  OS store only; config holds `base_url` and timeouts
- `client/src/wuwaterm_client/errors.py`, `strings.py` — code-to-message map and
  the single source of user-facing text
- `client/src/wuwaterm_client/ui/` — first-run, settings, token, translate,
  terms and status views
- `client/main.py` — the packaging entry point, which imports absolutely
  because a PyInstaller entry script has no package context
- `client/tests/` — `test_api.py`, `test_config.py`, `test_credentials.py`,
  `test_packaging_entry.py`, `test_translate_view_status.py`,
  `test_ui_smoke.py`, `test_ui_strings_source.py`
- `.github/workflows/ci.yml` — the `client` job (`windows-latest`)
- `client/README.md`, `docs/api/openapi.json`
