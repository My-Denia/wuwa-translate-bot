"""What the client's own documentation promises, pinned.

Two claims went wrong the same way: the text described a property the code
does not have. A build script called itself reproducible while containing no
reproducibility mechanism, and the client's README described Cancel as
stopping the in-flight request when it stops only this process waiting for it.
Both are cheap to reintroduce and neither is caught by any behavioural test,
because there is no behaviour to catch - the defect is the sentence.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every place a build claim could live: the script itself, the client's
# README, the ADR that records the packaging decision, and the validation
# table that says what each gate proves.
BUILD_CLAIM_FILES = (
    "client/build.ps1",
    "client/README.md",
    "docs/adr/0011-pc-client-stack.md",
    "docs/validation.md",
    "docs/architecture.md",
    "README.md",
    "CHANGELOG.md",
)


def test_no_document_claims_the_client_build_is_reproducible():
    """A reproducible build is a verified property, not a description of a
    scripted one.

    Nothing here normalises build timestamps, pins a hash seed, or compares
    two builds, so no file may use the word as a promise. The ADR is allowed
    to say the project does NOT have it - that is the denial, not the claim -
    so the check is on the claiming spellings.
    """
    for relative in BUILD_CLAIM_FILES:
        path = ROOT / relative
        if not path.exists():  # pragma: no cover - all of these are committed
            continue
        text = path.read_text(encoding="utf-8").lower()
        for claim in (
            "reproducible build",
            "reproducible one-folder",
            "reproducible pyinstaller",
            "reproducibly",
        ):
            assert claim not in text, f"{relative}: {claim}"


def test_the_build_script_states_the_guarantee_it_does_have():
    """Removing a false claim leaves a reader with no claim at all, which is
    how the false one comes back. The script says what it actually provides
    and, explicitly, what it does not.

    "Pinned" would have been the next false claim: the client has no lock
    file, its dependencies are ranges, and both the interpreter patch release
    and the CI runner image float, so two builds need not even share inputs.
    The word is version-bounded, and the reasons are named.
    """
    text = (ROOT / "client" / "build.ps1").read_text(encoding="utf-8")

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
    text = (ROOT / "client" / "README.md").read_text(encoding="utf-8")

    assert "Cancellation stops the waiting, not the work." in text
    assert "without un-spending the model budget" in text
    assert "before the request is\n  dispatched, nothing has been sent" in text
    # The old wording, which promised the service was affected.
    assert "Cancel stops the in-flight request\n  immediately" not in text
