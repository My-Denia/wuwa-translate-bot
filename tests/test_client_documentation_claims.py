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

import dataclasses
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Git object modes, as `git ls-files -s` reports them. The mode is what
# distinguishes a symlink from a regular file WITHOUT touching the working
# tree, which is the whole point of reading it here.
REGULAR_FILE_MODES = frozenset({"100644", "100755"})
SYMLINK_MODE = "120000"
GITLINK_MODE = "160000"

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
    """Working-tree text, whitespace collapsed to one space.

    Used only by the checks that pin NAMED sentences in specific documents,
    where reading the file a contributor is editing is the point. The
    repository-wide ban reads tracked blobs instead (`_tracked_text`), so its
    verdict cannot depend on anything outside the repository; that asymmetry
    is deliberate.
    """
    return " ".join(path.read_text(encoding="utf-8", errors="replace").split())


def _git(arguments: list[str], root: Path = ROOT) -> bytes:
    """Run git and return its raw stdout. Bytes, deliberately: paths and blob
    contents are both byte strings until something decides how to decode them.
    """
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    return completed.stdout


@dataclasses.dataclass(frozen=True)
class TrackedEntry:
    """One index entry: what git says is in this repository at this path."""

    mode: str
    object_id: str
    # RAW bytes, exactly as git reported them. Git permits any byte sequence
    # except NUL in a filename, and on Linux a legal non-UTF-8 name is not
    # rare enough to ignore: decoding it strictly here would raise
    # UnicodeDecodeError and abort the gate before it scanned anything at
    # all. The bytes are carried to the one place that needs a str.
    path: bytes

    @property
    def name(self) -> str:
        """A displayable path that no byte sequence can make raise.

        `surrogateescape` is explicit here rather than borrowed from
        `os.fsdecode`, and that is not a style choice: os.fsdecode uses the
        PLATFORM's filesystem error handler, which is `surrogateescape` on
        Linux but `surrogatepass` on Windows - and surrogatepass still raises
        on a byte that is simply invalid UTF-8. Measured on this machine,
        `os.fsdecode(b"docs/caf\\xe9.md")` raises UnicodeDecodeError, so
        routing through it would have left this gate abortable on Windows
        while looking fixed on Linux.

        Decoding explicitly, the same way on every platform, also keeps the
        gate's verdict from depending on which operating system ran it.
        """
        return self.path.decode("utf-8", "surrogateescape")


def _tracked_entries(root: Path = ROOT) -> list[TrackedEntry]:
    """Every stage-0 index entry, with its mode and object id.

    `-s` rather than a bare listing, because the two things this scan needs
    are exactly what it adds. The MODE tells a symlink from a regular file
    without consulting the working tree. The OBJECT ID lets the content be
    read out of the repository instead of off the disk.

    `-z` because a repository is allowed to contain a filename with a newline
    in it, and the line-oriented form would split it into two paths that
    exist nowhere.
    """
    entries: list[TrackedEntry] = []
    for record in _git(["ls-files", "-s", "-z"], root).split(b"\0"):
        if not record:
            continue
        metadata, _, path = record.partition(b"\t")
        mode, object_id, stage = metadata.split()
        if stage != b"0":
            # A conflicted index carries the same path at stages 1, 2 and 3.
            # This gate does not run meaningfully mid-merge, and taking every
            # stage would scan one path several times.
            continue
        entries.append(
            TrackedEntry(
                mode=mode.decode("ascii"),
                object_id=object_id.decode("ascii"),
                path=path,
            )
        )
    return entries


def _scanned_entries(root: Path = ROOT) -> list[TrackedEntry]:
    """The entries the repository-wide ban applies to.

    Gitlinks are excluded because a submodule's object id names a commit in
    ANOTHER repository and has no blob here to read.
    `test_the_repository_has_no_submodules_for_the_scan_to_skip` asserts
    there are none, so this exclusion cannot quietly hide anything today.
    """
    return [
        entry
        for entry in _tracked_entries(root)
        if entry.mode != GITLINK_MODE
        and not EXCLUDED_TOP_PARTS.intersection(Path(entry.name).parts)
    ]


