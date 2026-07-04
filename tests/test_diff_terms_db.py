from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.diff_terms_db import diff_databases, main
from wuwaterm.db import initialize


def _term(
    category: str,
    source_file: str,
    source_id: str,
    text_key: str,
    zh: str,
    en: str,
) -> tuple[str, str, str, str, str, str, str, str, str, str, int]:
    return (
        category,
        source_file,
        source_id,
        text_key,
        zh,
        en,
        zh.casefold(),
        en.casefold(),
        "",
        "",
        1,
    )


def _write_db(
    path: Path,
    terms: list[tuple[str, str, str, str, str, str, str, str, str, str, int]],
    metadata: dict[str, str],
) -> Path:
    conn = sqlite3.connect(path)
    try:
        initialize(conn)
        conn.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        conn.executemany(
            """
            INSERT INTO terms(
              category, source_file, source_id, text_key, zh, en, zh_norm, en_norm,
              pinyin, pinyin_abbrev, priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            terms,
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _fixture_dbs(tmp_path: Path) -> tuple[Path, Path]:
    old_terms = [
        _term("resonator", "RoleInfo.json", "1304", "RoleInfo_1304_Name", "今汐", "Jinhsi"),
        _term("item", "ItemInfo.json", "1", "ItemInfo_1_Name", "联觉经验", "Union EXP"),
        _term("skill", "Skill.json", "1001", "Skill_1001_Name", "风羽为刃", "Feather as Blade"),
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸", "Echo"),
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸甲", "Echo A"),
    ]
    new_terms = [
        _term("resonator", "RoleInfo.json", "1304", "RoleInfo_1304_Name", "今汐", "Jinhsi Prime"),
        _term("weapon", "WeaponConf.json", "2101", "WeaponConf_2101_Name", "纹秋", "Autumntrace"),
        _term("skill", "Skill.json", "1001", "Skill_1001_Name", "风羽为刃", "Feather as Blade"),
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸", "Echo"),
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸乙", "Echo B"),
    ]
    old_db = _write_db(
        tmp_path / "old.db",
        old_terms,
        {"GameVer": "1.0", "source": "old"},
    )
    new_db = _write_db(
        tmp_path / "new.db",
        new_terms,
        {"GameVer": "1.1", "ResVer": "2"},
    )
    return old_db, new_db


def test_diff_terms_db_text_report_includes_required_sections(tmp_path, capsys):
    old_db, new_db = _fixture_dbs(tmp_path)

    assert main([str(old_db), str(new_db)]) == 0

    out = capsys.readouterr().out
    assert "old terms: 5" in out
    assert "new terms: 5" in out
    assert "added: 2" in out
    assert "removed: 2" in out
    assert "changed zh/en pairs: 1" in out
    assert "ambiguous source keys: 1" in out
    assert "item: 1 -> 0 (-1)" in out
    assert "weapon: 0 -> 1 (+1)" in out
    assert "GameVer: 1.0 -> 1.1" in out
    assert "ResVer: <missing> -> 2" in out
    assert "source: old -> <missing>" in out
    assert "RoleInfo.json:1304 RoleInfo_1304_Name: 今汐 / Jinhsi -> 今汐 / Jinhsi Prime" in out
    assert "Echo.json:6001 Echo_6001_Name: old pairs=2, new pairs=2" in out
    assert "Examples by category" in out
    assert "- echo:" in out


def test_diff_terms_db_json_report_is_structured_and_deterministic(tmp_path, capsys):
    old_db, new_db = _fixture_dbs(tmp_path)

    assert main([str(old_db), str(new_db), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"] == {"old_terms": 5, "new_terms": 5}
    assert payload["summary"] == {
        "added": 2,
        "removed": 2,
        "changed_zh_en_pairs": 1,
        "ambiguous_source_keys": 1,
    }
    assert [item["category"] for item in payload["category_count_changes"]] == [
        "item",
        "weapon",
    ]
    assert [item["key"] for item in payload["metadata_differences"]] == [
        "GameVer",
        "ResVer",
        "source",
    ]
    assert payload["changed"][0]["old"] == {"zh": "今汐", "en": "Jinhsi"}
    assert payload["changed"][0]["new"] == {"zh": "今汐", "en": "Jinhsi Prime"}
    assert payload["ambiguous_source_keys"][0]["key"]["category"] == "echo"
    assert "echo" in payload["examples_by_category"]


def test_ambiguous_source_keys_are_not_reported_as_changed(tmp_path):
    old_db, new_db = _fixture_dbs(tmp_path)

    report = diff_databases(old_db, new_db)

    assert [change.category for change in report.changed] == ["resonator"]
    assert [item.category for item in report.ambiguous_source_keys] == ["echo"]
    assert ("声骸乙", "Echo B") in [term.pair for term in report.added]
    assert ("声骸甲", "Echo A") in [term.pair for term in report.removed]


def test_single_sided_duplicate_source_key_reports_pair_delta_without_ambiguity(tmp_path):
    old_terms = [
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸", "Echo"),
    ]
    new_terms = [
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸", "Echo"),
        _term("echo", "Echo.json", "6001", "Echo_6001_Name", "声骸乙", "Echo B"),
    ]
    old_db = _write_db(tmp_path / "old.db", old_terms, {"GameVer": "1.0"})
    new_db = _write_db(tmp_path / "new.db", new_terms, {"GameVer": "1.0"})

    report = diff_databases(old_db, new_db)

    assert report.changed == ()
    assert report.ambiguous_source_keys == ()
    assert [term.pair for term in report.added] == [("声骸乙", "Echo B")]
    assert report.removed == ()


def test_identical_databases_report_no_changes(tmp_path, capsys):
    terms = [
        _term("skill", "Skill.json", "1001", "Skill_1001_Name", "风羽为刃", "Feather as Blade")
    ]
    old_db = _write_db(tmp_path / "old.db", terms, {"GameVer": "1.0"})
    new_db = _write_db(tmp_path / "new.db", terms, {"GameVer": "1.0"})

    assert main([str(old_db), str(new_db)]) == 0

    out = capsys.readouterr().out
    assert "added: 0" in out
    assert "removed: 0" in out
    assert "changed zh/en pairs: 0" in out
    assert "Category count changes\n- none" in out
    assert "Metadata differences\n- none" in out
    assert "Examples by category\n- none" in out


def test_missing_database_is_not_created(tmp_path, capsys):
    missing_db = tmp_path / "missing.db"
    existing_db = _write_db(tmp_path / "new.db", [], {"GameVer": "1.0"})

    assert main([str(missing_db), str(existing_db)]) == 1

    assert not missing_db.exists()
    assert "database does not exist" in capsys.readouterr().err
