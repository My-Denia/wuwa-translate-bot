# WuwaTerm Sites shared beta candidate

As of 2026-09-02, this checkout contains a shared-pool public product **candidate**.
The Hosted Site remains owner-only on the previously accepted clean version.
Local code, a saved version, an access policy, and a successful public production
deployment are separate facts. This document does not announce a public launch.

The browser calls only same-origin `/api/pool`, `/api/terms` and
`/api/translations`. Initial rendering reads aggregate pool status without
contacting the VPS. Optional `/api/meta` remains gated and exposes only term
count, data version and the existing request correlation ID. The server-only
proxy uses the existing dedicated device credential and authoritative VPS
pipeline. Dictionary lookup, ranking, direction, term locking and model calls
remain on the VPS. The Telegram bot, Windows client and API contract are unchanged.

## Shared pool contract

There is no per-visitor, per-IP or personal fair-use allowance. A single caller
can exhaust the entire pool. D1 stores one singleton row of aggregate counters:
second/minute/day window numbers and admitted request/input counts. It never
stores or derives visitor identifiers, IPs, hashes, input, output, request IDs,
accounts or history. No cleanup scheduler is required: the row is overwritten
as active windows advance; inactive old aggregate windows can remain until
another admission. This is not a time-based deletion promise.

| Boundary | Candidate ceiling |
| --- | --- |
| All upstream routes combined | 6 admissions / fixed UTC minute; 1 / UTC second |
| Translation | 1 admission / fixed UTC minute |
| Terms | 240 admissions / UTC day |
| Translation | 30 admissions and 12,000 raw Unicode characters / UTC day |
| Optional meta | 60 admissions / UTC day |
| Inputs | query <=200 trimmed Unicode characters; translation <=2,000 raw characters; streamed body <=32,768 bytes |

Terms and translations have independent daily counters. Disabling translation,
exhausting its count or character pool, or VPS model unavailability does not
disable terms. Terms can still be busy under the total short window, its own
daily cap or infrastructure failure.

One atomic conditional SQLite UPSERT checks and increments all applicable
counters. No read-then-write race or partial multi-counter reservation.
Database clock determines the windows. Admission precedes exactly one BFF
fetch. There is no automatic retry or refund on error, cancellation, ambiguous
DB response or upstream timeout. Missing/invalid settings, missing D1, failed
queries and acquisition exceeding 1s fail closed, with no upstream call.
Rejected requests do not count as admitted requests.

Candidate ceilings reserve headroom against the separately verified upstream
device contract, including adjacent fixed-window bursts and dispatch delay.
The extra second gate reduces bursts. Network delay and other users of the
same credential can still produce upstream429; upstream admission remains
authoritative. Live observations belong in private operator evidence, not this
public document. Candidate limits are not demonstrated sustainable throughput.

The pool limits admitted upstream work and translation input. It does not
guarantee a currency bill, fairness, availability, DDoS resistance, bounded
inbound Worker/D1 requests, or a shared budget across the API and Telegram
processes. A <=2,000-character client request uses the existing single model
attempt path, when dictionary-first processing requires it.

## Runtime configuration

Existing secret values stay server-only in the Sites environment. Never put
them in hosting metadata, browser code, source control, evidence or URLs.

| Variable | Contract |
| --- | --- |
| `WUWATERM_API_BASE_URL` | Existing pinned HTTPS mount; unchanged |
| `WUWATERM_API_ALLOWED_HOST` | Existing exact lower-case hostname; unchanged |
| `WUWATERM_SITE_DEVICE_TOKEN` | Existing dedicated device; unchanged |
| `WUWATERM_SHARED_POOL_ENABLED` | Must explicitly equal `true`; otherwise all upstream requests fail closed |
| `WUWATERM_TRANSLATION_ENABLED` | Explicit `false` initially; `true` only after acceptance and owner decision |
| `WUWATERM_PUBLIC_ORIGIN` | Absent until separately approved public gate; exact HTTPS Sites origin with no trailing slash, credentials, path, port or query |

`.openai/hosting.json` declares logical `DB` only; no real database ID or
secret. `db/schema.ts`, `drizzle.config.ts` and generated `drizzle/` migrations
own schema changes. Runtime never creates/alters tables. Sites owns production
resources and applies migrations. A failed deployment can leave migrations
applied: inspect the applied boundary before retrying and never edit applied
migration history.

Public SEO is disabled by default. A validated operator origin enables canonical,
robots and sitemap metadata; it never derives from request or forwarded hosts.
All API responses remain no-store/noindex and public UI errors use fixed copy.

## Validation and publication gates

From `site/`:

~~~sh
npm ci
npm test
npm run typecheck
npm run lint
npm run build
npm run verify:no-client-secret
~~~

Tests include existing proxy failure/secret contracts, real SQLite boundary
fixtures and a genuine local Cloudflare D1/Worker concurrency test using a
synthetic upstream. They do not prove Hosted anonymous dispatch or production
request correlation. Browser acceptance uses the candidate UI and BFF with
local D1 and synthetic results; test harnesses are excluded from the production
entrypoint.

Before public access, independently authorize and verify the exact source
commit/push, D1/environment, saved version and private deployment. Complete
owner-session acceptance, failure recovery, secret projection, a real successful
VPS request-ID correlation and rollback readiness. Verify owner/custom ACL with
exactly one account and no groups or external visitors immediately before staging.
Public slug and public ACL are separate final owner decisions.

Private ACL deliberately rejects signed-out traffic. Local anonymous handler
tests cannot be called Hosted anonymous acceptance. Final signed-out production
smoke requires an explicitly authorized controlled public transition; restore
owner-only immediately on failure. If anonymous Hosted proof is required before
any such transition, remain private rather than bypass authentication.

Rollback: restore owner-only ACL first; restore prior runtime settings securely
and privately redeploy the accepted clean version7/source tree. Leave new D1
tables unused rather than dropping them. Never clear live counters as a recovery
shortcut. Turning off only translation preserves the independent lookup pool.

The prior trusted-IP design and M0 failure remain historical evidence, not an
implementation route. See [shared-only design](superpowers/specs/2026-09-02-wuwaterm-shared-beta.md).
