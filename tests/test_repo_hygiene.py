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
