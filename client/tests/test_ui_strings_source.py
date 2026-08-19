"""Static proof that every displayed literal in ui/ is sourced from
strings.py.

This test does not import Qt or need a display/event loop: it parses the
ui/ package sources with ast and applies two rules.

1. No string literal (including an f-string with literal text) may be passed
   to a known text-setting call. This is the original rule, and it is kept:
   it catches an English literal at a setter, which rule 2 cannot see.
2. No string literal anywhere in ui/*.py may contain a CJK character - Han,
   Hiragana, Katakana, Hangul, CJK punctuation or a fullwidth form. Display
   text is Chinese in this application, so this is the rule that closes the
   path rule 1 leaves open: a literal assigned to a local first, folded into
   an f-string, or handed to a setter nobody thought to whitelist
   (setAccessibleName, addAction, setItemData) is invisible to rule 1 and
   caught here (issue #65).

What rule 2 deliberately does not flag:

* comments - ast discards them, so they are out of scope by construction and
  not by a list of exceptions. Chinese commentary in ui/ is normal and stays.
* docstrings - these ARE string constants in the tree (the first statement of
  a module, class or function), so they have to be excluded explicitly, and
  they are: the module/class/function docstring node of every such body is
  collected first and skipped.

What neither rule proves: that what the user sees came from strings.py.
Text that Qt supplies (standard button labels, context menus) and text the
service returns are not literals in this package at all, and no static read
of ui/ can see them.
"""

from __future__ import annotations

import ast
import re
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

# The ranges that make a literal "display text" in this application. Kept as
# an explicit list rather than a unicodedata script lookup so that reading the
# test tells you exactly what it forbids.
CJK_RANGES = (
    ("ᄀ", "ᇿ"),  # Hangul Jamo
    ("　", "〿"),  # CJK symbols and punctuation, incl. the ideographic comma
    ("぀", "ゟ"),  # Hiragana
    ("゠", "ヿ"),  # Katakana
    ("㐀", "䶿"),  # CJK unified ideographs extension A
    ("一", "鿿"),  # CJK unified ideographs
    ("ꥠ", "꥿"),  # Hangul Jamo extended-A
    ("가", "힯"),  # Hangul syllables
    ("豈", "﫿"),  # CJK compatibility ideographs
    ("＀", "￯"),  # Halfwidth and fullwidth forms
)

CJK_CHARACTER = re.compile("[" + "".join(f"{lo}-{hi}" for lo, hi in CJK_RANGES) + "]")


def _ui_source_files() -> list[Path]:
    return sorted(UI_DIR.glob("*.py"))


def _display(path: Path) -> str:
    """How a file is named in a violation message.

    Inside the package a path relative to ``src/wuwaterm_client`` is what a
    reader can act on. The self-test below feeds this checker a file in a
    temporary directory, which has no such relation, so fall back to the bare
    name rather than raising while building an error message.
    """
    try:
        return str(path.relative_to(CLIENT_SRC))
    except ValueError:
        return path.name


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


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Identities of the Constant nodes that are docstrings, not values.

    A docstring is the first statement of a module, class or function body and
    is an ``Expr`` wrapping a string ``Constant``. Nothing else in the tree
    has that shape, and identity is used rather than position so that an
    identical literal elsewhere in the file is still flagged.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _setter_violations_in_file(path: Path) -> list[str]:
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
                    f"{_display(path)}:{arg.lineno}: literal display "
                    f"text passed to {name}(); route it through strings.py instead"
                )
    return violations


def _cjk_literal_violations_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_node_ids(tree)
    violations: list[str] = []
    seen: set[int] = set()

    def report(node: ast.Constant) -> None:
        if id(node) in docstrings or id(node) in seen:
            return
        if not isinstance(node.value, str):
            return
        found = CJK_CHARACTER.search(node.value)
        if found is None:
            return
        seen.add(id(node))
        violations.append(
            f"{_display(path)}:{node.lineno}: string literal contains "
            f"the CJK character {found.group()!r}; display text belongs in "
            f"strings.py - import the constant instead"
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            report(node)
        elif isinstance(node, ast.JoinedStr):
            # Walked anyway, but named here so the f-string case is a stated
            # part of the rule rather than an accident of ast.walk.
            for part in node.values:
                if isinstance(part, ast.Constant):
                    report(part)
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
    for path in _ui_source_files():
        if path.name == "__init__.py":
            continue
        all_violations.extend(_setter_violations_in_file(path))
    assert not all_violations, "\n" + "\n".join(all_violations)


def test_ui_sources_hold_no_cjk_string_literal() -> None:
    assert UI_DIR.is_dir(), f"expected ui package at {UI_DIR}"
    files = _ui_source_files()
    assert files, f"expected python sources under {UI_DIR}"
    all_violations: list[str] = []
    for path in files:
        all_violations.extend(_cjk_literal_violations_in_file(path))
    assert not all_violations, "\n" + "\n".join(all_violations)


def test_cjk_rule_flags_a_planted_literal_and_spares_comments_and_docstrings(
    tmp_path: Path,
) -> None:
    """The gate must be red on a broken tree, not merely green on this one.

    Three shapes are asserted together because the value of the rule is the
    difference between them: the literal is caught wherever it sits, and the
    two shapes that carry Chinese legitimately are not touched. Without the
    negative half, a checker that flagged every file would pass this test.
    """
    source = (
        '"""模块文档字符串, 不应触发。"""\n'
        "\n"
        "\n"
        "# 这是注释, 也不应触发。\n"
        "def build(widget):\n"
        '    """函数文档字符串, 同样不触发。"""\n'
        '    label = "刷新"\n'
        "    widget.setAccessibleName(label)\n"
        '    return f"共 {len(label)} 项"\n'
    )
    planted = tmp_path / "planted.py"
    planted.write_text(source, encoding="utf-8")

    tree = ast.parse(source)
    docstrings = _docstring_node_ids(tree)
    assert len(docstrings) == 2, "expected the module and function docstrings"

    violations = _cjk_literal_violations_in_file(planted)
    locations = [message.split(": ", 1)[0] for message in violations]
    # Line 7 is the local variable. Line 9 appears twice because an f-string
    # carries one literal part on each side of the placeholder, and each part
    # is its own Constant node - both are reported, and that is the shape a
    # reader should expect rather than a bug.
    assert locations == ["planted.py:7", "planted.py:9", "planted.py:9"], violations
    assert all("strings.py" in message for message in violations)

    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""模块文档字符串, 不应触发。"""\n'
        "\n"
        "\n"
        "# 这是注释, 也不应触发。\n"
        "def build(widget):\n"
        '    """函数文档字符串, 同样不触发。"""\n'
        "    widget.setAccessibleName(strings.STATUS_REFRESH_BUTTON)\n",
        encoding="utf-8",
    )
    assert _cjk_literal_violations_in_file(clean) == []
