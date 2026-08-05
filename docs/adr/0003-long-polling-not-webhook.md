# ADR 0003: Long polling (not webhook)

- Status: Accepted
- Date: 2026-08-05

## Context

Telegram bots can receive updates via long polling (`getUpdates`) or webhook
HTTPS endpoints. Webhooks need a public URL, TLS, reverse proxy, and careful
multi-instance semantics. This deployment is a single VPS hobby service with
host networking and no public application HTTP surface.

## Decision

Use long polling only: `Application.run_polling()` after handler registration.
Do not implement webhook delivery or document webhook HA.

## Consequences

- Positive: no inbound HTTP listener for Telegram; simpler firewall and image.
- Positive: matches single active consumer assumption (one token, one process).
- Negative: two concurrent pollers on the same token are unsafe (update races);
  multi-instance is unsupported rather than “scale out with sticky webhooks”.
- Enforcement: `scripts/check_non_goals.py` forbids webhook markers outside
  allowlisted docs/tests; CI runs that script on every PR.

## Evidence

- `src/wuwaterm/bot.py` `run_bot` → `app.run_polling()`
- `deploy/docker-compose.yml` `command: ["bot"]`, no webhook env
- `docs/deployment.md` (“uses long polling … does not configure webhook”)
- `scripts/check_non_goals.py`, `tests/test_non_goals.py`
