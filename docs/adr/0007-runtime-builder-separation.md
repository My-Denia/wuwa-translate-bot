# ADR 0007: Runtime / builder separation

- Status: Accepted
- Date: 2026-08-05

## Context

Building the terminology database needs `git`, sparse checkouts, and optional
`pypinyin`. The long-running bot needs a smaller attack surface: no build tools,
no write access to game data, and no builder secrets channel.

## Decision

Use a multi-stage Docker build:

- **runtime** target — runs only `bot`; mounts `data/` read-only; receives
  secrets via Compose `env_file`; refuses builder CLI entrypoints.
- **builder** target — profile-gated Compose service; writable `data/`; has
  `git` and build dependencies; **no** `env_file` for runtime secrets.

In code, `build_pinyin` is imported only from builder write paths (lazy import
inside `db.insert_records`). Runtime import smoke tests assert `pypinyin` is
not required to import bot/lookup/sentence/cli.

## Consequences

- Positive: production container cannot casually rebuild or pull TextMaps.
- Positive: CI `deploy-boundary` job encodes the split.
- Negative: operators must use the builder profile for refresh/build/verify
  instead of “one swiss-army image”.
- Coupling note: `db` still knows builder provenance types; acceptable shared
  storage module with lazy pinyin import.

## Evidence

- `deploy/Dockerfile` targets `runtime` / `builder`
- `deploy/docker-compose.yml` services `wuwaterm` vs `wuwaterm-builder`
- `deploy/entrypoint.sh` exit code 64 for non-bot on runtime (CI asserts)
- `tests/test_runtime_imports.py`
- `scripts/check_architecture_boundaries.py` (no presentation → builder imports)
