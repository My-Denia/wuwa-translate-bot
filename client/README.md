# WuwaTerm desktop client

A Windows desktop client for the wuwaterm HTTP API. It only calls the API
and renders what comes back — it never re-implements dictionary lookup,
direction detection, or any other translation pipeline step.

This package is not published anywhere (no PyPI, no package registry). It
lives entirely under `client/` with its own `pyproject.toml` and is not part
of the `wuwaterm` server wheel.

## What it does

- Translates plain text through the shared dictionary-first pipeline
  (`POST /v1/translations`), with an automatic or forced direction.
- Looks up official dictionary terms (`GET /v1/terms`).
- Shows service status: version, data profile, data commit, term count, and
  whether a translation model is configured (`GET /v1/meta`).
- Stores exactly one device credential in the Windows Credential Manager via
  `keyring`. The credential is never written to a config file, and never
  written to disk in plain text by this application at all.

## Configuring the server address

Settings → Server address takes one value: the base address of the configured
secure endpoint the service is published on. Everything else about the network
path is the deployment's business — this client only makes HTTPS requests to
the address you give it, and the API contract does not encode the path in any
way, so changing how the service is published changes nothing here except this
one string.

**There is no default address.** A fresh installation — and any launch where
the settings file cannot be read — starts *unconfigured*: the main window
shows `Server address: not configured` above the tabs, and every request is
refused with "No server address is configured, so no request was made. Set the
server address in Settings." instead of being sent anywhere. Earlier versions
substituted a development address on this machine whenever the setting was
missing, which turned "your setting is gone" into "the server is unreachable" —
a confident answer to the wrong question. When an address *is* configured, that
same line names it, so which server this client is talking to is always on
screen.

Two forms are accepted, and nothing else:

- `https://<host>[:<port>][/<path prefix>]` — any host. The server
  certificate is verified on every request, always. There is no setting,
  command-line argument or environment variable in this application that
  turns verification off.
- `http://127.0.0.1:<port>` (or `localhost`/`::1`) — this machine only, for a
  development service running on your own computer. Nothing leaves the host,
  so there is nothing to protect in transit. `http://127.0.0.1:8788` is the
  example the settings field shows as a hint; it is not filled in for you and
  is never used unless you type it.

Plain `http://` to any other host is refused, in the settings dialog and again
in the transport itself before a request is built: the device token travels in
a request header on every call, and an address typed or edited by hand is
exactly how it would otherwise end up crossing a network in the clear.

The port is whatever the deployment configured (`WUWATERM_API_PORT`, 8788 by
default) when you are talking to a service on this machine; for a published
endpoint the address is whatever the operator gives you, and it is normally
just the host name.

<!-- operations-note: the single place this client's documentation is allowed
     to name the host administration channel, so that nobody re-introduces it
     as a way for the application to reach the service. Pinned verbatim by
     tests/test_client_transport_policy.py. -->
> Operations note: SSH is how an operator administers the server host. It is
> not part of this client's path to the service and never a requirement for
> using it — the application starts no such process, manages no keys, and
> needs nothing running beside it. See `docs/deployment.md`.

## Where the settings are kept

The non-secret settings — the server address and the two timeouts — live in
`%APPDATA%\WuwaTerm\config.json`. That is the *roaming* user profile, which a
restart preserves and which is not a temporary location. The device token is
not in that file and never has been; it lives in the Windows Credential
Manager.

- **A save cannot leave a half-written file.** It writes a temporary file in
  the same directory, flushes it, and only then replaces the settings file in
  one step; the settings file is never opened for truncation. An interrupted
  save therefore leaves either the whole previous file or the whole new one —
  never a truncated one, which the client would read as unusable and which
  would now cost you the server address rather than being papered over. This
  is about an interrupted *write*; it is not a claim about what a power cut
  leaves on the disk platters.
- **The file is not recreated for you.** If it is deleted — by hand, or by a
  disk-cleanup tool — the client starts unconfigured and says so on screen;
  enter the address again in Settings and it is written back. This is not
  hypothetical: the file went missing on the owner's machine across a Windows
  restart on 2026-08-12, while the stored device credential was unaffected.
- **A hand-edited file is validated, not trusted.** An address the client will
  not use, one it cannot parse, or JSON it cannot read leaves the client
  unconfigured; a timeout outside 1–600 seconds is clamped, and a
  non-numeric one falls back to the default. Nothing from that file reaches
  the HTTP layer unchecked.

## Timeouts, retries and reconnection

