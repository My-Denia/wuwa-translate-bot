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
    version_file: str | None = None
    expected_game_version: str | None = None
    expected_resource_version: str | None = None
    expected_changelist: str | None = None
    representative_exact_hits: tuple[tuple[str, str], ...] = ()


SOURCE_PROFILES = {
    "arikatsu": SourceProfile(
        name="arikatsu",
        repo_url="https://github.com/Arikatsu/WutheringWaves_Data.git",
        pinned_commit="6ce8d5eda49f2930da84d8846c144432142c7465",
        layout="arikatsu_textmaps",
        sparse_paths=("README.md", "Textmaps", "BinData"),
        textmap_root="Textmaps",
        textmap_record_kind="id_content_arrays",
        version_file="README.md",
        expected_game_version="3.6.0",
        expected_resource_version="3.6.4",
        expected_changelist="8464573",
        # Measured against the built 3.6 candidate, not copied from upstream
        # text: 景燃 is new at 3.6.0 and is the only zh for "Jingran" and the
        # only en for 景燃 in both directions. The 3.5 pair 穗穗 -> Suisui was
        # retired here because 3.6 adds a second speaker row
        # 穗穗（通讯中） -> Suisui, so the reverse direction is no longer
        # single-valued and the check would fail on a correct build.
        representative_exact_hits=(("景燃", "Jingran"),),
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
