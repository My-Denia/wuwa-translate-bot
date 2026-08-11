"""What the client's own documentation promises, pinned.

Two claims went wrong the same way: the text described a property the code
does not have. A build script called itself reproducible while containing no
reproducibility mechanism, and the client's README described Cancel as
stopping the in-flight request when it stops only this process waiting for it.
Both are cheap to reintroduce and neither is caught by any behavioural test,
because there is no behaviour to catch - the defect is the sentence.

Every check here reads whitespace-normalised text. A phrase that spans a line
break in markdown must still be found, and - more importantly - a banned
sentence must not be able to slip back in simply by wrapping one word earlier.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that are not this repository's prose: build output, virtual
# environments, caches, and the run folders the agent harness writes.
SKIPPED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "goal-runs",
        "site-packages",
    }
)


def _flat(path: Path) -> str:
    """File text with every run of whitespace collapsed to one space."""
    return " ".join(path.read_text(encoding="utf-8").split())


def _prose_files() -> list[Path]:
    """Every prose or script file in this repository a build claim could
    live in - discovered, not listed.

    A hardcoded list turns a rename into silent loss of coverage and never
    sees a document added later, which is the wrong direction for a guard
    whose whole job is to notice text. `tests/` is excluded because this file
    has to name the banned spellings in order to ban them.
    """
    found: list[Path] = []
    for pattern in ("*.md", "*.ps1", "*.rst", "*.txt"):
        for path in ROOT.rglob(pattern):
            if SKIPPED_DIRECTORY_NAMES.intersection(path.parts):
                continue
            if "tests" in path.relative_to(ROOT).parts:
                continue
            found.append(path)
    return found


def test_no_document_claims_the_client_build_is_reproducible():
    """A reproducible build is a verified property, not a description of a
    scripted one.

    Nothing here normalises build timestamps, pins a hash seed, or compares
    two builds, so no file may use the word as a promise. Denials are fine and
    are what the corrected text uses, so the ban is on the claiming
    spellings.
    """
    scanned = _prose_files()
    # A discovery bug that finds nothing would pass every assertion below.
    assert len(scanned) > 10
    assert ROOT / "client" / "build.ps1" in scanned
    assert ROOT / "client" / "README.md" in scanned
    assert ROOT / "docs" / "adr" / "0011-pc-client-stack.md" in scanned
    assert ROOT / "docs" / "validation.md" in scanned

    for path in scanned:
        text = _flat(path).lower()
        for claim in (
            "reproducible build",
            "reproducible one-folder",
            "reproducible pyinstaller",
            "build is reproducible",
            "reproducibly",
        ):
            assert claim not in text, f"{path.relative_to(ROOT)}: {claim}"


def test_the_build_script_states_the_guarantee_it_does_have():
    """Removing a false claim leaves a reader with no claim at all, which is
    how the false one comes back. The script says what it actually provides
    and, explicitly, what it does not.

    "Pinned" would have been the next false claim: the client has no lock
    file, its dependencies are ranges, and both the interpreter patch release
    and the CI runner image float, so two builds need not even share inputs.
    The word is version-bounded, and the reasons are named.
    """
    text = _flat(ROOT / "client" / "build.ps1")

    assert "Version-bounded, self-checked one-folder PyInstaller build" in text
    assert "What it does NOT claim: bit-for-bit reproducibility" in text
    # Why the inputs are not fixed either.
    assert "The client has no lock file" in text
    # The absent mechanisms are named, so the next reader can tell whether
    # adding one would change the claim.
    for mechanism in (
        "build-timestamp normalisation",
        "hash-seed pinning",
        "two-build",
    ):
        assert mechanism in text, mechanism


def test_the_client_readme_says_what_cancel_does_not_do():
    """Cancel is client-side only once the request is on the wire: the service
    is never told, the work finishes and a model call already in flight is
    paid for. A user who reads "stops the in-flight request" believes the
    opposite of all three.

    The narrower case is part of the claim, not a footnote to it: a cancel
    caught before the task's first step really does send nothing
    (`TranslateView._on_translate_clicked` only schedules the coroutine;
    `client/tests/test_ui_smoke.py::test_cancelling_before_the_task_starts_restores_the_buttons`).
    Saying otherwise would be a second false claim in place of the first.
    """
    text = _flat(ROOT / "client" / "README.md")

    assert "Cancellation stops the waiting, not the work." in text
    assert "without un-spending the model budget" in text
    assert "before the request is dispatched, nothing has been sent" in text
    # The old wording, which promised the service was affected. Normalised, so
    # re-wrapping the sentence cannot bring it back past this check.
    assert "Cancel stops the in-flight request immediately" not in text