There is no background connection to lose and nothing to reconnect: each
action opens an HTTP request on demand through a shared, pooled `httpx` client
and finishes when the response arrives. Connections are pooled and reused;
when a pooled connection has been closed by the other side, the next request
establishes a new one.

- **Two timeouts, both configurable.** `request_timeout_seconds` (default
  10s, Settings → Request timeout) applies to term lookups and status
  refreshes; `translate_timeout_seconds` (default 60s) applies to
  `POST /v1/translations`, which may be waiting on a translation model. Values
  from the settings dialog and from a hand-edited config file are clamped to
  1–600 seconds.
- **They are per-operation limits, not a total deadline.** `httpx` applies the
  value separately to connecting, writing, reading and waiting for a pooled
  connection, so it bounds how long the client waits *without progress*, not
  the wall-clock length of the whole call: something that keeps sending bytes
  can legitimately keep a request open past the configured number. Not the
  service itself, though — it enforces its own deadline as real elapsed time
  and answers 504 — so a call that runs long in this way means something
  between this computer and the service, not the service.
- **Which limit expires first, on the shipped defaults.** The client's 60s
  translate timeout is shorter than the service's own request deadline
  (`WUWATERM_API_REQUEST_TIMEOUT_SECONDS`, 90s by default), so a translation
  that simply takes too long ends as a client-side timeout, not as the
  service's 504. The service's deadline becomes the binding one only if you
  raise the client's translate timeout above it (the client allows up to
  600s).
- **A timeout is reported, never retried.** The request stops and the view
  shows "The request timed out." This client does not retry automatically:
  a translation request that has already reached the service may have spent
  model budget, and silently sending it again is not a decision an application
  should take for you. Press the button again to make a new request.
- **Unreachable service.** A connection failure is reported as
  "Could not reach the server…" and leaves the client usable; the next
  request tries again from scratch. No state is cached across the failure.
- **Cancellation stops the waiting, not the work.** Translate → Cancel ends
  this client's request: the status line says the request was cancelled and
  the buttons return to their normal state. Whether it stops anything else
  depends on how early it lands. While the request is still being handed over
  — waiting for a connection, connecting, or sending — the service does not
  yet have a whole request to act on, so nothing is translated and nothing is
  spent. Once it does have the whole request — and your text is small, so that
  window is short — Cancel no longer reaches the service: it is not told you
  stopped waiting, and it finishes the request, recording it as an ordinary
  completed one. If that request needed the translation model, the model call
  runs to the end and is paid for whether or not you are still waiting; a
  dictionary hit never reaches the model and costs nothing either way.
  Cancelling then frees the application without stopping the work, and without
  un-spending anything the request had already committed. The answer is
  discarded unread, and with it the request id, so such a request is not one
  you can quote to an operator afterwards. Pressing Translate again makes a new
  request, and one that reaches the model pays again.

## Getting and storing a device credential

There is no self-registration screen, and the service never generates or
prints credential material. The operator of the service generates a secret,
registers it against a device with `wuwaterm-api device issue` (which reads
the secret from standard input), and hands you the resulting token out of
band. The token is `wtd1.<device-id>.<that secret>`; the server keeps only a
salted scrypt verifier of the secret, so nobody can recover the token from
the server later — if it is lost, the operator revokes that device and
registers a new one.

The secret is at least 32 characters of printable ASCII with no spaces, so
the token is always a single line that can be pasted as-is. On first launch,
or later from
Settings → Enter/Change token, paste it in. The client stores it immediately
in the Windows Credential Manager and never displays it again.

A first launch therefore asks for two things, in two places: the device token
in the welcome dialog, and the server address in Settings. The window says
`Server address: not configured` until the second one is done; the credential
and the address are stored separately and are lost separately, so it is normal
to be asked for one and not the other.

## Removing the stored credential

Settings → Forget token removes the credential from the Windows Credential
Manager. You can also remove it directly from Windows: open Credential
Manager → Windows Credentials, find the WuwaTerm entry, and remove it.

## Building

```
py "-V:Astral\CPython3.12.13" -m venv client\.venv
client\.venv\Scripts\python.exe -m pip install -e "client[dev,build]"
client\build.ps1
```

This is a one-folder PyInstaller build; the result is
`client\dist\WuwaTerm\WuwaTerm.exe`. No code signing is performed.

## Running tests

```
client\.venv\Scripts\python.exe -m pytest -q
```

Tests use a mocked HTTP transport and an in-memory credential-store stand-in;
no network access, no running server, and no real Windows Credential
Manager writes are required.
