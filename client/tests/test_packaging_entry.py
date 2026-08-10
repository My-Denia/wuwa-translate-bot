"""The packaged build must be able to start.

`pyinstaller` runs its entry script as a top-level module called `__main__`
with no package context. An entry that reaches its siblings through relative
imports therefore raises `ImportError: attempted relative import with no known
parent package` in the frozen program, while every source-tree test keeps
passing. These tests pin the two halves of the fix: the spec points at an
absolute-import entry, and that entry starts up without a display.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CLIENT_ROOT = Path(__file__).resolve().parents[1]
ENTRY = CLIENT_ROOT / "main.py"
SPEC = CLIENT_ROOT / "WuwaTerm.spec"


def test_the_spec_entry_is_the_absolute_import_launcher() -> None:
    spec = SPEC.read_text(encoding="utf-8")

    assert 'ENTRY_POINT = CLIENT_ROOT / "main.py"' in spec
    # The package's own __main__ is for `python -m wuwaterm_client` and must
    # not be the frozen entry point.
    assert '"wuwaterm_client" / "__main__.py"' not in spec


def test_the_packaging_entry_uses_no_relative_imports() -> None:
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))

    relative = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level
    ]
    assert not relative, [node.module for node in relative]


def test_the_package_main_module_still_works_for_dash_m() -> None:
    """The other half of the contract: `python -m wuwaterm_client` needs the
    relative import that the frozen entry may not use."""
    module = CLIENT_ROOT / "src" / "wuwaterm_client" / "__main__.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))

    assert any(
        isinstance(node, ast.ImportFrom) and node.level for node in ast.walk(tree)
    )


def test_self_check_starts_up_and_exits_without_a_window() -> None:
    pytest.importorskip("PySide6")
    pytest.importorskip("qasync")
    from wuwaterm_client.app import SELF_CHECK_FLAG, run

    # conftest.py pins QT_QPA_PLATFORM=offscreen, so this is the same code
    # path build.ps1 runs against the packaged artifact.
    assert run(["WuwaTerm", SELF_CHECK_FLAG]) == 0
