# Release Checklist

Use this checklist before publishing a GitHub release. Do not attach generated
SQLite databases, TextMap files, or bulk game data to the release.

The only authorized release assets are the audited source-only Python package
artifacts built from the exact release commit: one wheel, one sdist, and a
`SHA256SUMS` file covering both. Every asset must pass
`scripts/check_package_artifacts.py`, `twine check --strict`, and a
clean-environment install/import/CLI smoke before upload. Nothing else may be
attached.

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

Packaging validation additionally needs `build` and `twine`, which are not in
the `dev` extra; installing them requires package-index access:

```bash
python -m pip install --upgrade build twine  # or: uv pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_package_artifacts.py dist/*.whl dist/*.tar.gz
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

Create the release with only the authorized package assets (wheel, sdist,
`SHA256SUMS`), all built from the exact reviewed release commit and audited
before upload. After creation, verify that the release carries exactly those
assets — re-download them and check `sha256sum -c SHA256SUMS` — and that
`refs/tags/$NEXT_VERSION` points at the reviewed release target.
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

### Assets

This release attaches the audited source-only Python package artifacts built
from the exact release commit: one wheel, one sdist, and `SHA256SUMS`. Verify
downloads with `sha256sum -c SHA256SUMS`.

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
release-notes file OUTSIDE the checkout (for example
`"$(mktemp -d)/RELEASE_NOTES.md"`) and review it; it is a maintainer working
artifact, not a committed file, and keeping it outside the checkout keeps the
clean-tree gate below meaningful. Replace `<reviewed-main-commit-sha>` with the
exact `origin/main` commit that passed CI and review.

Build and audit the release assets from a clean checkout of the exact reviewed
release commit, then create the release and upload only those assets:

```bash
NEXT_VERSION=vX.Y.Z
reviewed_main_commit=<reviewed-main-commit-sha>
notes_file=<path-to-release-notes-outside-the-checkout>

# Every gate below must abort publication on failure.
set -euo pipefail

# Build from the exact reviewed commit, not whatever the checkout drifted to.
test "$(git rev-parse HEAD)" = "$reviewed_main_commit"
test -z "$(git status --porcelain --untracked-files=all)"

# The tag must match the declared package version so a 0.2.0 wheel cannot be
# published under a different release tag.
declared_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
test "v$declared_version" = "$NEXT_VERSION"

python -m pip install --upgrade build twine  # not in the dev extra; or: uv pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_package_artifacts.py dist/*.whl dist/*.tar.gz
(cd dist && sha256sum *.whl *.tar.gz > SHA256SUMS)

# Smoke the exact artifacts being uploaded, not CI's separately built copies:
# clean-venv install + import + CLI for both the wheel and the sdist.
smoke_dir="$(mktemp -d)"
python -m venv "$smoke_dir/wheel-venv"
"$smoke_dir/wheel-venv/bin/pip" install --no-cache-dir dist/*.whl
"$smoke_dir/wheel-venv/bin/python" -c "import wuwaterm"
"$smoke_dir/wheel-venv/bin/wuwaterm" --help
python -m venv "$smoke_dir/sdist-venv"
"$smoke_dir/sdist-venv/bin/pip" install --no-cache-dir dist/*.tar.gz
"$smoke_dir/sdist-venv/bin/python" -c "import wuwaterm"
"$smoke_dir/sdist-venv/bin/wuwaterm" --help

# Create as a DRAFT first so a failed or partial asset upload never leaves a
# published release with missing assets; set -e cannot roll back a remote
# mutation. Verify the draft's assets, then publish.
gh release create "$NEXT_VERSION" --draft --target "$reviewed_main_commit" --title "$NEXT_VERSION" --notes-file "$notes_file" dist/*.whl dist/*.tar.gz dist/SHA256SUMS

# Run every integrity gate while the release is still a draft: asset count,
# re-downloaded checksums, and a repeat tag-absence check. GitHub ignores
# target_commitish when the tag already exists, so a tag created by another
# actor between preflight and publish would silently retarget the release.
test "$(gh release view "$NEXT_VERSION" --json assets --jq '.assets | length')" -eq 3
verify_dir="$(mktemp -d)"
gh release download "$NEXT_VERSION" --dir "$verify_dir"
(cd "$verify_dir" && sha256sum -c SHA256SUMS)
tag_lookup_status=0
git ls-remote --exit-code --tags origin "refs/tags/$NEXT_VERSION" >/dev/null || tag_lookup_status=$?
test "$tag_lookup_status" -eq 2  # the tag must still be absent right before publish

gh release edit "$NEXT_VERSION" --draft=false
```

After publishing, verify that `refs/tags/$NEXT_VERSION` resolves to
`$reviewed_main_commit` (see the readback block above), and re-download the
published assets against `SHA256SUMS` before reporting the release as
published. If a draft is left behind by a failed upload, delete the draft
(drafts have no tag yet) and rerun the block.
