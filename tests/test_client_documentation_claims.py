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
        ".tox",
        ".nox",
        ".eggs",
        "htmlcov",
    }
)

# The only two files allowed to use the word at all, because both of them use
# it to DENY the property. Everything else is scanned for the stem, not for a
# list of spellings: "fully reproducible", "byte-for-byte reproducible" and
# "a reproducible artifact" are the same false claim as the one this gate was
# written for, and the next author's phrasing is not the last author's.
REPRODUCIBILITY_DENIALS = (
    Path("client") / "build.ps1",
    Path("docs") / "adr" / "0011-pc-client-stack.md",
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
    two builds, so no file may promise one. The check is on the STEM with a
    two-file allowlist rather than on a list of spellings: a list only ever
    catches the wording that was there, and "fully reproducible" or
    "byte-for-byte reproducible" would be the same false claim in the next
    author's words. The two allowed files are allowed because they deny it,
    which is asserted here too - an allowlist that does not check what it
    permits is just a hole.
    """
    scanned = _prose_files()
    # A discovery bug that finds nothing would pass every assertion below.
    assert len(scanned) > 10
    for required in (
        Path("client") / "build.ps1",
        Path("client") / "README.md",
        Path("docs") / "adr" / "0011-pc-client-stack.md",
        Path("docs") / "validation.md",
        Path("docs") / "architecture.md",
        Path("README.md"),
    ):
        assert ROOT / required in scanned, str(required)

    offenders = [
        str(path.relative_to(ROOT))
        for path in scanned
        if "reproducib" in _flat(path).lower()
        and path.relative_to(ROOT) not in REPRODUCIBILITY_DENIALS
    ]
    assert offenders == []

    # ...and the two files that may say the word say it to refuse the claim.
    assert (
        "what it does not claim: bit-for-bit reproducibility"
        in _flat(ROOT / "client" / "build.ps1").lower()
    )
    assert (
        "it is **not** reproducible and nothing in it attempts that"
        in _flat(ROOT / "docs" / "adr" / "0011-pc-client-stack.md").lower()
    )


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

    The narrower case is part of the claim, not a footnote to it. The client
    catches the cancellation around the whole `httpx` call - pool acquisition,
    connect, handshake and body write included (`ApiClient._request`) - so a
    cancel any time before the service has the whole body really does stop the
    work, and the boundary is that, not "before the task starts". Saying
    otherwise would be a second false claim in place of the first.
    """
    text = _flat(ROOT / "client" / "README.md")

    assert "Cancellation stops the waiting, not the work." in text
    assert "without un-spending the model budget" in text
    assert "the service does not yet have a whole request to act on" in text
    assert "Once it does have the whole request" in text
    # The old wording, which promised the service was affected. Normalised, so
    # re-wrapping the sentence cannot bring it back past this check.
    assert "Cancel stops the in-flight request immediately" not in text
