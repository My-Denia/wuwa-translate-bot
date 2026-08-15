from __future__ import annotations

from scripts.check_repo_hygiene import is_game_text_path, is_runtime_state_path


def test_repo_hygiene_detects_current_and_legacy_bulk_game_paths():
    assert is_game_text_path("Textmaps/en/multi_text/MultiText.json")
    assert is_game_text_path("BinData/role/roleinfo.json")
    assert is_game_text_path("TextMap/en/MultiText.json")
    assert is_game_text_path("ConfigDB/RoleInfo.json")
    assert is_game_text_path("data/wutheringdata/Textmaps/en/file.json")
    assert is_game_text_path("vendor/WutheringData/Textmaps/en/file.json")


def test_repo_hygiene_ignores_normal_source_files():
    assert not is_game_text_path("src/wuwaterm/builder.py")
    assert not is_game_text_path("README.md")


def test_repo_hygiene_detects_runtime_state_paths():
    assert is_runtime_state_path("state/chat_settings.json")
    assert is_runtime_state_path("state/chat_settings.json.lock")
    assert is_runtime_state_path("state/channel_replies.json")
    assert is_runtime_state_path("chat_settings.json")
    assert is_runtime_state_path(".chat_settings.abc")
    assert not is_runtime_state_path("src/wuwaterm/settings.py")


def test_repo_hygiene_detects_a_database_by_content_not_by_name(tmp_path):
    """A database is a database whichever name it arrives under.

    The suffix rule was necessary and not sufficient: a test that pointed a
    device store at a deliberately invalid path wrote a real SQLite file into
    the repository root under a name with no extension, nothing recognised it,
    and it was committed and merged. Detection is now by the sixteen bytes
    SQLite writes at offset zero, so the name is irrelevant.
    """
    from scripts.check_repo_hygiene import SQLITE_MAGIC, looks_like_a_database

    disguised = tmp_path / "!!not-a-valid-value!!"
    disguised.write_bytes(SQLITE_MAGIC + b"\x00" * 64)
    assert looks_like_a_database(disguised)

    ordinary = tmp_path / "notes.md"
    ordinary.write_text("just text", encoding="utf-8")
    assert not looks_like_a_database(ordinary)

    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert not looks_like_a_database(empty)

    assert not looks_like_a_database(tmp_path / "does-not-exist")


def test_repo_hygiene_reads_the_index_not_the_working_tree(tmp_path, monkeypatch):
    """A commit carries the INDEX, so that is what the guard must inspect.

    Stage a file and then delete it and the index still holds the original
    blob while the path on disk is gone. A working-tree-only content check
    reported such a repository clean - reproduced as an AD state with the
    SQLite magic in the index - and the database blob was free to be committed.
    The first version of this content check had exactly that gap: it fixed the
    RULE, from filename to content, and kept reading the wrong SOURCE.
    """
    import subprocess
    import sqlite3

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    disguised = tmp_path / "disguised"
    connection = sqlite3.connect(disguised)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()
    git("add", "disguised")
    disguised.unlink()  # staged, then removed: the AD state

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard.staged_blob_is_a_database("disguised"), (
        "the staged blob carries the SQLite magic and must be seen"
    )
    assert guard.main() == 1, "a staged database blob must fail the guard"

    git("rm", "--cached", "disguised")
    assert guard.main() == 0


def test_repo_hygiene_reads_only_the_header_of_a_staged_blob(tmp_path, monkeypatch):
    """A guard that rejects bulk data must not load bulk data to do it.

    git cat-file writes the whole object, so buffering it to look at sixteen
    bytes means holding an arbitrarily large file in memory - including exactly
    the oversized blobs this guard exists to report, which could kill the check
    before it reports them. Measured rather than asserted structurally: the
    peak allocation while inspecting a multi-megabyte staged blob must stay
    small.
    """
    import subprocess
    import sqlite3
    import tracemalloc

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    big = tmp_path / "big"
    connection = sqlite3.connect(big)
    connection.execute("create table t(x)")
    connection.executemany(
        "insert into t values (?)", [("x" * 2000,) for _ in range(4000)]
    )
    connection.commit()
    connection.close()
    assert big.stat().st_size > 4_000_000, big.stat().st_size
    git("add", "big")

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    tracemalloc.start()
    detected = guard.staged_blob_is_a_database("big")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert detected
    assert peak < 1_000_000, (
        f"inspecting the blob peaked at {peak} bytes; the header is 16 and "
        "buffering the whole object is what this avoids"
    )


