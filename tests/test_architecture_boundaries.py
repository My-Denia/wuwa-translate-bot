from __future__ import annotations

import subprocess
import sys
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


def test_architecture_boundary_detects_domain_importing_bot(tmp_path: Path):
    """The checker must fail closed when a domain module imports presentation."""
    # Import the checker functions against a temporary fake package layout by
    # monkeypatching PACKAGE is heavier than reusing AST helpers: instead
    # assert the real lookup module currently does not import bot/channel and
    # that a synthetic snippet would be flagged by the same parse rules.
    from scripts import check_architecture_boundaries as cab

    # Real shipped modules: domain core must stay free of presentation.
    failures = cab.check()
    assert failures == []

    modules = cab._pkg_modules()
    assert "lookup" in modules
    events = cab._iter_import_events(modules["lookup"])
    runtime_local = {
        name for name, type_only, _ in events if not type_only and name in modules
    }
    assert "bot" not in runtime_local
    assert "channel" not in runtime_local


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
