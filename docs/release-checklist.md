# Release Checklist

Use this checklist before publishing a GitHub release. Do not attach generated
SQLite databases, TextMap files, or bulk game data to the release.

## Release Metadata

- Release version: `v0.1.0`
- Supported source profile: `arikatsu`
- Supported game data version: `GameVer 3.4.0 | ResVer 3.4.13`
- Pinned source repository:
  `https://github.com/Arikatsu/WutheringWaves_Data`
- Pinned source commit:
  `58ec43698d2b4e188cb285467ce1ae887612dd92`
- Fallback source profile: `dimbreath_legacy`
- Fallback pinned commit:
  `e9234ffe094b2d944d16b222d31102e8ab32d954`

## Validation

Run the full offline validation set from a clean checkout:

```bash
python scripts/check_repo_hygiene.py
python scripts/check_non_goals.py
python -m pytest
```

For a locally built release candidate database, also run:

```bash
python scripts/verify_db.py data/terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location
python scripts/verify_seed_terms.py data/terms.db
python scripts/verify_exact_hits.py data/terms.db --sample-size 500
python scripts/verify_idempotent_build.py --data-dir data/wutheringdata --out-dir goal-runs/wuwaterm-v2-translator --profile arikatsu
```

Release only after GitHub CI and configured review workflows are green for the
exact release-prep commit.

## Privacy And LLM Notes

- Exact database hits are dictionary-first and do not call the LLM.
- Free-text sentence translation can call an OpenAI-compatible endpoint only
  when the maintainer configures one.
- Telegram bot tokens, OpenAI-compatible API keys, owner IDs, chat IDs, `.env`
  files, runtime settings, and deployment logs must not be included in release
  notes, release assets, commits, or screenshots.
- Operational logs must keep chat, user, and message identifiers redacted.

## Distribution Boundary

- Do not publish generated `terms.db` files as release assets.
- Do not publish generated TextMap data or bulk Wuthering Waves game data.
- Do not copy upstream game data into this repository.
- State that the MIT license covers this project's source code only, not
  Wuthering Waves game data or in-game terminology.

## Release Note Template

```markdown
## v0.1.0

### Supported Game Data

- Source profile: arikatsu
- Source repository: https://github.com/Arikatsu/WutheringWaves_Data
- Pinned source commit: 58ec43698d2b4e188cb285467ce1ae887612dd92
- GameVer: 3.4.0
- ResVer: 3.4.13

### Validation

- `python scripts/check_repo_hygiene.py`
- `python scripts/check_non_goals.py`
- `python -m pytest`
- `python scripts/verify_db.py data/terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location`

### Privacy And LLM

Exact database hits do not call the LLM. Free-text sentence translation can call
an OpenAI-compatible endpoint only when the operator configures one. Do not
include tokens, API keys, chat IDs, owner IDs, `.env` files, runtime settings, or
deployment logs in release materials.

### Distribution Boundary

This release distributes source code only. It does not distribute generated
SQLite databases, generated TextMap files, or Wuthering Waves game data.

### Known Limitations

- Self-hosted bot; no public hosted service is provided.
- Live Telegram operation requires maintainer-provided credentials and chat
  configuration.
- Free-text sentence translation requires an external OpenAI-compatible
  endpoint.
```

## GitHub Release Command

Do not run this command until CI and review are green and repository policy
allows publishing a release:

```bash
gh release create v0.1.0 --title "v0.1.0" --notes-file RELEASE_NOTES.md
```