def _tracked_text(entries: list[TrackedEntry], root: Path = ROOT) -> dict[bytes, str]:
    """Flattened text for each entry, read from the REPOSITORY not the disk.

    This is what stops the gate's verdict depending on things that are not in
    the repository. The scan used to build `ROOT / path` and read it, which
    FOLLOWED a tracked symlink to whatever it pointed at - possibly untracked,
    possibly outside the checkout entirely - and silently skipped a tracked
    symlink whose target did not exist, because the path was not `is_file()`.
    Both are the same defect the closed discovery was supposed to remove: a
    verdict decided by content this repository does not contain.

    Reading the blob answers the question the ban actually asks. A symlink
    (mode 120000) has a blob too - its content IS the target path as a string
    - so its stored target is scanned as text and the target is never
    followed. There is no such thing as a broken tracked symlink from here:
    the stored string is always readable.

    One `git cat-file --batch` for the whole scan rather than one process per
    file; the batch protocol answers one record per REQUESTED id, in order,
    so duplicate ids (two paths with identical content) are not a special
    case.
    """
    if not entries:
        return {}
    request = "".join(f"{entry.object_id}\n" for entry in entries).encode("ascii")
    completed = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=request,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")

    stream = completed.stdout
    texts: dict[bytes, str] = {}
    offset = 0
    for entry in entries:
        end_of_header = stream.index(b"\n", offset)
        header = stream[offset:end_of_header].split()
        assert len(header) == 3, f"unreadable object for {entry.name}: {header!r}"
        assert header[0].decode("ascii") == entry.object_id, (
            f"cat-file answered out of order at {entry.name}"
        )
        size = int(header[2])
        start = end_of_header + 1
        # Undecodable bytes are REPLACED rather than raised: a tracked binary
        # must not be able to stop the gate, and dropping it from the scan
        # instead is how coverage rots quietly.
        texts[entry.path] = " ".join(
            stream[start : start + size].decode("utf-8", errors="replace").split()
        )
        offset = start + size + 1  # the record's trailing newline
    return texts


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
    return [entry.name for entry in _scanned_entries()]


def _expected_scope_by_an_independent_route(root: Path = ROOT) -> set[str]:
    """The oracle for the scope test, sharing no code with the scanner.

    The equality used to derive BOTH sides from one helper. A defect in that
    helper - a mis-parsed record, a dropped path - removed the same entries
    from the system under test and from the thing it was compared against,
    and the assertion went on passing while the scan silently shrank. An
    oracle that shares a data source with what it checks is not an oracle.

    This asks git a different question in a different form - plain
    `ls-files -z`, paths only, no staged mode/object records - and parses the
    answer itself. Nothing below calls `_git`, `_tracked_entries` or
    `_scanned_entries`, and that separation is the point rather than an
    accident of style.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr.decode("utf-8", errors="replace")
    # The same decoding CONVENTION as TrackedEntry.name, spelled out again
    # rather than shared: the independence that matters is of the data
    # source, and both sides must agree on how a path is named or they could
    # not be compared at all.
    names = {path.decode("utf-8", "surrogateescape") for path in listed.stdout.split(b"\0") if path}
    return {
        name
        for name in names
        if not EXCLUDED_TOP_PARTS.intersection(Path(name).parts)
    }


def test_the_scope_oracle_notices_when_the_scanner_loses_a_path(monkeypatch):
    """The equality is only worth its green tick if a shrinking scan fails it.

    Both sides used to come from one helper, so a defect there removed the
    same paths from the scan AND from what the scan was compared against, and
    the assertion passed while coverage silently fell. Here the scanner is
    sabotaged - one real path dropped - and the oracle, which asks git
    separately, has to disagree.
    """
    module = sys.modules[__name__]
    honest = _tracked_entries

    def lossy(root: Path = ROOT) -> list[TrackedEntry]:
        return [entry for entry in honest(root) if entry.name != "README.md"]

    monkeypatch.setattr(module, "_tracked_entries", lossy)

    scanned = {entry.name for entry in _scanned_entries()}
    expected = _expected_scope_by_an_independent_route()

    assert scanned != expected, "a shared-source oracle would have passed here"
    assert "README.md" in expected - scanned


def test_a_path_that_is_not_valid_utf8_does_not_abort_the_gate():
    """Git permits any byte except NUL in a filename, and Linux really does
    carry such names.

    The scan used to decode paths strictly, so ONE such file anywhere in the
    repository would raise UnicodeDecodeError out of discovery - before a
    single document was read, and long before the content policy that was
    careful about undecodable bytes ever ran.

    Checked against injected bytes rather than a real file, because a name
    like this cannot be created on the Windows filesystem this also has to
    pass on.
    """
    entry = TrackedEntry(mode="100644", object_id="0" * 40, path=b"docs/caf\xe9.md")

    name = entry.name

    assert name.startswith("docs/caf")
    assert name.endswith(".md")
    # Lossless: surrogateescape round-trips back to the bytes git gave us.
    assert name.encode("utf-8", "surrogateescape") == b"docs/caf\xe9.md"
    # ...and it is still a path that the exclusion rule can reason about.
    assert Path(name).parts[0] == "docs"


def test_the_repository_has_no_submodules_for_the_scan_to_skip():
    """The scan reads blobs, and a gitlink has none here - its object id
    names a commit in another repository.

    There are no submodules, so the exclusion in `_scanned_entries` hides
    nothing. If one is ever added, this fails and the gate gets revisited
    deliberately instead of quietly declining to read it.
    """
    gitlinks = [entry.name for entry in _tracked_entries() if entry.mode == GITLINK_MODE]
    assert gitlinks == []


def _repository_with_a_tracked_symlink(tmp_path: Path, target: str) -> Path:
    """A real git repository whose index holds a mode-120000 entry.

    Built with `hash-object` plus `update-index --cacheinfo` rather than by
    creating a symlink on disk, for two reasons. Creating one on Windows
    needs a privilege CI does not necessarily have, and - more to the point -
    the entry under test is an INDEX entry. Writing it directly is both
    portable and a more honest fixture: it produces exactly the tracked state
    the scanner must handle, with nothing in the working tree to accidentally
    fall back on.
    """
    root = tmp_path / "repository"
    root.mkdir()
    for arguments in (
        ["init", "-q"],
        ["config", "user.email", "gate@example.invalid"],
        ["config", "user.name", "gate"],
    ):
        _git(arguments, root)

    # An ordinary tracked file, so the fixture is not all symlink.
    (root / "README.md").write_text("nothing to see here\n", encoding="utf-8")
    _git(["add", "README.md"], root)

    # The blob whose CONTENT is the link target - which is what a symlink is
    # in git - and an index entry pointing at it with the symlink mode.
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=root,
        input=target.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert blob.returncode == 0, blob.stderr.decode("utf-8", errors="replace")
    object_id = blob.stdout.decode("ascii").strip()
    _git(["update-index", "--add", "--cacheinfo", f"120000,{object_id},docs/link.md"], root)
    return root


def test_a_tracked_symlink_is_read_as_its_stored_target_not_followed(tmp_path):
    """The gate's verdict must not depend on what a link points AT.

    The scan used to build a path and read it, which followed a tracked
    symlink to a target that can be untracked, outside the checkout, or
    absent - and when it was absent the entry vanished from the scan
    entirely, because the path was not `is_file()`. Either way the answer
    came from something this repository does not contain, which is the exact
    dependence the closed discovery existed to remove.

    The target here is deliberately hostile on both counts: it points outside
    the repository AND does not exist. It must still be scanned, and what is
    scanned must be the stored target string.
    """
    target = "../outside/never-created.md"
    root = _repository_with_a_tracked_symlink(tmp_path, target)

    entries = _scanned_entries(root)
    by_name = {entry.name: entry for entry in entries}

    assert set(by_name) == {"README.md", "docs/link.md"}
    link = by_name["docs/link.md"]
    assert link.mode == SYMLINK_MODE

    texts = _tracked_text(entries, root)
    # The stored target, verbatim - not the target's content, which does not
    # exist, and not an omission.
    assert texts[link.path] == target
    assert texts[by_name["README.md"].path] == "nothing to see here"


def test_a_tracked_symlink_cannot_import_a_claim_from_outside_the_repository(tmp_path):
    """The consequence that matters, stated as behaviour.

    A link pointing at a file that DOES exist and DOES carry the banned claim
    must not put that claim in scope: the repository contains the link, not
    the file it names.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "claims.md").write_text(
        "this build is fully reproducible\n", encoding="utf-8"
    )
    root = _repository_with_a_tracked_symlink(tmp_path, "../outside/claims.md")

    texts = _tracked_text(_scanned_entries(root), root)
    scanned_text = " ".join(texts.values())

    assert STEM not in scanned_text
    assert "../outside/claims.md" in scanned_text


