from __future__ import annotations

import pytest

from wuwaterm.builder import (
    BuildError,
    build_database,
    build_database_atomic,
    source_profile_for_data_dir,
)
from wuwaterm.db import category_counts, connect


def test_builder_extracts_required_categories(sample_db):
    with connect(sample_db) as conn:
        counts = category_counts(conn)

    for category in (
        "core_term",
        "resonator",
        "weapon",
        "echo",
        "item",
        "skill",
        "sonata_effect",
        "location",
    ):
        assert counts[category] > 0


def test_builder_uses_multitext_symbol_keys(sample_db):
    with connect(sample_db) as conn:
        row = conn.execute(
            "SELECT zh, en FROM terms WHERE text_key = 'RoleInfo_1304_Name'"
        ).fetchone()

    assert row["zh"] == "今汐"
    assert row["en"] == "Jinhsi"


def test_builder_selects_legacy_profile_from_configdb_layout(sample_data_dir):
    profile = source_profile_for_data_dir(sample_data_dir)

    assert profile.name == "dimbreath_legacy"


def test_build_database_accepts_explicit_source_profile(sample_data_dir, tmp_path):
    db_path = tmp_path / "profiled.db"
    build_database(sample_data_dir, db_path, profile_name="dimbreath_legacy")

    with connect(db_path) as conn:
        metadata = dict(conn.execute("SELECT key, value FROM metadata").fetchall())

    assert metadata["source_profile"] == "dimbreath_legacy"
    assert metadata["wutheringdata_commit"] == "e9234ffe094b2d944d16b222d31102e8ab32d954"


def test_build_database_atomic_replaces_target_after_success(sample_data_dir, tmp_path):
    db_path = tmp_path / "terms.db"
    db_path.write_text("old database", encoding="utf-8")

    count = build_database_atomic(
        sample_data_dir, db_path, profile_name="dimbreath_legacy"
    )

    assert count > 0
    with connect(db_path) as conn:
        counts = category_counts(conn)
    assert counts["resonator"] > 0
    assert not list(tmp_path.glob(".terms.db.*.tmp"))


def test_build_database_atomic_failure_keeps_existing_db(
    monkeypatch, sample_data_dir, tmp_path
):
    db_path = tmp_path / "terms.db"
    db_path.write_text("old database", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise BuildError("boom")

    monkeypatch.setattr("wuwaterm.builder.iter_records", boom)

    with pytest.raises(BuildError):
        build_database_atomic(sample_data_dir, db_path, profile_name="dimbreath_legacy")

    assert db_path.read_text(encoding="utf-8") == "old database"
    assert not list(tmp_path.glob(".terms.db.*.tmp"))
