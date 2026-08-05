# ADR 0004: SQLite terminology data

- Status: Accepted
- Date: 2026-08-05

## Context

Official Chinese/English term pairs are derived from public game-data checkouts
and must be served with byte-for-byte fidelity on exact hits. Volume is modest
(a derived dictionary, not full TextMaps). Operators need offline build,
hashable candidates, and read-only production mounts.

## Decision

Store the serving glossary in a local SQLite file (`data/terms.db`) with a fixed
schema (`db.SCHEMA`, `SCHEMA_VERSION`). Runtime opens it for reads via
`TermService` / `connect`. Bulk TextMaps stay out of Git and out of the runtime
write path.

## Consequences

- Positive: exact-hit path needs no network; privacy-friendly dictionary-first
  behavior (`docs/privacy-and-llm.md`).
- Positive: candidates are ordinary files — verifiable, backupable, promotable
  transactionally ([ADR 0008](0008-candidate-verification-and-transactional-deployment.md)).
- Negative: not a multi-writer networked database; horizontal read replicas are
  out of scope.
- Constraint: production Compose mounts `data/` read-only for the runtime
  service.

## Evidence

- `src/wuwaterm/db.py` schema and `connect` / `create_database`
- `src/wuwaterm/lookup.py` `TermService`
- `deploy/docker-compose.yml` `../data:/app/data:ro`
- `docs/data-refresh.md`, `scripts/verify_db.py`
