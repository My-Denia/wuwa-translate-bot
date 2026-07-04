from __future__ import annotations

from wuwaterm.db import connect, insert_records
from wuwaterm.lookup import TermService
from wuwaterm.models import TermRecord


GOLDEN_SEEDS = {
    "共鸣者": "Resonator",
    "声骸": "Echo",
    "漂泊者": "Rover",
    "今汐": "Jinhsi",
    "守岸人": "Shorekeeper",
    "安可": "Encore",
    "卡卡罗": "Calcharo",
    "椿": "Camellya",
}

PINYIN_SEEDS = {
    "gongmingzhe": "Resonator",
    "shenghai": "Echo",
    "piaobozhe": "Rover",
    "jinxi": "Jinhsi",
    "shouanren": "Shorekeeper",
    "anke": "Encore",
    "kakaluo": "Calcharo",
    "chun": "Camellya",
}


def test_exact_hit_returns_db_english_byte_for_byte(sample_db):
    service = TermService(sample_db)

    for zh, en in GOLDEN_SEEDS.items():
        assert service.term_text(zh) == en


def test_exact_hit_prefers_sorted_top_candidate_on_db_conflict(sample_db):
    with connect(sample_db) as conn:
        insert_records(
            conn,
            [
                TermRecord(
                    category="resonator",
                    source_file="BinData/role/roleinfo.json",
                    source_id="5103",
                    text_key="RoleInfo_5103_Name",
                    zh="守岸人",
                    en="The Shorekeeper",
                )
            ],
        )
        conn.commit()

    service = TermService(sample_db)

    assert service.term_text("守岸人") == "Shorekeeper"


def test_exact_lookup_does_not_truncate_high_priority_zh_candidate(sample_db):
    records = [
        TermRecord(
            category="resonator",
            source_file=f"A_Generic_{idx:02d}.json",
            source_id=str(idx),
            text_key=f"Generic_{idx:02d}_Name",
            zh="重复测试词",
            en=f"Generic {idx:02d}",
        )
        for idx in range(25)
    ]
    records.append(
        TermRecord(
            category="resonator",
            source_file="RoleInfo.json",
            source_id="9999",
            text_key="RoleInfo_9999_Name",
            zh="重复测试词",
            en="Priority Official",
        )
    )
    with connect(sample_db) as conn:
        insert_records(conn, records)
        conn.commit()

    result = TermService(sample_db).lookup("重复测试词", limit=5)

    assert result.exact is True
    assert result.best is not None
    assert result.best.entry.en == "Priority Official"
    assert result.best.entry.source_file == "RoleInfo.json"


def test_exact_lookup_does_not_truncate_high_priority_en_candidate(sample_db):
    records = [
        TermRecord(
            category="resonator",
            source_file=f"A_Generic_{idx:02d}.json",
            source_id=str(idx),
            text_key=f"Generic_{idx:02d}_Name",
            zh=f"普通候选{idx:02d}",
            en="Shared Exact Term",
        )
        for idx in range(25)
    ]
    records.append(
        TermRecord(
            category="resonator",
            source_file="RoleInfo.json",
            source_id="9999",
            text_key="RoleInfo_9999_Name",
            zh="高优先候选但是长度更长",
            en="Shared Exact Term",
        )
    )
    with connect(sample_db) as conn:
        insert_records(conn, records)
        conn.commit()

    result = TermService(sample_db).lookup("Shared Exact Term", limit=5)

    assert result.exact is True
    assert result.best is not None
    assert result.best.entry.zh == "高优先候选但是长度更长"
    assert result.best.entry.source_file == "RoleInfo.json"

def test_reverse_lookup_is_same_table_only(sample_db):
    service = TermService(sample_db)

    assert service.term_text("Echo") == "Echo"


def test_lookup_exact_does_not_use_fuzzy_path(monkeypatch, sample_db):
    service = TermService(sample_db)

    def fail_fuzzy(*_args, **_kwargs):
        raise AssertionError("lookup_exact must not call fuzzy lookup")

    monkeypatch.setattr(service, "_fuzzy", fail_fuzzy)

    hit = service.lookup_exact("声骸")
    assert hit.exact is True
    assert hit.best is not None
    assert hit.best.entry.en == "Echo"

    miss = service.lookup_exact("今汐说声骸很强")
    assert miss.exact is False
    assert miss.candidates == ()


def test_pinyin_fuzzy_returns_top_candidate(sample_db):
    service = TermService(sample_db)

    for query, expected in PINYIN_SEEDS.items():
        result = service.lookup(query)
        assert result.best is not None
        assert result.best.entry.en == expected
        assert result.best.reason == "pinyin"
