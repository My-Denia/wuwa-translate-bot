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

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `tests/` is the one exclusion, and it is load-bearing: this file has to name
# the banned spellings in order to ban them. There is deliberately no list of
# skipped DIRECTORIES any more - discovery asks git what the repository
# contains, and build output, virtual environments, caches and the harness run
# folders are not tracked, so they are already out of scope by construction
# rather than by a list somebody has to keep in step.
EXCLUDED_TOP_PARTS = frozenset({"tests"})

# The stem is banned everywhere. What is exempted is not a FILE but these
# exact denial sentences, wherever they appear: exempting whole files would
# let the two documents most likely to carry the claim carry it again, beside
# an untouched denial, and still pass. Everything else is scanned for the stem
# rather than for a list of spellings, because "fully reproducible" or
# "byte-for-byte reproducible" is the same false claim in the next author's
# words.
REPRODUCIBILITY_DENIALS = (
    "what it does not claim: bit-for-bit reproducibility",
    "it is **not** reproducible and nothing in it attempts that",
)
STEM = "reproducib"


def _flat(path: Path) -> str:
    """File text with every run of whitespace collapsed to one space.

    Undecodable bytes are REPLACED rather than raised. Discovery is closed
    over everything the repository tracks, which may one day include a
    binary, and a gate that dies on the first one is a gate nobody can run.
    Replacement keeps such a file in scope; skipping it would be how coverage
    rots quietly.
    """
    return " ".join(path.read_text(encoding="utf-8", errors="replace").split())


def _tracked_files() -> list[str]:
    """Every path this repository tracks, asked of git.

    `-z` because a repository is allowed to contain a filename with a newline
    in it, and the line-oriented form would split it into two paths that
    exist nowhere.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr.decode("utf-8", errors="replace")
    return [entry.decode("utf-8") for entry in listed.stdout.split(b"\0") if entry]


def _prose_files() -> list[Path]:
    """Every file this repository TRACKS, outside `tests/`.

    Discovery used to be eleven filename globs over the working tree, and it
    was wrong in BOTH directions.

    It missed tracked files whose names carry no extension or an unlisted
    one - `deploy/Dockerfile`, `.env.example`, `deploy/env.example`,
    `docs/api/openapi.json`, `uv.lock`, `LICENSE`, `MANIFEST.in`,
    `.dockerignore`, `.gitattributes` and the two `.gitignore` files: eleven
    places the banned claim could sit untouched while `docs/validation.md`
    called the ban repository-wide.

    And it included whatever else happened to be lying in the working tree.
    A virtual environment named anything but `.venv` - `.venv-server`, `env`
    - or the `*.egg-info` an editable install writes, put third-party bytes
    in scope and made the gate's verdict depend on the state of somebody's
    checkout rather than on the repository.

    `git ls-files` answers exactly the question the claim is about: what is
    in this repository. It is CLOSED - a file added tomorrow, under any name,
    is in scope the moment it is tracked, with nothing to add to a list - and
    `test_the_scan_covers_every_tracked_file` pins that it stays closed.

    `tests/` remains excluded because this file has to name the banned
    spellings in order to ban them.
    """
    found: list[Path] = []
    for relative in _tracked_files():
        parts = Path(relative).parts
        if EXCLUDED_TOP_PARTS.intersection(parts):
            continue
        path = ROOT / relative
        # An index entry whose working file is gone - a deletion staged but
        # not committed - has no text to read.
        if not path.is_file():
            continue
        found.append(path)
    return found


def test_the_scan_covers_every_tracked_file():
    """`docs/validation.md` calls this ban repository-wide. This is what
    makes that sentence true rather than aspirational.

    The scope is asserted as an EQUALITY against what git reports, not as a
    sample: a subset check would pass an implementation that quietly went
    back to matching extensions, which is precisely the drift being closed.
    The named files below are the ones the old glob really missed - kept
    explicit so a reader can see what "repository-wide" bought.
    """
    scanned = {path.relative_to(ROOT).as_posix() for path in _prose_files()}
    expected = {
        relative
        for relative in _tracked_files()
        if not EXCLUDED_TOP_PARTS.intersection(Path(relative).parts)
        and (ROOT / relative).is_file()
    }

    assert scanned == expected
    # Extensionless, or an extension no list would have thought of.
    for missed_by_the_old_glob in (
        "deploy/Dockerfile",
        ".env.example",
        "deploy/env.example",
        "docs/api/openapi.json",
        "uv.lock",
        "LICENSE",
        "MANIFEST.in",
        ".dockerignore",
        ".gitattributes",
        ".gitignore",
        "client/.gitignore",
    ):
        assert missed_by_the_old_glob in scanned, missed_by_the_old_glob
    # ...and nothing that is merely PRESENT rather than tracked. A developer's
    # virtual environment or an editable install's metadata is not this
    # repository's prose, and used to be scanned as though it were.
    assert not [
        relative
        for relative in scanned
        if ".egg-info" in relative or relative.startswith((".venv", "venv"))
    ]


def test_no_document_claims_the_client_build_is_reproducible():
    """A reproducible build is a verified property, not a description of a
    scripted one.

    Nothing here normalises build timestamps, pins a hash seed, or compares
    two builds, so no file may promise one. Every occurrence of the stem must
    fall inside one of the exact denial sentences - a file-level exemption
    would let the synopsis go back to claiming the property while the denial
    paragraph below it kept the file exempt, which is precisely the defect
    this gate exists for, reintroduced past its own guard.
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
        # A job name or a step summary is prose too, and so is a script
        # header: the claim can live anywhere a reader would meet it.
        Path(".github") / "workflows" / "ci.yml",
        Path("deploy") / "vps-update.sh",
        Path("client") / "WuwaTerm.spec",
        Path("client") / "pyproject.toml",
    ):
        assert ROOT / required in scanned, str(required)

    offenders: list[str] = []
    for path in scanned:
        text = _flat(path).lower()
        # Character spans the denial sentences occupy, so an occurrence is
        # judged by WHERE it is, not by which file it is in.
        allowed: list[range] = []
        for denial in REPRODUCIBILITY_DENIALS:
            start = text.find(denial)
            while start != -1:
                allowed.append(range(start, start + len(denial)))
                start = text.find(denial, start + 1)
        start = text.find(STEM)
        while start != -1:
            if not any(start in span for span in allowed):
                offenders.append(f"{path.relative_to(ROOT)}:{start}")
            start = text.find(STEM, start + 1)
    assert offenders == []

    # ...and the denials are actually present, so "no occurrences" cannot be
    # reached by deleting the statement that the property is not claimed.
    build = _flat(ROOT / "client" / "build.ps1").lower()
    adr = _flat(ROOT / "docs" / "adr" / "0011-pc-client-stack.md").lower()
    assert REPRODUCIBILITY_DENIALS[0] in build
    assert REPRODUCIBILITY_DENIALS[1] in adr


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
    assert "without un-spending anything the request had already committed" in text
    assert "the service does not yet have a whole request to act on" in text
    assert "Once it does have the whole request" in text
    # The model cost is real but not universal: the dictionary stage returns
    # before the model, so a claim that every cancelled request costs money
    # would be the next overstatement.
    assert "a dictionary hit never reaches the model and costs nothing" in text
    # The old wording, which promised the service was affected. Normalised, so
    # re-wrapping the sentence cannot bring it back past this check.
    assert "Cancel stops the in-flight request immediately" not in text
