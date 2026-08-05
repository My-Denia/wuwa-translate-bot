# ADR 0008: Candidate verification and transactional deployment

- Status: Accepted
- Date: 2026-08-05

## Context

Replacing a live glossary or runtime image in place risks serving a bad DB,
leaking partial upgrades, or losing the previous known-good pair of
`(source commit, image, terms.db)`. Manual `mv` of candidates over production
has caused operational risk historically and is explicitly disallowed in docs.

## Decision

Deploy through `deploy/vps-update.sh` (owner-gated) with a candidate pipeline:

1. Require clean Git checkout and `HEAD ==` freshly fetched `origin/main`.
2. Build/verify a unique candidate DB under `data/candidates/` (not live path).
3. Build an immutable `wuwaterm-runtime:<source-commit>` image; verify revision
   label.
4. Snapshot old DB and pointer; tag old image for rollback.
5. Stop runtime → promote DB → start exact image → smoke
   (`scripts/deploy_smoke.py`, diagnostic send disabled in updater path).
6. Write immutable `.deployments/<source-commit>.json` and atomic
   `.deploy_commit`.
7. On any post-promote failure, restore previous DB, image, and pointer.

Re-running the same source commit is accepted only when image/DB binding is
byte-identical; different rebuilds need a new source commit rather than
rewriting history manifests.

## Consequences

- Positive: provenance is auditable; rollback path is scripted.
- Positive: strong verifiers run before traffic sees the candidate.
- Negative: deploy is intentionally serial and owner-attended; not a CD
  free-for-all.
- Constraint: architecture docs describe this from repo scripts — production
  mutation remains an owner-authorized ops action, not an agent default.

## Evidence

- `deploy/vps-update.sh` candidate paths, `rollback_on_failure`, manifests
- `scripts/deployment_manifest.py`, `scripts/deploy_smoke.py`
- `docs/deployment.md`, `docs/validation.md`, `docs/release-checklist.md`
- `tests/test_deploy_scripts.py` (script contracts where covered)
