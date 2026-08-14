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
    import importlib

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
