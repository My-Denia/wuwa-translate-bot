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

Two forms are accepted, and nothing else:

- `https://<host>[:<port>][/<path prefix>]` — any host. The server
  certificate is verified on every request, always. There is no setting,
  command-line argument or environment variable in this application that
  turns verification off.
- `http://127.0.0.1:<port>` (or `localhost`/`::1`) — this machine only, for a
  development service running on your own computer. Nothing leaves the host,
  so there is nothing to protect in transit. This is the default,
  `http://127.0.0.1:8788`.

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
- **Cancellation.** Translate → Cancel stops the in-flight request
  immediately; the status line says the request was cancelled and the buttons
  return to their normal state.

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
