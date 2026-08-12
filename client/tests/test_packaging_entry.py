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
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLIENT_ROOT = Path(__file__).resolve().parents[1]
ENTRY = CLIENT_ROOT / "main.py"
SPEC = CLIENT_ROOT / "WuwaTerm.spec"

# What a packaged launch does: start an interpreter, build a QApplication and
# an event loop from nothing, construct the window, exit. Run as a child so it
# is the same shape as the real thing - see the test below for why that also
# removes an ordering constraint the whole suite used to carry.
_SELF_CHECK_PROGRAM = """
from wuwaterm_client.app import SELF_CHECK_FLAG, run

raise SystemExit(run(["WuwaTerm", SELF_CHECK_FLAG]))
"""


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
    """The start-up rehearsal, run in a CHILD interpreter.

    A packaged launch is a fresh process that builds its OWN QApplication and
    its own event loop, and that construction is the thing this test exists to
    prove. In-process it could not keep proving it: `app.run` calls
    `QApplication(...)` unconditionally, libshiboken refuses a second instance
    while one is alive, and the other Qt test files here keep a session-scoped
    one. So this test used to pass only when it ran BEFORE all of them - an
    invisible constraint that every future Qt test file had to satisfy by
    being named to sort later, and one that really did bite: a file added
    during the config-persistence work failed immediately and had to be
    renamed to dodge it.

    The two ways out were to borrow the session instance or to move the probe
    to a subprocess. Borrowing would have removed the constraint by deleting
    the assertion's content - a `run` handed a QApplication someone else built
    is no longer the packaged start-up path. A child process keeps the
    content and removes the constraint, because there is nothing in it to
    collide with. Ordering is now proven, not assumed:
    `test_the_suite_does_not_depend_on_this_file_running_first` below.
    """
    pytest.importorskip("PySide6")
    pytest.importorskip("qasync")

    environment = dict(os.environ)
    # A child does not inherit pytest's `pythonpath` setting, and an editable
    # install is not something a test should assume about the machine.
    environment["PYTHONPATH"] = str(CLIENT_ROOT / "src")
    # conftest.py pins this for the parent; the child needs it in its own
    # environment, and this is the same headless platform build.ps1 uses
    # against the packaged artifact.
    environment["QT_QPA_PLATFORM"] = "offscreen"

    completed = subprocess.run(
        [sys.executable, "-c", _SELF_CHECK_PROGRAM],
        capture_output=True,
        text=True,
        env=environment,
        timeout=300,
    )

    assert completed.returncode == 0, completed.stderr


def test_the_suite_does_not_depend_on_this_file_running_first() -> None:
    """The ordering constraint is gone, and this is what keeps it gone.

    Nothing in this module may construct a QApplication in the test process.
    While it did, the suite ran correctly only in filename order, and the
    failure it produced named libshiboken rather than the ordering - which is
    why it cost a rename to diagnose rather than a read.

    Checked by parsing this file rather than by importing Qt: the property is
    "this module does not build one", and an assertion that needed a live
    QApplication to check it would be the same trap one level down.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    constructed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "QApplication"
    ]
    assert not constructed, "this module must not build a QApplication in-process"
    # The probe still has to actually run the entry point, or the test above
    # could be satisfied by doing nothing at all.
    assert "run([\"WuwaTerm\", SELF_CHECK_FLAG])" in _SELF_CHECK_PROGRAM
