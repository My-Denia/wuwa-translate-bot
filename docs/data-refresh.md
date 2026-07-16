# Data Refresh

## Data Source

Primary source:

- `https://github.com/Arikatsu/WutheringWaves_Data`
- pinned commit: `dae29691c04ef0f48d0810b5d244fb0b37288c60`
- pinned version: `GameVer 3.5.0 | ResVer 3.5.5 | Changelist 8059200`

Fallback mirror to try manually if the primary source is unavailable:

- `https://github.com/Dimbreath/WutheringData` is frozen at 3.1.0
  (`e9234ffe094b2d944d16b222d31102e8ab32d954`, 2026-03-13) and is kept only
  as a legacy fallback. It has no machine-readable version file, so metadata
  records those version fields as `unavailable`; its remote, exact HEAD, and
  clean Git checkout are still measured and required. Arbitrary copied
  `ConfigDB` directories are rejected rather than stamped from constants.

The active Arikatsu source profile uses sparse checkout for only:

- `BinData`
- `README.md` (required checkout-version provenance)
- `Textmaps`

Bulk TextMap data and generated `terms.db` are local artifacts and are ignored
by Git. This project does **not** redistribute Wuthering Waves game data; only a
small derived term dictionary is built locally from the public source above. All
Wuthering Waves game data and in-game terminology are © Kuro Games.

## Local Development

WSL/Linux is the primary development environment. Keep the checkout on the WSL
filesystem (for example under `~/projects/...`) so file watching,
permissions, line endings, and virtualenv scripts behave like Linux.

```bash
test -x .venv/bin/python || uv venv .venv
uv sync --locked --extra dev
```

Use `uv venv --clear .venv` only when intentionally rebuilding the local
virtualenv from scratch.

If the WSL image has `python3-venv` and pip installed, the standard-library
path also works:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```

Docker data refresh uses the build image, not the production runtime image:

```bash
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm -e WUWATERM_DB_PATH=/app/data/terms.candidate.db wuwaterm-builder verify-db
```

## Build Dictionary

```bash
.venv/bin/python -m wuwaterm.cli refresh-data --dest data/wutheringdata --profile arikatsu
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.candidate.db --profile arikatsu --atomic
.venv/bin/python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
```

The refresh and build fail closed unless the checkout's `origin`, full HEAD,
clean tracked state, and all three version fields in the root README match the
active source profile. The builder writes those observed values into DB
metadata. The verifier opens the candidate read-only and checks integrity,
exact tables/columns/indexes, schema and source metadata, every required
category, and the 3.5 representative exact pair `穗穗 -> Suisui` in both
directions. It must pass before any production promotion. Generated candidates
remain ignored and are not distributed.

## Lookup

```bash
.venv/bin/python -m wuwaterm.cli lookup --db data/terms.db 声骸
.venv/bin/python -m wuwaterm.cli sentence --db data/terms.db "今汐装备了声骸"
```

## Refresh Checks

For 3.5, `穗穗 -> Suisui` is the required representative new-term check.
Offline candidate verification proves the source and DB content; it does not
prove that a VPS is running that DB. After an owner-authorized deployment, the
immutable deployment manifest records the DB hash and provenance. A live
`/tr` check is separate real-Telegram evidence and is never implied by offline
or deployment validation. Use [Validation](validation.md) for the full offline
validation command set.
