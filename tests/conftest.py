from __future__ import annotations

import json
from pathlib import Path

import pytest

from wuwaterm.builder import build_database


@pytest.fixture()
def sample_data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "wutheringdata"
    (root / "ConfigDB").mkdir(parents=True)
    (root / "TextMap" / "zh-Hans").mkdir(parents=True)
    (root / "TextMap" / "en").mkdir(parents=True)

    configs = {
        "RoleInfo.json": [
            {"Id": 1304, "Name": "RoleInfo_1304_Name"},
            {"Id": 1505, "Name": "RoleInfo_1505_Name"},
            {"Id": 1102, "Name": "RoleInfo_1102_Name"},
            {"Id": 1106, "Name": "RoleInfo_1106_Name"},
            {"Id": 1207, "Name": "RoleInfo_1207_Name"},
            {"Id": 1607, "Name": "RoleInfo_1607_Name"},
            {"Id": 1608, "Name": "RoleInfo_1608_Name"},
        ],
        "WeaponConf.json": [{"ItemId": 2101, "WeaponName": "WeaponConf_2101_WeaponName"}],
        "PhantomItem.json": [{"ItemId": 6001, "MonsterName": "MonsterInfo_6001_Name"}],
        "ItemInfo.json": [
            {"Id": 1, "Name": "ItemInfo_1_Name"},
            {"Id": 2, "Name": "ItemInfo_2_Name"},
        ],
        "Skill.json": [
            {"Id": 1001, "SkillName": "Skill_1001_SkillName"},
            {"Id": 1002, "SkillName": "Skill_1002_SkillName"},
        ],
        "PhantomFetterGroup.json": [{"Id": 1, "FetterGroupName": "PhantomFetter_1_Name"}],
        "Area.json": [{"AreaId": 2, "Title": "Area_2_Title"}],
    }
    for name, rows in configs.items():
        (root / "ConfigDB" / name).write_text(
            json.dumps(rows, ensure_ascii=False),
            encoding="utf-8",
        )

    zh = {
        "LoadingTipsText_1005_Title": "共鸣者",
        "Term850082_Title": "声骸",
        "OccupationConfig_漂泊者_Name": "漂泊者",
        "RoleInfo_1304_Name": "今汐",
        "RoleInfo_1505_Name": "守岸人",
        "RoleInfo_1102_Name": "安可",
        "RoleInfo_1106_Name": "卡卡罗",
        "RoleInfo_1207_Name": "椿",
        "RoleInfo_1607_Name": "卡提希娅",
        "RoleInfo_1608_Name": "洛瑟菈",
        "WeaponConf_2101_WeaponName": "纹秋",
        "MonsterInfo_6001_Name": "先锋幼岩",
        "ItemInfo_1_Name": "联觉经验",
        "ItemInfo_2_Name": "巧手烹调",
        "Skill_1001_SkillName": "风羽为刃",
        "Skill_1002_SkillName": "巧手烹调",
        "PhantomFetter_1_Name": "凝夜白霜",
        "Area_2_Title": "云陵谷",
    }
    en = {
        "LoadingTipsText_1005_Title": "Resonator",
        "Term850082_Title": "Echo",
        "OccupationConfig_漂泊者_Name": "Rover",
        "RoleInfo_1304_Name": "Jinhsi",
        "RoleInfo_1505_Name": "Shorekeeper",
        "RoleInfo_1102_Name": "Encore",
        "RoleInfo_1106_Name": "Calcharo",
        "RoleInfo_1207_Name": "Camellya",
        "RoleInfo_1607_Name": "Cartethyia",
        "RoleInfo_1608_Name": "Lucilla",
        "WeaponConf_2101_WeaponName": "Autumntrace",
        "MonsterInfo_6001_Name": "Vanguard Junrock",
        "ItemInfo_1_Name": "Union EXP",
        "ItemInfo_2_Name": "Life Skill",
        "Skill_1001_SkillName": "Feather as Blade",
        "Skill_1002_SkillName": "Skillful Cooking",
        "PhantomFetter_1_Name": "Freezing Frost",
        "Area_2_Title": "Gorges of Spirits",
    }
    for lang, mapping in (("zh-Hans", zh), ("en", en)):
        (root / "TextMap" / lang / "MultiText.json").write_text(
            json.dumps(mapping, ensure_ascii=False),
            encoding="utf-8",
        )
    return root


@pytest.fixture()
def sample_db(tmp_path: Path, sample_data_dir: Path) -> Path:
    db_path = tmp_path / "terms.db"
    build_database(sample_data_dir, db_path)
    return db_path
