# Data Refresh

## Data Source

Primary source:

- `https://github.com/Arikatsu/WutheringWaves_Data`
- pinned commit: `58ec43698d2b4e188cb285467ce1ae887612dd92`
- pinned version: `GameVer 3.4.0 | ResVer 3.4.13`

Fallback mirrors to try manually if the primary source is unavailable:

- `https://github.com/Dimbreath/WutheringData` is frozen at 3.1.0
  (`e9234ffe094b2d944d16b222d31102e8ab32d954`, 2026-03-13) and is kept only
  as a legacy fallback.

The active Arikatsu source profile uses sparse checkout for only:

- `BinData`
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
uv pip install -e ".[dev]"
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

## Build Dictionary

```bash
.venv/bin/python -m wuwaterm.cli refresh-data --dest data/wutheringdata --profile arikatsu
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.db --profile arikatsu
.venv/bin/python scripts/verify_db.py data/terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location
```

## Lookup

```bash
.venv/bin/python -m wuwaterm.cli lookup --db data/terms.db 声骸
.venv/bin/python -m wuwaterm.cli sentence --db data/terms.db "今汐装备了声骸"
```

## Refresh Checks

For each real game-version refresh, pick at least one term that exists only in
the new game data and run a live `/tr <term>` check in Telegram after the DB
build. Counts and hashes prove rebuild mechanics; a new-term live check proves
the running bot is serving the refreshed content. Use
[Validation](validation.md) for the full offline validation command set.
