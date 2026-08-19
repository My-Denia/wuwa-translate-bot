"""Build a term-only SQLite dictionary from WutheringData entity configs."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import CORE_TERM_KEYS, SourceProfile, get_source_profile
from .data_source import inspect_data_source
from .db import create_database
from .models import TermRecord
from .normalize import clean_source_text


@dataclass(frozen=True)
class CategorySpec:
    category: str
    config_file: str
    id_field: str
    text_fields: tuple[str, ...]


CATEGORY_SPECS = (
    CategorySpec("resonator", "RoleInfo.json", "Id", ("Name",)),
    CategorySpec("weapon", "WeaponConf.json", "ItemId", ("WeaponName",)),
    CategorySpec("echo", "PhantomItem.json", "ItemId", ("MonsterName",)),
    CategorySpec("item", "ItemInfo.json", "Id", ("Name",)),
    CategorySpec("skill", "Skill.json", "Id", ("SkillName",)),
    CategorySpec("sonata_effect", "PhantomFetterGroup.json", "Id", ("FetterGroupName",)),
    CategorySpec("location", "Area.json", "AreaId", ("Title",)),
)

ARIKATSU_CORE_TEXT_IDS = frozenset(CORE_TERM_KEYS)

ARIKATSU_BIN_SPECS = (
    CategorySpec("resonator", "role/roleinfo.json", "Id", ("Name",)),
    CategorySpec("weapon", "weapon/weaponconf.json", "ItemId", ("WeaponName",)),
    CategorySpec("echo", "phantom/phantomitem.json", "ItemId", ("MonsterName",)),
    CategorySpec("item", "item/iteminfo.json", "Id", ("Name",)),
    CategorySpec("skill", "skill/skill.json", "Id", ("SkillName",)),
    CategorySpec("sonata_effect", "phantom/phantomfettergroup.json", "Id", ("FetterGroupName",)),
    CategorySpec("location", "area/area.json", "AreaId", ("Title",)),
)


class BuildError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_multitext(data_dir: Path, lang: str) -> dict[str, str]:
    path = data_dir / "TextMap" / lang / "MultiText.json"
    if not path.exists():
        raise BuildError(f"missing TextMap file: {path}")
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise BuildError(f"expected object in {path}")
    return {str(k): str(v) for k, v in raw.items()}


def load_arikatsu_string_texts(data_dir: Path, lang: str) -> dict[str, str]:
    path = data_dir / "Textmaps" / lang / "multi_text" / "MultiText.json"
    if not path.exists():
        raise BuildError(f"missing Arikatsu multi_text file: {path}")
    raw = load_json(path)
    if not isinstance(raw, list):
        raise BuildError(f"expected array in {path}")
    mapping: dict[str, str] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        if "Id" not in row or "Content" not in row:
            continue
        text = clean_source_text(str(row["Content"]))
        if text:
            mapping[str(row["Id"])] = text
    return mapping


def _record_for_key(
    *,
    category: str,
    source_file: str,
    source_id: str,
    text_key: str,
    zh_map: dict[str, str],
    en_map: dict[str, str],
) -> TermRecord | None:
    zh_raw = zh_map.get(text_key)
    en_raw = en_map.get(text_key)
    if zh_raw is None or en_raw is None:
        return None
    zh = clean_source_text(zh_raw)
    en = clean_source_text(en_raw)
    if not zh or not en:
        return None
    return TermRecord(
        category=category,
        source_file=source_file,
        source_id=source_id,
        text_key=text_key,
        zh=zh,
        en=en,
    )


def source_profile_for_data_dir(data_dir: str | Path, profile_name: str | None = None) -> SourceProfile:
    root = Path(data_dir)
    if profile_name is not None:
        return get_source_profile(profile_name)
    if (root / "Textmaps").exists():
        return get_source_profile("arikatsu")
    if (root / "ConfigDB").exists():
        return get_source_profile("dimbreath_legacy")
    return get_source_profile()


def iter_records(data_dir: str | Path, profile_name: str | None = None) -> list[TermRecord]:
    profile = source_profile_for_data_dir(data_dir, profile_name)
    if profile.layout == "arikatsu_textmaps":
        return iter_arikatsu_records(data_dir)
    return iter_dimbreath_records(data_dir)


def iter_arikatsu_records(data_dir: str | Path) -> list[TermRecord]:
    root = Path(data_dir)
    bin_root = root / "BinData"
    if not bin_root.exists():
        raise BuildError(f"missing BinData directory: {bin_root}")
    zh_map = load_arikatsu_string_texts(root, "zh-Hans")
    en_map = load_arikatsu_string_texts(root, "en")
    records: list[TermRecord] = []

    for text_key, category in CORE_TERM_KEYS.items():
        record = _record_for_key(
            category=category,
            source_file="Textmaps/multi_text/MultiText.json",
            source_id=text_key,
            text_key=text_key,
            zh_map=zh_map,
            en_map=en_map,
        )
        if record:
            records.append(record)

    for spec in ARIKATSU_BIN_SPECS:
        path = bin_root / spec.config_file
        if not path.exists():
            raise BuildError(f"missing required BinData file: {path}")
        rows = load_json(path)
        if not isinstance(rows, list):
            raise BuildError(f"expected list in {path}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get(spec.id_field, ""))
            if not source_id:
                continue
            for field in spec.text_fields:
                text_key = row.get(field)
                if not isinstance(text_key, str) or not text_key:
                    continue
                record = _record_for_key(
                    category=spec.category,
                    source_file=f"BinData/{spec.config_file}",
                    source_id=source_id,
                    text_key=text_key,
                    zh_map=zh_map,
                    en_map=en_map,
                )
                if record:
                    records.append(record)

    speaker_path = bin_root / "speaker" / "speaker.json"
    if speaker_path.exists():
        rows = load_json(speaker_path)
        if not isinstance(rows, list):
            raise BuildError(f"expected list in {speaker_path}")
        for row in rows:
            if not isinstance(row, dict) or not row.get("Id"):
                continue
            source_id = str(row["Id"])
            text_key = f"Speaker_{source_id}_Name"
            record = _record_for_key(
                category="speaker",
                source_file="BinData/speaker/speaker.json",
                source_id=source_id,
                text_key=text_key,
                zh_map=zh_map,
                en_map=en_map,
            )
            if record:
                records.append(record)

    if not records:
        raise BuildError("no term records were extracted")
    return records


def iter_dimbreath_records(data_dir: str | Path) -> list[TermRecord]:
    root = Path(data_dir)
    config_root = root / "ConfigDB"
    if not config_root.exists():
        raise BuildError(f"missing ConfigDB directory: {config_root}")

    zh_map = load_multitext(root, "zh-Hans")
    en_map = load_multitext(root, "en")
    records: list[TermRecord] = []

    for text_key, category in CORE_TERM_KEYS.items():
        record = _record_for_key(
            category=category,
            source_file="TextMap/MultiText.json",
            source_id=text_key,
            text_key=text_key,
            zh_map=zh_map,
            en_map=en_map,
        )
        if record:
            records.append(record)

    for spec in CATEGORY_SPECS:
        path = config_root / spec.config_file
        if not path.exists():
            raise BuildError(f"missing required config file: {path}")
        rows = load_json(path)
        if not isinstance(rows, list):
            raise BuildError(f"expected list in {path}")
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get(spec.id_field, ""))
            if not source_id:
                continue
            for field in spec.text_fields:
                text_key = row.get(field)
                if not isinstance(text_key, str) or not text_key:
                    continue
                record = _record_for_key(
                    category=spec.category,
                    source_file=spec.config_file,
                    source_id=source_id,
                    text_key=text_key,
                    zh_map=zh_map,
                    en_map=en_map,
                )
                if record:
                    records.append(record)

    if not records:
        raise BuildError("no term records were extracted")
    return records


def build_database(
    data_dir: str | Path,
    db_path: str | Path,
    profile_name: str | None = None,
) -> int:
    profile = source_profile_for_data_dir(data_dir, profile_name)
    provenance = inspect_data_source(data_dir, profile.name)
    records = iter_records(data_dir, profile.name)
    if inspect_data_source(data_dir, profile.name) != provenance:
        raise BuildError("source provenance changed during database extraction")
    create_database(
        db_path,
        records,
        source_profile=profile,
        source_provenance=provenance,
    )
    return len(records)


def build_database_atomic(
    data_dir: str | Path,
    db_path: str | Path,
    profile_name: str | None = None,
) -> int:
    """Build a database in the target directory, then atomically replace it."""
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        count = build_database(data_dir, tmp_path, profile_name=profile_name)
        os.replace(tmp_path, target)
        return count
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
