from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_GAME_TEXT_BYTES = 1_000_000
BULK_GAME_PATH_MARKERS = (
    "TextMap/",
    "Textmaps/",
    "ConfigDB/",
    "BinData/",
)
RUNTIME_STATE_NAMES = {
    "chat_settings.json",
    "chat_settings.json.lock",
    "channel_replies.json",
}


def _git_paths(args: list[str]) -> list[str]:
    proc = subprocess.run(
        args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
    return [p.decode("utf-8") for p in proc.stdout.split(b"\0") if p]


def tracked_paths() -> list[str]:
    """Repo-relative paths present in the INDEX - what a commit would carry."""
    return _git_paths(["git", "ls-files", "--cached", "-z"])


def untracked_paths() -> list[str]:
    return _git_paths(["git", "ls-files", "--others", "--exclude-standard", "-z"])


def candidate_files() -> list[Path]:
    return sorted({ROOT / rel for rel in tracked_paths() + untracked_paths()})


def is_game_text_path(rel: str) -> bool:
    return (
        any(marker in rel for marker in BULK_GAME_PATH_MARKERS)
        or rel.startswith("data/")
        or "wutheringdata" in rel.casefold()
    )


def is_runtime_state_path(rel: str) -> bool:
    name = Path(rel).name
    return (
        rel.startswith(("state/", "state-api/"))
        or name in RUNTIME_STATE_NAMES
        or name.startswith(".chat_settings.")
        or name.startswith(".channel_replies.")
    )


# SQLite writes this at offset 0 of every database file it creates.
SQLITE_MAGIC = b"SQLite format 3\x00"


def looks_like_a_database(path: Path) -> bool:
    """Identify a database by its CONTENT, not by what it is called.

    The suffix check is necessary and was not sufficient. A test that pointed a
    device store at a deliberately silly path wrote a real SQLite file into the
    repository root under a name with no extension at all; the suffix rule did
    not see it, nothing else did either, and it was committed. A database is a
    database whichever name it arrives under, and the first sixteen bytes say
    so.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def staged_blob_is_a_database(rel: str) -> bool:
    """The same question asked of the INDEX rather than the working tree.

    Reading the working tree was the wrong SOURCE for a tracked path, and the
    gap is reachable: stage a file and then delete or replace it, and the index
    still carries the original blob while the path on disk is gone. Reproduced
    as an `AD` state - index holding the SQLite magic, working tree empty - in
    which a worktree-only check reported the repository clean and the database
    blob was free to be committed. What a commit carries is the index, so that
    is what has to be inspected.

    Only the HEADER is read. ``git cat-file blob`` writes the whole object, and
    buffering it to look at sixteen bytes would mean loading an arbitrarily
    large file into memory - including exactly the bulk game data this guard
    exists to reject, so the check could be killed by the very thing it is
    meant to report. The pipe is closed after the header, which ends the
    writer.
    """
    proc = subprocess.Popen(
        ["git", "cat-file", "blob", f":{rel}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        header = proc.stdout.read(len(SQLITE_MAGIC)) if proc.stdout else b""
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.terminate()
        proc.wait()
    return header == SQLITE_MAGIC


def main() -> int:
    failures: list[str] = []
    tracked = set(tracked_paths())
    for path in candidate_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.endswith((".db", ".sqlite", ".sqlite3")):
            failures.append(f"tracked generated DB: {rel}")
            continue
        # Tracked paths are judged on the INDEX, because that is what a commit
        # would carry; untracked ones only exist on disk. Checking the working
        # tree for both let a staged database blob through whenever the path
        # had since been deleted or replaced.
        if rel in tracked:
            if staged_blob_is_a_database(rel):
                failures.append(
                    f"staged database blob (detected by content): {rel}"
                )
                continue
        elif (
            path.is_file()
            # is_file() FOLLOWS the link, so a symlink pointing at a database
            # outside the repository was reported as one - but git stores only
            # the target path string, so there is no database to commit and the
            # gate was blocked by a false positive.
            and not path.is_symlink()
            and looks_like_a_database(path)
        ):
            failures.append(f"untracked database file (detected by content): {rel}")
            continue
        if is_runtime_state_path(rel):
            failures.append(f"tracked runtime state: {rel}")
            continue
        if not path.exists():
            continue
        size = path.stat().st_size
        if is_game_text_path(rel) and size > MAX_GAME_TEXT_BYTES:
            failures.append(f"tracked bulk game-text file >1MB: {rel} ({size} bytes)")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("repo hygiene ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