def test_the_scan_covers_every_tracked_file():
    """`docs/validation.md` calls this ban repository-wide. This is what
    makes that sentence true rather than aspirational.

    The scope is asserted as an EQUALITY against an INDEPENDENTLY obtained
    answer, not as a sample and not against the scanner's own helper. A
    subset check would pass an implementation that quietly went back to
    matching extensions; a shared-source equality would pass one that had
    lost paths from both sides at once.
    """
    scanned = {entry.name for entry in _scanned_entries()}
    expected = _expected_scope_by_an_independent_route()

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
    entries = _scanned_entries()
    names = {entry.name for entry in entries}
    # A discovery bug that finds nothing would pass every assertion below.
    assert len(entries) > 10
    for required in (
        "client/build.ps1",
        "client/README.md",
        "docs/adr/0011-pc-client-stack.md",
        "docs/validation.md",
        "docs/architecture.md",
        "README.md",
        # A job name or a step summary is prose too, and so is a script
        # header: the claim can live anywhere a reader would meet it.
        ".github/workflows/ci.yml",
        "deploy/vps-update.sh",
        "client/WuwaTerm.spec",
        "client/pyproject.toml",
    ):
        assert required in names, required

    # Content comes from the repository, not from the disk: see _tracked_text.
    texts = _tracked_text(entries)
    offenders: list[str] = []
    for entry in entries:
        text = texts[entry.path].lower()
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
                offenders.append(f"{entry.name}:{start}")
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
