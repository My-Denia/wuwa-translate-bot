"""Project constants."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceProfile:
    name: str
    repo_url: str
    pinned_commit: str
    layout: str
    sparse_paths: tuple[str, ...]
    textmap_root: str
    textmap_record_kind: str


SOURCE_PROFILES = {
    "arikatsu": SourceProfile(
        name="arikatsu",
        repo_url="https://github.com/Arikatsu/WutheringWaves_Data.git",
        pinned_commit="58ec43698d2b4e188cb285467ce1ae887612dd92",
        layout="arikatsu_textmaps",
        sparse_paths=("Textmaps", "BinData"),
        textmap_root="Textmaps",
        textmap_record_kind="id_content_arrays",
    ),
    "dimbreath_legacy": SourceProfile(
        name="dimbreath_legacy",
        repo_url="https://github.com/Dimbreath/WutheringData.git",
        pinned_commit="e9234ffe094b2d944d16b222d31102e8ab32d954",
        layout="dimbreath_configdb",
        sparse_paths=("ConfigDB", "TextMap/zh-Hans", "TextMap/en"),
        textmap_root="TextMap",
        textmap_record_kind="multitext_object",
    ),
}

DEFAULT_SOURCE_PROFILE_NAME = "arikatsu"
SOURCE_PROFILE_ENV = "WUWATERM_SOURCE_PROFILE"


def get_source_profile(name: str | None = None) -> SourceProfile:
    profile_name = name or os.getenv(SOURCE_PROFILE_ENV, DEFAULT_SOURCE_PROFILE_NAME)
    try:
        return SOURCE_PROFILES[profile_name]
    except KeyError as exc:
        known = ", ".join(sorted(SOURCE_PROFILES))
        raise ValueError(f"unknown source profile {profile_name!r}; known profiles: {known}") from exc


def source_profile_choices() -> tuple[str, ...]:
    return tuple(sorted(SOURCE_PROFILES))


ACTIVE_SOURCE_PROFILE = get_source_profile()
PINNED_WUTHERINGDATA_COMMIT = ACTIVE_SOURCE_PROFILE.pinned_commit
WUTHERINGDATA_REPO = ACTIVE_SOURCE_PROFILE.repo_url

CATEGORY_ORDER = {
    "core_term": 0,
    "resonator": 10,
    "weapon": 20,
    "echo": 30,
    "skill": 40,
    "sonata_effect": 50,
    "location": 60,
    "item": 70,
    "speaker": 80,
}

CORE_TERM_KEYS = {
    "LoadingTipsText_1005_Title": "core_term",  # 共鸣者 -> Resonator
    "Term850082_Title": "core_term",  # 声骸 -> Echo
    "OccupationConfig_漂泊者_Name": "core_term",  # 漂泊者 -> Rover
}
