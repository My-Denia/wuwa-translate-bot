# Release Checklist

Use this checklist before publishing a GitHub release. Do not attach generated
SQLite databases, TextMap files, or bulk game data to the release.

## Release Metadata

- Prospective release version: `<next-version>` (set `NEXT_VERSION` explicitly
  before any tag/release command; `v0.1.0` remains the historical 3.4 release
  recorded in `CHANGELOG.md`)
- Supported source profile: `arikatsu`
- Supported game data version:
  `GameVer 3.5.0 | ResVer 3.5.5 | Changelist 8059200`
- Pinned source repository:
  `https://github.com/Arikatsu/WutheringWaves_Data`
- Pinned source commit:
  `dae29691c04ef0f48d0810b5d244fb0b37288c60`
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
python scripts/verify_db.py data/terms.candidate.db --profile arikatsu
python scripts/verify_seed_terms.py data/terms.candidate.db
python scripts/verify_exact_hits.py data/terms.candidate.db --sample-size 500
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

## Final Publication Gate

Before publishing, fetch and fast-forward `main`, then record the exact reviewed
`origin/main` commit that passed CI and review. Use that SHA as the release
target.

For cleanup of squash-merged source branches, delete only if the branch is an
ancestor of `main` or `git cherry main <branch>` prints no `+` lines:

```bash
git merge-base --is-ancestor <branch> main
git cherry main <branch>
```

If the ancestry check fails and `git cherry` prints `+` lines, leave the branch
in place. Report the branch name and tip SHA, and record the restore commands
so the branch can be recreated if it is ever dropped:

```bash
git branch <branch> <tip-sha>
git push origin <tip-sha>:refs/heads/<branch>
```

When merging a release documentation PR, pass the expected PR head SHA to the
merge operation. If the wrong documentation change is merged, do not rewrite
`main`; open a revert PR for the squash merge commit.

Before creating the GitHub release, verify that the tag and release do not
already exist. The tag preflight command must fail if any matching ref already
exists, and it must also fail if the remote lookup itself errors. A
`git ls-remote --exit-code` status of 2 means no matching ref was found, which
is the expected pass state before publication. For the release preflight, a
non-zero `gh release view` result with "release not found" is the expected pass
state:

```bash
NEXT_VERSION=vX.Y.Z
tag_lookup_status=0
git ls-remote --exit-code --tags origin "refs/tags/$NEXT_VERSION" >/dev/null || tag_lookup_status=$?
case "$tag_lookup_status" in
  0)
    echo "refs/tags/$NEXT_VERSION already exists; stop"
    exit 1
    ;;
  2)
    echo "refs/tags/$NEXT_VERSION not found; ok"
    ;;
  *)
    echo "tag lookup failed; stop"
    exit 1
    ;;
esac

gh release view "$NEXT_VERSION" --json tagName,targetCommitish,url,assets
```

Create the release with no asset paths. After creation, verify that the release
has no assets and that `refs/tags/$NEXT_VERSION` points at the reviewed release
target.
For an annotated tag, compare the reviewed target against the peeled commit ref
`refs/tags/$NEXT_VERSION^{}`; otherwise compare against the tag ref itself:

```bash
NEXT_VERSION=vX.Y.Z
reviewed_main_commit=<reviewed-main-commit-sha>
gh release view "$NEXT_VERSION" --json tagName,targetCommitish,url,assets
tag_ref="$(git ls-remote --exit-code --tags origin "refs/tags/$NEXT_VERSION" "refs/tags/$NEXT_VERSION^{}")" || {
  echo "tag lookup failed or tag missing; stop"
  exit 1
}
tag_sha="$(
  printf '%s\n' "$tag_ref" | awk -v tag="refs/tags/$NEXT_VERSION" '
    $2 == tag "^{}" { print $1; found = 1; exit }
    $2 == tag { fallback = $1 }
    END {
      if (!found && fallback != "") print fallback
      else if (!found) exit 1
    }
  '
)" || {
  echo "tag ref parse failed; stop"
  exit 1
}
test "$tag_sha" = "$reviewed_main_commit"
```

If an incorrect release is published and repository policy allows removal,
delete the release and tag together after confirming it should no longer be
consumed:

```bash
NEXT_VERSION=vX.Y.Z
gh release delete "$NEXT_VERSION" --cleanup-tag
```

## Release Note Template

```markdown
## <next-version>

### Supported Game Data

- Source profile: arikatsu
- Source repository: https://github.com/Arikatsu/WutheringWaves_Data
- Pinned source commit: dae29691c04ef0f48d0810b5d244fb0b37288c60
- GameVer: 3.5.0
- ResVer: 3.5.5
- Changelist: 8059200

### Validation

- `python scripts/check_repo_hygiene.py`
- `python scripts/check_non_goals.py`
- `python -m pytest`
- `python scripts/verify_db.py data/terms.candidate.db --profile arikatsu`

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
allows publishing a release. First copy the release note template above into a
local `RELEASE_NOTES.md` file and review it; that file is a maintainer working
artifact, not a required committed file. Replace `<reviewed-main-commit-sha>`
with the exact `origin/main` commit that passed CI and review.

```bash
NEXT_VERSION=vX.Y.Z
gh release create "$NEXT_VERSION" --target <reviewed-main-commit-sha> --title "$NEXT_VERSION" --notes-file RELEASE_NOTES.md
```
