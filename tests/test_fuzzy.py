from __future__ import annotations

from wuwaterm.lookup import TermService


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


def test_offline_pinyin_seed_queries_are_top_one(sample_db):
    service = TermService(sample_db)

    for query, expected in PINYIN_SEEDS.items():
        result = service.lookup(query)
        assert result.best is not None
        assert result.best.entry.en == expected
        assert result.best.reason == "pinyin"
