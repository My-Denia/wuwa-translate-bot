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


def test_reverse_lookup_is_same_table_only(sample_db):
    service = TermService(sample_db)

    assert service.term_text("Echo") == "Echo"


def test_pinyin_fuzzy_returns_top_candidate(sample_db):
    service = TermService(sample_db)

    for query, expected in PINYIN_SEEDS.items():
        result = service.lookup(query)
        assert result.best is not None
        assert result.best.entry.en == expected
        assert result.best.reason == "pinyin"
