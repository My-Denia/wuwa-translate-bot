from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_architecture_boundaries.py"


def test_architecture_boundary_script_passes_on_shipped_tree():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "architecture boundary guard ok" in result.stdout


def test_shipped_modules_are_fully_classified():
    from scripts import check_architecture_boundaries as cab

    modules = set(cab._pkg_modules())
    assert modules <= cab.ALL_CLASSIFIED
    assert modules  # cardinality floor: package is non-empty


def test_absolute_package_import_normalizes_to_local_stem():
    from scripts import check_architecture_boundaries as cab

    assert cab._normalize_imported_name("wuwaterm.bot") == "bot"
    assert cab._normalize_imported_name("wuwaterm.build_pinyin") == "build_pinyin"
    assert cab._normalize_imported_name("telegram.ext") == "telegram.ext"


def test_absolute_import_wuwaterm_bot_is_detected_as_local_bot(tmp_path: Path):
    """Fail-closed: plain ``import wuwaterm.bot`` must not evade stem rules."""
    from scripts import check_architecture_boundaries as cab

    snippet = tmp_path / "snippet.py"
    snippet.write_text("import wuwaterm.bot\n", encoding="utf-8")
    events = cab._iter_import_events(snippet)
    names = {name for name, _type_only, _lineno in events}
    assert "bot" in names
    assert "wuwaterm.bot" not in names


def test_type_checking_else_import_is_runtime():
    from scripts import check_architecture_boundaries as cab

    source = textwrap.dedent(
        """
        from typing import TYPE_CHECKING
        if TYPE_CHECKING:
            from .models import TermEntry
        else:
            from .bot import BotConfig
        """
    )
    path = Path(cab.PACKAGE / "_probe_type_checking_else.py")
    # Parse via a temp path outside package so we do not require a real file
    # under src/; write to a disposable file under the test process cwd.
    probe = ROOT / ".architecture_boundary_probe.py"
    try:
        probe.write_text(source, encoding="utf-8")
        events = cab._iter_import_events(probe)
        by_name = {name: type_only for name, type_only, _ in events}
        assert by_name.get("models") is True
        assert by_name.get("bot") is False
    finally:
        if probe.exists():
            probe.unlink()


def test_channel_bot_import_is_type_checking_only():
    from scripts import check_architecture_boundaries as cab

    modules = cab._pkg_modules()
    events = cab._iter_import_events(modules["channel"])
    bot_events = [(type_only, lineno) for name, type_only, lineno in events if name == "bot"]
    assert bot_events, "expected channel to TYPE_CHECKING-import BotConfig from bot"
    assert all(type_only for type_only, _ in bot_events)


def test_build_pinyin_only_from_allowed_importers():
    from scripts import check_architecture_boundaries as cab

    modules = cab._pkg_modules()
    for name, path in modules.items():
        events = cab._iter_import_events(path)
        if any(imported == "build_pinyin" for imported, *_ in events):
            assert name in cab.BUILD_PINYIN_ALLOWED_IMPORTERS, name


def test_presentation_does_not_import_builder_modules():
    from scripts import check_architecture_boundaries as cab

    modules = cab._pkg_modules()
    for pres in ("bot", "channel"):
        events = cab._iter_import_events(modules[pres])
        imported = {name for name, *_ in events if name in modules}
        assert not imported & cab.BUILDER, (pres, imported & cab.BUILDER)


def test_domain_lookup_has_no_presentation_runtime_imports():
    from scripts import check_architecture_boundaries as cab

    modules = cab._pkg_modules()
    events = cab._iter_import_events(modules["lookup"])
    runtime_local = {
        name for name, type_only, _ in events if not type_only and name in modules
    }
    assert "bot" not in runtime_local
    assert "channel" not in runtime_local


def test_check_reports_unclassified_modules(monkeypatch):
    from scripts import check_architecture_boundaries as cab

    real_modules = cab._pkg_modules()
    # Inject a fake unclassified stem without writing into src/.
    fake = dict(real_modules)
    fake["rogue_helper"] = ROOT / "src" / "wuwaterm" / "lookup.py"
    monkeypatch.setattr(cab, "_pkg_modules", lambda: fake)
    failures = cab.check()
    assert any("unclassified modules" in f and "rogue_helper" in f for f in failures)
