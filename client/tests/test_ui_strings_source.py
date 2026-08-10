"""Static proof that every displayed literal in ui/ is sourced from
strings.py.

This test does not import Qt or need a display/event loop: it parses the
ui/ package sources with ast and flags any string literal (including
f-strings with literal text) passed as an argument to a known text-setting
call that is not sourced from the strings module.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLIENT_SRC = Path(__file__).resolve().parents[1] / "src" / "wuwaterm_client"
UI_DIR = CLIENT_SRC / "ui"
STRINGS_PATH = CLIENT_SRC / "strings.py"

TEXT_SETTER_NAMES = {
    "setText",
    "setWindowTitle",
    "setPlaceholderText",
    "setToolTip",
    "setStatusTip",
    "setWhatsThis",
    "addItem",
    "addItems",
    "setItemText",
    "setTitle",
    "setLabelText",
    "information",
    "warning",
    "critical",
    "question",
    "setHeaderLabel",
    "setHeaderLabels",
    "setHorizontalHeaderLabels",
    "setTabText",
    "addTab",
}


def _has_literal_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip() != ""
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and part.value.strip() != ""
            for part in node.values
        )
    return False


def _violations_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        if name not in TEXT_SETTER_NAMES:
            continue
        for arg in (*node.args, *(kw.value for kw in node.keywords)):
            if _has_literal_text(arg):
                violations.append(
                    f"{path.relative_to(CLIENT_SRC)}:{arg.lineno}: literal display "
                    f"text passed to {name}(); route it through strings.py instead"
                )
    return violations


def test_strings_module_defines_a_substantial_set_of_constants() -> None:
    assert STRINGS_PATH.is_file(), f"expected strings module at {STRINGS_PATH}"
    tree = ast.parse(STRINGS_PATH.read_text(encoding="utf-8"), filename=str(STRINGS_PATH))
    string_constants = [
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    assert len(string_constants) >= 20, "strings.py should hold the UI's display text"


def test_ui_widgets_source_all_display_text_from_strings_module() -> None:
    assert UI_DIR.is_dir(), f"expected ui package at {UI_DIR}"
    all_violations: list[str] = []
    for path in sorted(UI_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        all_violations.extend(_violations_in_file(path))
    assert not all_violations, "\n" + "\n".join(all_violations)
