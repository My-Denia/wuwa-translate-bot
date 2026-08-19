"""Static proof that every displayed literal in ui/ is sourced from
strings.py.

This test does not import Qt or need a display/event loop: it parses the
ui/ package sources with ast and applies two rules.

1. No string literal (including an f-string with literal text) may be passed
   to a known text-setting call. This is the original rule, and it is kept:
   it catches an English literal at a setter, which rule 2 cannot see.
2. No string literal anywhere in the ui package may contain a CJK character -
   Han, Hiragana, Katakana, Hangul, CJK punctuation or a fullwidth form.
   Display text is Chinese in this application, so this is the rule that
   closes the path rule 1 leaves open: a literal assigned to a local first,
   folded into an f-string, or handed to a setter nobody thought to whitelist
   (setAccessibleName, addAction, setItemData) is invisible to rule 1 and
   caught here (issue #65).

Both rules read the package RECURSIVELY, and the only file the setter rule
skips is the ui package's OWN __init__.py, identified by position rather than
by name. Neither was true in the first version of this file, and each is held
open by a test that fails without it.

The block list of what counts as a CJK character was short three times in a
row - the supplementary Han extensions, then extension I by one code point,
then the supplementary Kana blocks - each time while the prose above already
claimed the missing one. Hand-maintaining a list against a promise is the
defect rather than any one omission, so the list is now checked against the
character database the interpreter ships with, and the test that does it
sweeps the whole code space rather than sampling it.

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
import unicodedata
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
# an explicit block list rather than a live unicodedata lookup, so that reading
# the test tells you exactly what it forbids, and written as code points rather
# than as the characters themselves so the supplementary-plane rows are
# readable at all.
#
# Two review rounds found this list one block short - first the supplementary
# Han extensions, then extension I by one code point, then the supplementary
# Kana blocks. Hand-maintaining a list against a promise stated in prose is the
# defect, not any one omission, so the list is now checked against Unicode
# itself by test_the_range_list_covers_every_character_unicode_calls_cjk.
CJK_RANGES = (
    (0x1100, 0x11FF),    # Hangul Jamo
    (0x2E80, 0x2EFF),    # CJK radicals supplement
    (0x2F00, 0x2FDF),    # Kangxi radicals
    (0x2FF0, 0x2FFF),    # Ideographic description characters
    (0x3000, 0x303F),    # CJK symbols and punctuation, incl. the ideographic comma
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0x3100, 0x312F),    # Bopomofo
    (0x3130, 0x318F),    # Hangul compatibility Jamo
    (0x3190, 0x319F),    # Kanbun
    (0x31A0, 0x31BF),    # Bopomofo extended
    (0x31C0, 0x31EF),    # CJK strokes
    (0x31F0, 0x31FF),    # Katakana phonetic extensions
    (0x3200, 0x32FF),    # Enclosed CJK letters and months
    (0x3300, 0x33FF),    # CJK compatibility
    (0x3400, 0x4DBF),    # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),    # CJK unified ideographs
    (0xA960, 0xA97F),    # Hangul Jamo extended-A
    (0xAC00, 0xD7AF),    # Hangul syllables
    (0xD7B0, 0xD7FF),    # Hangul Jamo extended-B
    (0xF900, 0xFAFF),    # CJK compatibility ideographs
    (0xFE10, 0xFE1F),    # Vertical forms
    (0xFE30, 0xFE4F),    # CJK compatibility forms
    (0xFE50, 0xFE6F),    # Small form variants, incl. the small ideographic comma
    (0xFF00, 0xFFEF),    # Halfwidth and fullwidth forms
    # Supplementary planes. A Han or Kana character above the BMP is still one.
    (0x1AFF0, 0x1AFFF),  # Kana extended-B
    (0x1B000, 0x1B0FF),  # Kana supplement
    (0x1B100, 0x1B12F),  # Kana extended-A
    (0x1B130, 0x1B16F),  # Small Kana extension
    (0x1D360, 0x1D37F),  # Counting rod numerals, incl. the ideographic tally marks
    (0x1F200, 0x1F2FF),  # Enclosed ideographic supplement
    (0x20000, 0x2A6DF),  # CJK unified ideographs extension B
    # Extensions C through F run to U+2EBEF; extension I starts one code point
    # later, at U+2EBF0. An earlier version of this row stopped at U+2EBEF while
    # its own comment claimed extension I - a label wider than the check under
    # it, which is the shape of mistake this file exists to catch.
    (0x2A700, 0x2EE5F),  # CJK unified ideographs extensions C through F and I
    (0x2F800, 0x2FA1F),  # CJK compatibility ideographs supplement
    (0x30000, 0x323AF),  # CJK unified ideographs extensions G and H
)

# The words in a Unicode character NAME that make it this rule's business. Used
# only by the completeness test below, never by the rule itself: the rule reads
# the block list above, which is deliberately WIDER than this (CJK punctuation
# such as U+3008 LEFT ANGLE BRACKET carries none of these words and is still
# display text here). So the test asserts a superset, not an equality.
CJK_NAME_TOKENS = (
    "CJK",
    "HIRAGANA",
    "KATAKANA",
    "HANGUL",
    "IDEOGRAPH",
    "KANGXI",
    "BOPOMOFO",
)

CJK_CHARACTER = re.compile(
    "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in CJK_RANGES) + "]"
)


def _python_sources(root: Path) -> list[Path]:
    """Every python file under ``root``, at any depth.

    Recursive on purpose. A flat glob stops at the top of the package, so a
    widget moved into a subpackage - ``ui/dialogs/foo.py`` - would leave both
    rules below with nothing to read, and inline text there would pass CI.
    """
    return sorted(root.rglob("*.py"))


def _ui_source_files() -> list[Path]:
    return _python_sources(UI_DIR)


def _files_for_setter_rule(files: list[Path]) -> list[Path]:
    """``files`` minus the one file the setter rule is allowed to skip.

    That file is the ui package's OWN ``__init__.py``, which is re-exports
    rather than widget code. It has to be identified by position, not by name:
    once enumeration became recursive a name test would also have dropped every
    ``ui/<subpackage>/__init__.py``, and a subpackage initializer is ordinary
    widget code that can call a setter with a literal. Written as a filter over
    a given list rather than as a check inside the loop so the exemption itself
    is testable on paths that do not have to exist.
    """
    root_init = UI_DIR / "__init__.py"
    return [path for path in files if path != root_init]


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
    for path in _files_for_setter_rule(_ui_source_files()):
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


def test_cjk_rule_reaches_above_the_basic_multilingual_plane(tmp_path: Path) -> None:
    """A Han character outside the BMP is still a Han character.

    The first version of the range list stopped at U+9FFF and friends, so a
    literal built from an extension-B ideograph passed the gate untouched, and
    two later versions fell short of extension I and of the supplementary Kana
    blocks. One literal below stands for each of those three misses: each was
    flagged only after the block it belongs to was added, and the ASCII line
    beside them must stay unflagged so a widened range cannot pass by flagging
    everything.
    """
    source = (
        "def build():\n"
        "    a = \"\\U00020BB7\"\n"          # extension B
        "    b = \"\\U0002EBF0\"\n"          # extension I, one code point past C-F
        "    c = \"\\U0002F81A\"\n"          # compatibility ideographs supplement
        "    d = \"\\U0001B000\"\n"          # kana supplement, archaic katakana E
        "    e = \"plain ascii, not display text\"\n"
        "    return a, b, c, d, e\n"
    )
    planted = tmp_path / "supplementary.py"
    planted.write_text(source, encoding="utf-8")

    violations = _cjk_literal_violations_in_file(planted)
    locations = [message.split(": ", 1)[0] for message in violations]
    assert locations == [
        "supplementary.py:2",
        "supplementary.py:3",
        "supplementary.py:4",
        "supplementary.py:5",
    ], violations


def test_source_enumeration_reaches_into_subpackages(tmp_path: Path) -> None:
    """A widget in ui/<subpackage>/ has to be read like any other.

    A flat glob returns nothing from a subdirectory, which would let both rules
    pass on a file they never opened. The nested module below is the case, and
    the assertion is on the returned paths rather than on a count, so an empty
    result cannot look like success.
    """
    root = tmp_path / "ui"
    (root / "dialogs").mkdir(parents=True)
    top = root / "top.py"
    nested = root / "dialogs" / "nested.py"
    top.write_text("x = 1\n", encoding="utf-8")
    nested.write_text("y = 2\n", encoding="utf-8")

    found = _python_sources(root)
    assert found == [nested, top], found

    # And the rule actually runs on the nested file.
    nested.write_text('label = "\u5237\u65b0"\n', encoding="utf-8")
    violations = _cjk_literal_violations_in_file(nested)
    assert len(violations) == 1, violations
    assert violations[0].startswith("nested.py:1:"), violations[0]


def test_the_real_ui_package_is_enumerated_recursively() -> None:
    """The rules read the actual package, not an empty list.

    Guards the shape of the two tests above against a refactor that silently
    points them at nothing: the live enumeration must return real files, and
    every one of them must live under the ui package.
    """
    files = _ui_source_files()
    assert len(files) >= 5, files
    assert all(UI_DIR in path.parents for path in files), files


def test_only_the_root_package_initializer_is_exempt_from_the_setter_rule() -> None:
    """A subpackage initializer is widget code, and is not skipped.

    The setter rule has always skipped ui/__init__.py, which is re-exports.
    Once enumeration became recursive a name-only test would have skipped
    ui/<subpackage>/__init__.py too, and the CJK rule cannot cover for it: an
    ENGLISH literal at a setter is invisible to that rule by construction.
    """
    root_init = UI_DIR / "__init__.py"
    nested_init = UI_DIR / "dialogs" / "__init__.py"
    nested_widget = UI_DIR / "dialogs" / "foo.py"
    top_widget = UI_DIR / "main_window.py"

    kept = _files_for_setter_rule([root_init, nested_init, nested_widget, top_widget])
    assert kept == [nested_init, nested_widget, top_widget], kept

    # And the live list really does go through that filter, minus exactly one.
    live = _ui_source_files()
    assert root_init in live, live
    assert _files_for_setter_rule(live) == [p for p in live if p != root_init]


def test_the_setter_rule_flags_a_literal_in_a_subpackage_initializer(
    tmp_path: Path,
) -> None:
    """And the rule itself reports it, in English, where the CJK rule is blind."""
    nested_init = tmp_path / "dialogs" / "__init__.py"
    nested_init.parent.mkdir(parents=True)
    nested_init.write_text(
        "def build(widget):\n"
        '    widget.setText("Refresh")\n',
        encoding="utf-8",
    )
    setter = _setter_violations_in_file(nested_init)
    assert len(setter) == 1, setter
    assert setter[0].startswith("__init__.py:2:"), setter[0]
    # The CJK rule is silent on it, which is why the exemption had to narrow.
    assert _cjk_literal_violations_in_file(nested_init) == []


def test_the_range_list_covers_every_character_unicode_calls_cjk() -> None:
    """The block list must not fall behind the promise stated in prose.

    Three review rounds found this list one block short, each time for a
    different block, and each time the rule's own description already claimed
    the missing one. So the list is checked against the character database that
    ships with the interpreter: every code point whose Unicode NAME contains
    one of CJK_NAME_TOKENS must match. Superset, not equality - the list also
    covers CJK punctuation whose names carry none of those words.

    A sweep of the whole code space costs a fraction of a second, and it is the
    only form of this check that cannot be one block behind. It is deliberately
    tied to the interpreter's character database rather than to a number: the
    list is complete under the two databases it has been run against, and a
    future one that adds a CJK block should turn this red rather than let the
    list go quietly stale.
    """
    uncovered = [
        cp
        for cp in range(0x110000)
        if (name := _unicode_name(cp)) is not None
        and any(token in name for token in CJK_NAME_TOKENS)
        and not CJK_CHARACTER.match(chr(cp))
    ]
    assert not uncovered, (
        "the block list misses %d code point(s) Unicode names as CJK, "
        "starting at U+%05X %s"
        % (len(uncovered), uncovered[0], _unicode_name(uncovered[0]))
    )
    # And the sweep must actually have found CJK characters to check, so an
    # empty or broken name lookup cannot pass this test by finding nothing.
    covered = sum(
        1
        for cp in range(0x110000)
        if (name := _unicode_name(cp)) is not None
        and any(token in name for token in CJK_NAME_TOKENS)
    )
    assert covered > 90000, covered


def _unicode_name(code_point: int) -> str | None:
    try:
        return unicodedata.name(chr(code_point))
    except ValueError:
        return None
