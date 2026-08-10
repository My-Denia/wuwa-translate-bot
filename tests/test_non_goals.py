from __future__ import annotations

from pathlib import Path


def test_no_webhook_inline_alias_or_messagehandler_in_runtime_code():
    root = Path(__file__).resolve().parents[1]
    runtime_files = list((root / "src" / "wuwaterm").glob("*.py"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    assert "run_webhook" not in text
    assert "set_webhook" not in text
    assert "InlineQueryHandler" not in text
    assert "alias" not in text.casefold()


def test_scanner_skips_nested_virtual_environments(tmp_path, monkeypatch):
    """Third-party code in a nested venv must not fail the product gate."""
    from scripts import check_non_goals as gate

    root = tmp_path / "repo"
    (root / "client" / ".venv" / "Lib" / "site-packages").mkdir(parents=True)
    (root / "client" / "src").mkdir(parents=True)
    vendored = root / "client" / ".venv" / "Lib" / "site-packages" / "vendor.py"
    vendored.write_text("TypeAlias = 1\n", encoding="utf-8")
    (root / "client" / "src" / "ours.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", root)

    scanned = {path.relative_to(root).as_posix() for path in gate.iter_files()}

    assert "client/src/ours.py" in scanned
    assert not any(".venv" in item for item in scanned)
