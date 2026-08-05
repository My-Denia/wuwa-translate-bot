# ADR 0002: Modular monolith

- Status: Accepted
- Date: 2026-08-05

## Context

Workload is one bot process on one VPS: command translations, linked-channel
auto-translate, and occasional offline dictionary builds. Splitting into
microservices, message buses, or an API gateway would multiply deploy and
failure modes without a measured scaling need.

## Decision

Ship a single Python package (`wuwaterm`) as a modular monolith: clear module
responsibilities and dependency direction inside one deployable runtime, plus
optional builder jobs from the same codebase.

Layers (see `docs/architecture.md`):

- presentation: `bot`, `channel`, Telegram helpers
- domain: `lookup`, `normalize`, `models`, `sentence`, `translation_policy`
- local infrastructure: `settings`, `channel_reply_*`, `channel_runtime`, `db` reads
- builder: `builder`, `data_source`, `build_pinyin`
- bootstrap: `cli`, `constants`, `runtime_keys`

## Consequences

- Positive: one image, one process model, simple Compose topology.
- Positive: import-direction checks can fail closed without a service mesh.
- Negative: large presentation modules remain; boundary discipline is social +
  automated checks, not process isolation.
- Out of scope until real triggers exist: Web admin, multi-instance shared
  state, external queues (`docs/architecture.md` extension table).

## Evidence

- Package layout under `src/wuwaterm/`
- Import graph snapshot and `scripts/check_architecture_boundaries.py`
- `deploy/docker-compose.yml` single `wuwaterm` runtime service
- `tests/test_runtime_imports.py`, CI jobs `test` + `deploy-boundary`
