# Sites private workbench

Owner-facing browser workbench under `site/`. It is **not** the in-process
web layer at `/wuwaterm-web`. That layer lives inside `wuwaterm-api`, is off
by default, and is documented in [web-presentation-layer.md](web-presentation-layer.md)
and [ADR 0014](adr/0014-private-web-presentation-layer.md). This page is the
Hosted / Cloudflare Worker product that landed as a metadata probe in pull
request #96 and as terminology-plus-translation in pull request #98.

The workbench is a same-origin BFF: the browser talks only to
`/api/meta`, `/api/terms`, and `/api/translations` on the Sites origin. A
server-side proxy in `site/lib/wuwaterm-proxy.js` calls the published
`/v1` contract on the VPS. The browser never holds a device token and never
sees the upstream URL.

## What it is not

- Not a presentation adapter and not a second translation pipeline. Direction,
  fuzzy ranking, term locking, LLM use, quotas, and authentication stay on
  the VPS.
- Not `/wuwaterm-web`. No `basic_auth`, no `X-Wuwaterm-Edge`, no session
  cookie, no in-process call to `application.py`.
- Not in the runtime or builder image. `deploy/Dockerfile` copies `src/` and
  `scripts/` only. `scripts/validate.py` does not run the Site suite.
- Not a visitor-authenticated product. UI copy that says the workbench is
  private is a label. The routes accept anonymous requests. Anyone who can
  reach the deployed URL — a browser or `curl` — can spend the upstream
  device token's rate limit and model budget.
- Not a public API. It does not proxy `/healthz`, `/readyz`, `/openapi.json`,
  or `wuwaterm-api device` commands. It does not store history, accounts, or
  D1/R2 data (`site/.openai/hosting.json` leaves `d1` and `r2` null).

## Environment (Worker / Sites, not the VPS `.env`)

These three values are required together. They belong in the Hosted runtime
environment, not in `deploy/env.example` and not in the bot or API
containers.

| Variable | Contract |
|---|---|
| `WUWATERM_API_BASE_URL` | Must be `https://<WUWATERM_API_ALLOWED_HOST>/wuwaterm-api/` (trailing slash optional on input only) |
| `WUWATERM_API_ALLOWED_HOST` | Lowercase FQDN. Rejects IPs, ports, `localhost`, `.local`, `.internal`, `.home.arpa` |
| `WUWATERM_SITE_DEVICE_TOKEN` | Bearer token sent upstream only. Format `wtd1.<device_id>.<secret>`. Issue it with `wuwaterm-api device issue` and grant both `meta` and `translate` |

Invalid or missing configuration returns HTTP 503 `{ "status": "unavailable", "reason": "site_not_configured" }` and does **not** fetch upstream.

Give this workbench its own device. Sharing the desktop client's token puts
both callers in one sliding window and one model-call budget
([Architecture](architecture.md#cost-topology-budgets-are-per-process-and-the-worst-case-is-their-sum)).

## Request path

```
Browser (same origin, no Authorization)
  GET  /api/meta
  GET  /api/terms?q=<query>
  POST /api/translations   { "text": "...", "to"?: "en"|"zh" }
        │
        ▼
site/app/api/*/route.ts  →  site/lib/wuwaterm-proxy.js
        │  HTTPS, host pinned, redirect: manual
        │  Authorization: Bearer <WUWATERM_SITE_DEVICE_TOKEN>
        ▼
https://<allowed-host>/wuwaterm-api/v1/{meta,terms,translations}
        │
        ▼
wuwaterm-api on loopback behind the operator's reverse proxy
```

Proxy hardening that is pinned by `site/tests/`: HTTPS only, exact host and
`/wuwaterm-api/` mount, no redirect follow, 65,536-byte streamed request and
upstream-response caps, a 65,536-JavaScript-string-unit translation guard, a
4,096-JavaScript-string-unit query guard, strict JSON and field allowlists,
and refusal to project the token, the base URL, or `Authorization` back to the
browser. Meta and terms time out at 8 seconds; translations at 100 seconds.

Those Worker guards are resource and generic input ceilings, not an upstream
acceptance promise. The API owns the domain checks after preparation/trim: its
public client envelope is 2,000 prepared translation characters and 200
trimmed query characters. The API request-body cap defaults to 32,768 bytes
and is operator-configurable, so it may reject a body below the Worker's byte
ceiling. The Site projects the API's stable
`payload_too_large`, `input_too_long`, and `invalid_request` errors.

Site success bodies are projected subsets of the published OpenAPI models.
Site errors use `{ "status": "unavailable", "reason": "...", "request_id"? }`,
not the VPS `{ "error": { "code", "message" }, "request_id" }` envelope.

Crawlers are asked to stay out (`site/app/robots.ts` disallows `/`; layout
and proxy responses send `noindex`). That is not an access control.

## Operator checklist

1. Issue a dedicated device with both scopes. Keep the secret out of the
   browser, out of `site/.openai/hosting.json`, and out of git.
2. Pin `WUWATERM_API_ALLOWED_HOST` to the hostname the reverse proxy already
   serves for `/wuwaterm-api/`.
3. Visitor authorization is a separate Hosted-platform control. Set and
   verify owner-only access in the platform control plane; do not treat URL
   secrecy as access control. Nothing in `hosting.json` or the route handlers
   enforces who may open the page.
4. Revoking the device in `state-api/devices.db` turns the workbench's
   upstream calls into `401` without touching Telegram, the desktop client
   (if it uses another device), or `/wuwaterm-web`.

## How it is checked

CI job `site` in `.github/workflows/ci.yml` (`site feasibility security`):

```bash
cd site
npm ci
npm test
npm run typecheck
npm run lint
npm run build
npm run verify:no-client-secret
```

`npm test` runs `site/tests/feasibility.test.mjs` and
`site/tests/product-v1.test.mjs`. The verify script forbids `WUWATERM_*`
names, absolute URLs, `Authorization`, and browser storage APIs in client
sources, and scans the production client bundle after `vinext build`.

Those tests mock upstream. They do not prove a live VPS or a Hosted ACL.
