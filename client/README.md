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

The service binds to a loopback port on the server host only; it is not
reachable directly from the internet. The supported way to reach it today is
an SSH tunnel from this computer to that loopback port. The remote end must
be the port the deployment configured (`WUWATERM_API_PORT`, 8787 by default —
see `docs/deployment.md` for reading it back from the running service); the
local end is your own choice:

```
ssh -N -L 8787:127.0.0.1:8787 <ssh-target>
```

With the tunnel open, set the client's server address (Settings) to
`http://127.0.0.1:8787` — this is also the default. Only the local half of
that command has to match the address you configure here.

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
