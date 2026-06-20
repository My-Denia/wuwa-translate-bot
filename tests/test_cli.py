from __future__ import annotations

from wuwaterm.cli import main


def test_build_db_atomic_flag_routes_to_atomic_builder(monkeypatch, capsys):
    calls = []

    def fake_build_database(*_args, **_kwargs):
        raise AssertionError("non-atomic builder should not be used")

    def fake_build_database_atomic(data_dir, db_path, profile_name=None):
        calls.append((data_dir, db_path, profile_name))
        return 7

    monkeypatch.setattr("wuwaterm.cli.build_database", fake_build_database)
    monkeypatch.setattr(
        "wuwaterm.cli.build_database_atomic", fake_build_database_atomic
    )

    assert (
        main(
            [
                "build-db",
                "--data-dir",
                "data-dir",
                "--db",
                "terms.db",
                "--profile",
                "arikatsu",
                "--atomic",
            ]
        )
        == 0
    )

    assert calls == [("data-dir", "terms.db", "arikatsu")]
    assert capsys.readouterr().out == "built 7 extracted records into terms.db\n"


def test_build_db_without_atomic_uses_regular_builder(monkeypatch):
    calls = []

    def fake_build_database(data_dir, db_path, profile_name=None):
        calls.append((data_dir, db_path, profile_name))
        return 3

    def fake_build_database_atomic(*_args, **_kwargs):
        raise AssertionError("atomic builder should not be used")

    monkeypatch.setattr("wuwaterm.cli.build_database", fake_build_database)
    monkeypatch.setattr(
        "wuwaterm.cli.build_database_atomic", fake_build_database_atomic
    )

    assert main(["build-db", "--data-dir", "data-dir", "--db", "terms.db"]) == 0

    assert calls == [("data-dir", "terms.db", None)]
