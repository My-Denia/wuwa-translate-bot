"""Tests for wuwaterm_client.config."""

from __future__ import annotations

import json
from pathlib import Path

from wuwaterm_client.config import ClientConfig, config_path


def test_config_file_never_contains_the_credential(tmp_path: Path) -> None:
    config = ClientConfig(
        base_url="http://127.0.0.1:9000",
        request_timeout_seconds=5.0,
        translate_timeout_seconds=30.0,
    )
    config.save(base_dir=tmp_path)

    path = config_path(tmp_path)
    assert path.exists()
    raw_text = path.read_text(encoding="utf-8")

    for forbidden in ("token", "authorization", "bearer", "wtd1."):
        assert forbidden not in raw_text.lower()

    data = json.loads(raw_text)
    assert set(data) == {
        "base_url",
        "request_timeout_seconds",
        "translate_timeout_seconds",
    }


def test_config_round_trip(tmp_path: Path) -> None:
    original = ClientConfig(
        base_url="http://example.local:8787", request_timeout_seconds=12.0
    )
    original.save(base_dir=tmp_path)

    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded == original


def test_config_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded == ClientConfig()


def test_config_load_malformed_file_returns_defaults(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all {{{", encoding="utf-8")
    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded == ClientConfig()


def test_config_load_ignores_unrecognized_keys(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"base_url": "http://x:1", "some_future_field": "ignored"}),
        encoding="utf-8",
    )
    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded.base_url == "http://x:1"
