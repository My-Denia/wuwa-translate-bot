# WuwaTerm shared-only public beta candidate

Owner decision: 2026-09-02. Status: local candidate; public launch unverified.
Supersedes the visitor/IP/HMAC design. M0 FAIL is immutable historical evidence.

Use the existing same-origin BFF and authoritative dictionary-first VPS
translation path. No accounts, login, history, visitor identity, IP processing,
RAG, external edge or second translation implementation. No API, Telegram,
desktop or term database changes.

The product is a shared pool, not a personal allowance. One visitor may exhaust
all admitted requests. Terms remain independently eligible after translation
is disabled or its daily count/character allowance is exhausted, subject to
the short-window total gate, term pool and service health.

Implementation and operational contracts live in [Sites](../../sites.md).
The limits, rejection and retention semantics there are authoritative for this
candidate. A single D1 row holds aggregate windows and counts only. Conditional
UPSERT atomically checks the second/minute/day gates; no refunds or retries.

The primary UI exposes term lookup and sentence translation with separate
inputs, pool status, result provenance labels, controlled errors and cancellation.
Privacy and limits are first-class pages. Public copy explains cost protection
and lack of fairness; it exposes no operational secrets or provider details.
The candidate is noindex until a validated operator public origin is enabled.

Private Hosted staging, source publication, runtime settings and D1 migration
require separately authorized operations. Public slug and public ACL are final
independent owner boundaries. Local tests cannot certify Hosted anonymous
dispatch; actual signed-out acceptance requires a controlled, owner-authorized
final transition and immediate owner-only rollback on failure. Remaining private
is a valid outcome when this evidence cannot be obtained.
