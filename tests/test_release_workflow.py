"""What the release workflow promises, and what the checklist says it does.

`docs/release-checklist.md` describes a release the maintainer never assembles
by hand any more: `.github/workflows/release.yml` builds every asset, writes the
manifest and composes the release notes. Two documents describing one mechanism
is exactly the shape that drifts - the checklist is read at release time, the
workflow runs at release time, and nothing else compares them.

So the properties that would be expensive to discover at release time are
pinned here:

* the note template in the checklist has the SAME section headings, in the same
  order, as the notes the workflow actually writes. The prose under them is
  deliberately NOT pinned: making every wording change a two-file edit buys
  nothing, and the reason to read the generated notes before publishing does
  not go away.
* publishing stays a human step - no tag trigger, no `--draft=false` anywhere
  in the workflow.
* the write permissions stay where the design put them.
* the client zip is named the same thing by the build script, the workflow, the
  client's own guide and the support matrix. That name is a cross-file contract
  with four holders and no other reader.

Everything here is a TEXT check on purpose. The workflow is YAML, but PyYAML is
not a dependency of this project and adding one so a test can read a file it
could read as text would be the wrong trade. The greps below are anchored
tightly enough to say what they mean.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CHECKLIST = ROOT / "docs" / "release-checklist.md"

# The list entries of the notes generator: `              "### Assets",`.
# Anchored to the trailing comma so the step-summary headings the workflow also
# writes (`echo "### Images"`) cannot be mistaken for release-note sections.
NOTES_SECTION = re.compile(r'^\s*"(### .+)",$', re.MULTILINE)

# The version part differs legitimately per file - a literal, an English
# placeholder, a Chinese one, a shell variable - so it is matched loosely and
# normalised away below. The prefix, the platform and the extension are the
# contract.
ZIP_NAME = re.compile(r"WuwaTerm-[^\n`'\"]*?-windows-x64\.zip")


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Lines that DO something, with comment-only lines dropped.

    A permission named in a comment explaining where permissions live must not
    be counted as a permission being granted; the first version of this file
    counted both and failed on its own explanatory comment.
    """
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _trigger_keys(text: str) -> list[str]:
    """The top-level keys of the workflow's `on:` block."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == "on:")
    keys = []
    for line in lines[start + 1 :]:
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):  # back to column 0: the block ended
            break
        match = re.match(r"^  ([A-Za-z_]+):", line)
        if match:
            keys.append(match.group(1))
    return keys


def _template_block() -> str:
    """The fenced release-note template out of the checklist."""
    text = CHECKLIST.read_text(encoding="utf-8")
    blocks = re.findall(r"^```markdown\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1, (
        f"expected exactly one fenced markdown template in {CHECKLIST.name}, "
        f"found {len(blocks)}"
    )
    return blocks[0]


def test_release_workflow_exists_and_has_no_tag_trigger():
    # Dispatch builds the draft; pull_request is always a dry run; release
    # published only retags recorded GHCR digests. A push or tag trigger
    # would still make publication a side effect of moving a ref.
    assert _trigger_keys(_workflow_text()) == [
        "workflow_dispatch",
        "pull_request",
        "release",
    ]
    assert re.search(
        r"^  release:\n    types:\n      - published\s*$",
        _workflow_text(),
        re.MULTILINE,
    )


def test_release_workflow_never_publishes():
    text = _workflow_text()
    executable = [
        line
        for line in text.splitlines()
        if "--draft=false" in line and not line.lstrip().startswith("#")
    ]
    assert executable == [], (
        "the workflow must never publish a release; publication is "
        f"`gh release edit --draft=false`, run by a person: {executable}"
    )


def test_release_workflow_keeps_write_permissions_where_the_design_put_them():
    text = _workflow_text()

    assert re.search(r"^permissions:\n  contents: read\n", text, re.MULTILINE), (
        "the workflow default must be read-only"
    )
    # Two package writes: draft-time sha push, and post-publish digest retag.
    # One draft job may write releases. More than that is a permission spread.
    code = _code_lines(text)
    assert sum("packages: write" in line for line in code) == 2
    assert sum("contents: write" in line for line in code) == 1


def test_no_write_scope_is_reachable_from_a_pull_request():
    """The workflow is inside its own pull_request path filter.

    So a branch pull request can rewrite this file and have it run. Any job
    that holds a write scope on that run holds it for whatever the pull request
    turned the job into - the dry-run switch only guards the steps that are
    checked in. Both widened jobs therefore carry a condition that a
    pull_request run cannot satisfy, and this test reads the condition rather
    than trusting the comment above it.
    """
    text = _workflow_text()
    lines = text.splitlines()
    widened: dict[str, str] = {}
    current = None
    for index, line in enumerate(lines):
        if re.match(r"^  [a-z0-9-]+:$", line):
            current = line.strip().rstrip(":")
        if line.lstrip().startswith("#"):
            continue
        if "packages: write" in line or "contents: write" in line:
            # `permissions:` is nested under the job, so the scope belongs to
            # whichever job header was seen last.
            assert current is not None, f"a write scope outside any job at line {index + 1}"
            widened[current] = line.strip()

    assert set(widened) == {"push-images", "draft-release", "promote-images"}, widened

    for job in widened:
        start = lines.index(f"  {job}:")
        block = []
        for line in lines[start + 1 :]:
            if re.match(r"^  [a-z0-9-]+:$", line):
                break
            block.append(line)
        condition = [line for line in block if line.strip().startswith("if:")]
        assert condition, f"{job} holds a write scope with no condition at all"
        text_of = " ".join(condition)
        if job == "promote-images":
            assert "release" in text_of, f"{job}: {text_of}"
            assert "published" in text_of, f"{job}: {text_of}"
            assert "pull_request" not in text_of, f"{job}: {text_of}"
            continue
        assert "workflow_dispatch" in text_of, f"{job}: {text_of}"
        assert "dry_run" in text_of and "'false'" in text_of, f"{job}: {text_of}"


def test_note_template_headings_match_the_notes_the_workflow_writes():
    generated = NOTES_SECTION.findall(_workflow_text())
    documented = [
        line.rstrip()
        for line in _template_block().splitlines()
        if line.startswith("### ")
    ]

    assert generated, "found no release-note sections in the workflow"
    assert generated == documented, (
        "docs/release-checklist.md's note template and the notes "
        ".github/workflows/release.yml writes describe different releases:\n"
        f"  workflow:  {generated}\n"
        f"  checklist: {documented}"
    )


def _job_block(name: str) -> str:
    lines = _workflow_text().splitlines()
    start = lines.index(f"  {name}:")
    block = []
    for line in lines[start + 1 :]:
        if re.match(r"^  [a-z0-9-]+:$", line):
            break
        block.append(line)
    return "\n".join(block)


def test_build_jobs_skip_the_release_event():
    for job in ("preflight", "python-package", "client", "images", "assemble"):
        block = _job_block(job)
        condition = [line for line in block.splitlines() if line.strip().startswith("if:")]
        assert condition, f"{job} has no if: and would rebuild on release published"
        text_of = " ".join(condition)
        assert "github.event_name != 'release'" in text_of, f"{job}: {text_of}"


def test_draft_image_tags_are_sha_class_only():
    assignments = re.findall(r"tag_values=\(([^)]+)\)", _workflow_text())
    assert assignments, "no tag_values assignments found"
    for assigned in assignments:
        assert "v$VERSION" not in assigned, assigned
        assert "major_minor" not in assigned, assigned
        assert "sha-$short" in assigned, assigned


def test_promote_images_retags_published_manifest_digests():
    block = _job_block("promote-images")
    assert "imagetools" in block
    assert "release-manifest.json" in block
    assert "deploy/Dockerfile" not in block
    assert "docker buildx build" not in block
    executable = [
        line
        for line in block.splitlines()
        if "--draft=false" in line and not line.lstrip().startswith("#")
    ]
    assert executable == []


def test_the_note_template_states_the_unsigned_client():
    template = _template_block().lower()
    assert "unsigned" in template
    assert "smartscreen" in template


def test_the_client_zip_has_one_name_across_every_file_that_names_it():
    holders = {
        ".github/workflows/release.yml": WORKFLOW,
        "client/build.ps1": ROOT / "client" / "build.ps1",
        "client/README.md": ROOT / "client" / "README.md",
        "docs/support-matrix.md": ROOT / "docs" / "support-matrix.md",
        "docs/release-checklist.md": CHECKLIST,
        "README.md": ROOT / "README.md",
        "README.zh-CN.md": ROOT / "README.zh-CN.md",
    }
    shapes: dict[str, set[str]] = {}
    for label, path in holders.items():
        assert path.is_file(), f"{label} is missing"
        found = ZIP_NAME.findall(path.read_text(encoding="utf-8"))
        assert found, f"{label} no longer names the client zip"
        # Normalise the version, which each file legitimately writes its own
        # way (a literal, a placeholder, a shell variable). What must agree is
        # the prefix, the platform suffix and the extension.
        shapes[label] = {re.sub(r"WuwaTerm-.*?-windows", "WuwaTerm-<v>-windows", n) for n in found}

    distinct = set().union(*shapes.values())
    assert distinct == {"WuwaTerm-<v>-windows-x64.zip"}, (
        f"the client zip is named more than one way: {shapes}"
    )
