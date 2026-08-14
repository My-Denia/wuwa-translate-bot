# The owner-private web presentation layer

A mobile-first browser interface for dictionary lookup and sentence
translation, for the owner's own use from a phone. It is a third presentation
layer over the same protocol-neutral pipeline the Telegram bot and the HTTP API
already use, and it runs **inside the API process** rather than as a service of
its own. The reasoning for that, and the cost it carries, are recorded in
[ADR 0014](adr/0014-private-web-presentation-layer.md).

This document extends [deployment.md](deployment.md); the route below is a
second block in the same site that already carries the API route, and every
precondition listed there applies here unchanged.

## It is off unless you turn it on

The layer is not mounted unless `WUWATERM_API_WEB_ENABLED` says so. With the
switch off there is no route, no sub-application, and no entry in the published
API document — the process behaves exactly as it did before this layer existed.
Turning it off again is a restart, and it is the first thing to do if the layer
is ever suspected in an API problem.

## Settings

| variable | default | meaning |
| --- | --- | --- |
| `WUWATERM_API_WEB_ENABLED` | `false` | Mount the layer at all. Accepts `1/0`, `true/false`, `yes/no`, `on/off`; anything else fails the process rather than being read as "off". |
| `WUWATERM_API_WEB_DEVICE_TOKEN` | — | The device token the browser session is mapped onto. Required when enabled. **Server-side only** — it is never sent to the browser and must never be typed into one. |
| `WUWATERM_API_WEB_EDGE_SECRET` | — | Shared marker the reverse proxy injects on every proxied request. Required when enabled: with it unset the layer refuses everything. |
| `WUWATERM_API_WEB_SESSION_TTL_SECONDS` | `43200` | Browser session lifetime. |
| `WUWATERM_API_WEB_MAX_SESSIONS` | `32` | Ceiling on live sessions. |

Issue the device token the normal way (`wuwaterm-api device issue`), give it
both scopes, and put it in the environment file the API container reads. It is
an ordinary device from the credential store's point of view — it can be listed
and revoked like any other, and revoking it ends the browser session on the
next request.

## The route

Two things are added to the **existing** site block — the same one that already
carries the API route. The prefix is not stripped: the mount path inside the
process and the public path are deliberately identical, so the application
never has to reconstruct where it lives.

```caddyfile
handle /wuwaterm-web/* {
    basic_auth {
        # `caddy hash-password` output. Not a plain password.
        owner <bcrypt-hash>
    }
    # The port from the deployment readback, not the default assumed here.
    reverse_proxy 127.0.0.1:8788 {
        # header_up is a reverse_proxy SUBDIRECTIVE. Written at handle level it
        # is not a valid directive and `caddy validate` rejects the file.
        header_up X-Wuwaterm-Edge "<the value of WUWATERM_API_WEB_EDGE_SECRET>"
    }
}
```

`caddy validate` has not been run against this block on the target host; it is
a precondition of the deployment, not something this document can assert.

`basic_auth` is the private protection layer: an unauthenticated request is
refused by the proxy and never reaches the application at all. The injected
header is the second half — the application refuses any request that arrives
without it, so reaching the loopback port directly does not get past the front
door either. Neither half is sufficient alone and both are cheap.

Apply it the same way as any other change to this file:

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date -u +%Y%m%dT%H%M%SZ)
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

**Rollback is deleting that block and reloading**, exactly as for the API
route. The service keeps running, still bound to loopback; removing the route
removes the exposure, because there is no second place the surface could be
reached from.

## From the phone

Open `https://<the-existing-site>/wuwaterm-web/`. The browser asks for the
basic-auth credentials once and the browser's own password manager can keep
them. Two tabs: **查词** for dictionary lookup, **翻译** for sentence
translation. The interface is in Chinese.

Nothing needs to be installed, and there is no sign-in inside the page: the
device token stays on the server, and what the browser receives is an opaque
session cookie marked HttpOnly, so page scripts cannot read it — and this page
ships no scripts at all.

## Checking it after a change

- `https://<site>/wuwaterm-web/` renders the lookup form.
- A known term returns its official English string.
- With the switch off, the same address returns `404`.
- Requesting it without the basic-auth credentials returns `401` from the proxy
  and produces no application log line.
- The API's own routes answer exactly as before, and `/openapi.json` lists the
  same paths it listed before the layer existed.

## What this layer deliberately does not do

No public registration, and no change to how the service is distributed. It
adds no second translation path: the same dictionary-first pipeline answers
here, spending the **same** rate limit and the **same** model-call budget as
the API, because it holds the same objects rather than building its own. That
is what keeps a third presentation layer from raising the deployment's ceiling.
