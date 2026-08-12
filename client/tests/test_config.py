"""Tests for wuwaterm_client.config."""

from __future__ import annotations

import json
from pathlib import Path

from wuwaterm_client.config import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_TRANSLATE_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    ClientConfig,
    config_path,
    usable_base_url,
)


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
        base_url="https://example.local:8787", request_timeout_seconds=12.0
    )
    original.save(base_dir=tmp_path)

    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded == original


def test_config_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded == ClientConfig()
    # The timeouts are the client's own business; the address is not.
    assert loaded.base_url is None


def test_config_load_malformed_file_returns_defaults(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all {{{", encoding="utf-8")
    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded == ClientConfig()
    assert loaded.base_url is None


def test_config_load_ignores_unrecognized_keys(tmp_path: Path) -> None:
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"base_url": "http://127.0.0.1:9001", "some_future_field": "ignored"}
        ),
        encoding="utf-8",
    )
    loaded = ClientConfig.load(base_dir=tmp_path)
    assert loaded.base_url == "http://127.0.0.1:9001"


def test_a_hand_edited_timeout_is_clamped_rather_than_trusted(tmp_path) -> None:
    """`load` never raises, which must not mean it never checks.

    The Settings dialog cannot produce these values; a hand-edited config file
    can, and a zero or negative timeout would go straight into the HTTP
    client.
    """
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "base_url": "http://127.0.0.1:8787",
                "request_timeout_seconds": 0,
                "translate_timeout_seconds": 100000,
            }
        ),
        encoding="utf-8",
    )

    config = ClientConfig.load(tmp_path)

    assert config.request_timeout_seconds == MIN_TIMEOUT_SECONDS
    assert config.translate_timeout_seconds == MAX_TIMEOUT_SECONDS


def test_a_non_numeric_timeout_falls_back_to_the_default(tmp_path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"request_timeout_seconds": "soon"}), encoding="utf-8"
    )

    config = ClientConfig.load(tmp_path)

    assert config.request_timeout_seconds == DEFAULT_REQUEST_TIMEOUT_SECONDS


def test_a_non_finite_timeout_falls_back_to_the_default(tmp_path) -> None:
    """Python's JSON parser accepts NaN, and min/max pass it through."""
    (tmp_path / "config.json").write_text(
        '{"request_timeout_seconds": NaN, "translate_timeout_seconds": Infinity}',
        encoding="utf-8",
    )

    config = ClientConfig.load(tmp_path)

    assert config.request_timeout_seconds == DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert config.translate_timeout_seconds == DEFAULT_TRANSLATE_TIMEOUT_SECONDS


def test_a_base_url_of_the_wrong_type_leaves_the_client_unconfigured(tmp_path) -> None:
    """Annotations are not runtime validation: a list would reach httpx."""
    (tmp_path / "config.json").write_text(
        json.dumps({"base_url": ["http://127.0.0.1:8787"]}), encoding="utf-8"
    )

    assert ClientConfig.load(tmp_path).base_url is None


def test_an_address_that_cannot_be_used_is_recognized() -> None:
    """The port is the case a text field will not catch on its own."""
    assert usable_base_url("http://127.0.0.1:8787")
    assert usable_base_url("https://example.invalid/api")
    assert not usable_base_url("http://127.0.0.1:notaport")
    assert not usable_base_url("127.0.0.1:8787")
    assert not usable_base_url("ftp://127.0.0.1")
    assert not usable_base_url("http://")
    assert not usable_base_url("")
    assert not usable_base_url(None)


def test_an_unusable_saved_address_leaves_the_client_unconfigured(tmp_path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"base_url": "http://127.0.0.1:notaport"}), encoding="utf-8"
    )

    assert ClientConfig.load(tmp_path).base_url is None


def test_plain_http_is_only_accepted_for_this_machine() -> None:
    """A remote http:// address carries the bearer credential in the clear."""
    assert usable_base_url("http://127.0.0.1:8787")
    assert usable_base_url("http://localhost:8787")
    assert usable_base_url("http://[::1]:8787")
    assert not usable_base_url("http://192.0.2.10:8787")
    assert not usable_base_url("http://api.example.invalid")
    # TLS anywhere is fine.
    assert usable_base_url("https://api.example.invalid")


def test_a_base_address_may_not_carry_a_query_or_fragment() -> None:
    """Joining a request path onto it drops them, so the setting would lie."""
    assert usable_base_url("https://api.example.invalid/prefix")
    assert not usable_base_url("https://api.example.invalid/?x=1")
    assert not usable_base_url("https://api.example.invalid/#part")


def test_an_address_may_not_carry_credentials() -> None:
    """The device token lives in the credential store, not in a JSON file."""
    assert not usable_base_url("https://user:pass@api.example.invalid")
    assert not usable_base_url("https://user@api.example.invalid")


def test_an_address_the_client_itself_cannot_parse_is_refused() -> None:
    """urlsplit drops an embedded control character; httpx refuses it.

    Saving one produced an address that prevented the application from
    starting until the file was repaired by hand.
    """
    assert not usable_base_url("https://\texample.invalid")
    assert not usable_base_url("https://exa\nmple.invalid")