def test_repo_hygiene_does_not_follow_symlinks_out_of_the_repository(
    tmp_path, monkeypatch
):
    """git stores a symlink's target STRING, not the file it points at.

    Path.is_file() and the content read both follow the link, so an untracked
    symlink aimed at a database outside the repository was reported as a
    database - a false positive that blocks the gate over a file that would
    never be committed.
    """
    import subprocess
    import sqlite3

    outside = tmp_path / "outside.db"
    connection = sqlite3.connect(outside)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()

    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        (repo / "linkonly").symlink_to(outside)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("this platform does not permit creating symlinks")

    subprocess.run(["git", "init"], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", repo)
    assert guard.main() == 0, "a symlink is not a database to commit"

    # ...and a real untracked database in the same tree is still reported.
    real = repo / "realdb"
    connection = sqlite3.connect(real)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()
    assert guard.main() == 1


def test_repo_hygiene_inspects_the_index_in_one_git_process(tmp_path, monkeypatch):
    """A gate that runs on every commit must not spawn a process per file.

    Asking git per path cost 5.35 seconds and about 170 processes on this
    repository, against roughly 0.03 seconds before the content check existed,
    growing linearly with the index. The batched reader answers the whole list
    down one pipe while still never holding a blob whole.
    """
    import subprocess
    import sqlite3
    import tracemalloc

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    for index in range(25):
        (tmp_path / f"file{index}.txt").write_text("ordinary", encoding="utf-8")
    disguised = tmp_path / "disguised"
    connection = sqlite3.connect(disguised)
    connection.execute("create table t(x)")
    connection.executemany(
        "insert into t values (?)", [("x" * 2000,) for _ in range(2000)]
    )
    connection.commit()
    connection.close()
    git("add", "-A")

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    spawns = []
    real_popen = subprocess.Popen

    def counting_popen(args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and "cat-file" in list(args):
            spawns.append(list(args))
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", counting_popen)

    tracked = [p for p in guard.tracked_paths()]
    assert len(tracked) > 20, tracked
    tracemalloc.start()
    found = guard.staged_database_paths(tracked)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert found == {"disguised"}, found
    assert len(spawns) == 1, f"one git process for the whole index, got {len(spawns)}"
    assert peak < 1_000_000, peak


def test_repo_hygiene_drains_non_blob_objects(tmp_path, monkeypatch):
    """A non-blob resolved object must still have its body consumed.

    git cat-file --batch returns oid type size + body for any resolved object.
    If the body of a commit (e.g. from a gitlink) is left in the pipe, every
    subsequent path reads the wrong frame and the guard becomes unreliable.
    The gitlink is named so it sorts before the database path; without an
    unconditional drain the database is never seen.
    """
    import subprocess
    import sqlite3

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")

    (tmp_path / "readme").write_text("x", encoding="utf-8")
    git("add", "readme")
    git("commit", "--no-gpg-sign", "-m", "c")
    commit_sha = git("rev-parse", "HEAD").stdout.decode().strip()

    # Stage a gitlink whose target commit is present in this object store.
    git("update-index", "--add", "--cacheinfo", f"160000,{commit_sha},aaa-sub")

    disguised = tmp_path / "disguised"
    connection = sqlite3.connect(disguised)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()
    git("add", "disguised")

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    tracked = guard.tracked_paths()
    assert "aaa-sub" in tracked and "disguised" in tracked, tracked

    found = guard.staged_database_paths(tracked)
    assert found == {"disguised"}, (
        "after draining the gitlink/commit body the database must still be "
        f"recognised; got {found!r}"
    )


def test_repo_hygiene_accepts_newline_in_staged_path(tmp_path, monkeypatch):
    """Request framing must tolerate paths that contain newlines.

    Newline-delimited cat-file requests split a path that itself contains a
    newline into multiple queries, desynchronising the response stream. With
    ``--batch -Z`` both sides are NUL-framed, so the path is one frame.
    """
    import subprocess
    import sqlite3

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")

    payload = tmp_path / "payload.bin"
    connection = sqlite3.connect(payload)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()
    blob_sha = git("hash-object", "-w", str(payload)).stdout.decode().strip()
    weird_path = "dir/with\nnewline"
    # Pass the path with a real newline via argv; git accepts it in --cacheinfo.
    git(
        "update-index",
        "--add",
        "--cacheinfo",
        f"100644,{blob_sha},{weird_path}",
    )

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    tracked = guard.tracked_paths()
    assert weird_path in tracked, tracked

    found = guard.staged_database_paths(tracked)
    assert found == {weird_path}, (
        "a database staged under a path containing a newline must still be "
        f"detected under NUL framing; got {found!r}"
    )


def test_repo_hygiene_missing_response_with_space_in_path(tmp_path, monkeypatch):
    """A missing response whose path contains spaces must not crash the gate.

    cat-file answers ``:sub module missing\0``. Splitting that header and
    reading a fixed field index treats ``module`` as the type and tries to
    parse ``missing`` as a size. Detection is by the trailing `` missing``
    marker so the requested name can contain any characters the protocol
    permits.
    """
    import subprocess
    import sqlite3

    def git(*args, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")

    # gitlink whose target commit is absent → missing response.
    fake = "a" * 40
    git("update-index", "--add", "--cacheinfo", f"160000,{fake},sub module")

    disguised = tmp_path / "disguised"
    connection = sqlite3.connect(disguised)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()
    git("add", "disguised")

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    tracked = guard.tracked_paths()
    assert "sub module" in tracked and "disguised" in tracked, tracked

    found = guard.staged_database_paths(tracked)
    assert found == {"disguised"}, (
        "a missing response with spaces in the path must be skipped without "
        f"desync or crash; got {found!r}"
    )


def test_repo_hygiene_fails_closed_when_cat_file_batch_fails(tmp_path, monkeypatch):
    """If cat-file itself fails, the gate must not report a clean tree.

    An older Git without ``-Z``, or any other launch failure, used to leave
    stdout empty; the reader returned an empty set and main() printed
    ``repo hygiene ok`` while a staged database sat in the index. Process
    failure is now a hard error.
    """
    import subprocess
    import sqlite3

    def git(*args):
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    git("init")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    disguised = tmp_path / "disguised"
    connection = sqlite3.connect(disguised)
    connection.execute("create table t(x)")
    connection.commit()
    connection.close()
    git("add", "disguised")

    import scripts.check_repo_hygiene as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    real_popen = subprocess.Popen

    def failing_popen(args, *rest, **kwargs):
        if isinstance(args, (list, tuple)) and "cat-file" in list(args):
            # Force the same class of failure as an unsupported -Z option.
            args = ["git", "cat-file", "--batch", "--not-a-real-flag"]
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", failing_popen)

    try:
        guard.staged_database_paths(["disguised"])
    except RuntimeError as exc:
        assert "cat-file" in str(exc).lower() or "failed" in str(exc).lower(), exc
    else:
        raise AssertionError("cat-file failure must raise, not return an empty set")
