#!/usr/bin/env python3
"""Fail closed on forbidden import directions among shipped modules.

Intended layers (must stay aligned with docs/architecture.md):

  domain core:     lookup, normalize, models
  domain+LLM:      sentence  (may use telegram_html; must not import bot/channel)
  shared policy:   translation_policy, runtime_keys, constants
  presentation:    bot, channel, telegram_html, telegram_text
  local state:     settings, channel_reply_index, channel_reply_schema,
                   channel_runtime, logging_utils
  storage:         db  (lazy build_pinyin only inside write helpers)
  builder:         builder, data_source, build_pinyin
  bootstrap:       cli  (may wire runtime and builder entrypoints)

Rules enforced here (all against real src/wuwaterm/*.py AST imports):

1. Domain core and pure helpers must not import presentation or Telegram SDK.
2. sentence must not import bot or channel.
3. Presentation must not import builder-path modules (builder, data_source,
   build_pinyin).
4. channel must not runtime-import bot (TYPE_CHECKING-only is allowed).
5. build_pinyin may only be imported from db (lazy write path) or builder;
   never from bot, channel, lookup, sentence, settings, etc.
6. Builder modules must not import bot or channel.

Companion gates (not duplicated here):
- scripts/check_non_goals.py — delivery-mode and channel-listener product pins
- tests/test_runtime_imports.py — runtime import without pypinyin
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "wuwaterm"

DOMAIN_CORE = frozenset({"lookup", "normalize", "models"})
DOMAIN_LLM = frozenset({"sentence"})
SHARED = frozenset({"translation_policy", "runtime_keys", "constants"})
PRESENTATION = frozenset({"bot", "channel", "telegram_html", "telegram_text"})
LOCAL_STATE = frozenset(
    {
        "settings",
        "channel_reply_index",
        "channel_reply_schema",
        "channel_runtime",
        "logging_utils",
    }
)
STORAGE = frozenset({"db"})
BUILDER = frozenset({"builder", "data_source", "build_pinyin"})
BOOTSTRAP = frozenset({"cli"})

# Modules that must never depend on Telegram presentation or the Bot SDK.
NO_TELEGRAM_PRESENTATION = DOMAIN_CORE | SHARED | LOCAL_STATE | STORAGE | BUILDER

# Modules that must never pull builder-only graph into the bot edge.
NO_BUILDER_IMPORTS = PRESENTATION | DOMAIN_CORE | DOMAIN_LLM | SHARED | LOCAL_STATE

# Who may import build_pinyin at all (including lazy imports).
BUILD_PINYIN_ALLOWED_IMPORTERS = frozenset({"db", "builder", "build_pinyin"})

TELEGRAM_SDK_PREFIXES = (
    "telegram",
    "telegram.ext",
)


def _pkg_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == "__init__.py":
            continue
        modules[path.stem] = path
    return modules


def _iter_import_events(path: Path) -> list[tuple[str, bool, int]]:
    """Return (imported_name, type_checking_only, lineno) for local + external.

    `imported_name` is either a bare wuwaterm module stem (e.g. ``bot``) or a
    dotted external module (e.g. ``telegram.ext``).
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    type_checking_nodes: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            if _is_type_checking_test(node.test):
                for child in ast.walk(node):
                    type_checking_nodes.add(child)

    events: list[tuple[str, bool, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and module:
                # from .foo import bar  or  from . import foo
                top = module.split(".", 1)[0]
                events.append((top, node in type_checking_nodes, node.lineno))
            elif node.level and not module:
                for entry in node.names:
                    events.append(
                        (entry.name.split(".", 1)[0], node in type_checking_nodes, node.lineno)
                    )
            elif module.startswith("wuwaterm."):
                rest = module[len("wuwaterm.") :].split(".", 1)[0]
                events.append((rest, node in type_checking_nodes, node.lineno))
            elif module == "wuwaterm":
                for entry in node.names:
                    events.append(
                        (entry.name.split(".", 1)[0], node in type_checking_nodes, node.lineno)
                    )
            else:
                events.append((module, node in type_checking_nodes, node.lineno))
        elif isinstance(node, ast.Import):
            for entry in node.names:
                name = entry.name
                events.append((name, node in type_checking_nodes, node.lineno))
    return events


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _is_telegram_sdk(name: str) -> bool:
    return name == "telegram" or name.startswith("telegram.")


def check() -> list[str]:
    modules = _pkg_modules()
    failures: list[str] = []

    for name, path in modules.items():
        rel = path.relative_to(ROOT).as_posix()
        events = _iter_import_events(path)
        local_runtime = {
            imported
            for imported, type_only, _ in events
            if imported in modules and not type_only
        }
        local_all = {
            imported for imported, _type_only, _ in events if imported in modules
        }
        external_runtime = {
            imported
            for imported, type_only, _ in events
            if imported not in modules and not type_only
        }

        if name in NO_TELEGRAM_PRESENTATION:
            bad_pres = sorted(local_runtime & PRESENTATION)
            if bad_pres:
                failures.append(
                    f"{rel}: domain/infra/builder module must not import "
                    f"presentation {bad_pres}"
                )
            sdk = sorted(n for n in external_runtime if _is_telegram_sdk(n))
            if sdk:
                failures.append(
                    f"{rel}: must not import Telegram SDK {sdk}"
                )

        if name in DOMAIN_LLM:
            bad = sorted(local_runtime & {"bot", "channel"})
            if bad:
                failures.append(
                    f"{rel}: sentence must not import presentation cores {bad}"
                )

        if name in NO_BUILDER_IMPORTS:
            bad_builder = sorted(local_all & BUILDER)
            # db is storage, not in NO_BUILDER_IMPORTS; presentation/domain must
            # not import any builder module even under TYPE_CHECKING.
            if bad_builder:
                failures.append(
                    f"{rel}: must not import builder-path modules {bad_builder}"
                )

        if name == "channel":
            # Runtime import of bot is forbidden; TYPE_CHECKING is the documented
            # exception for BotConfig annotations.
            if "bot" in local_runtime:
                failures.append(
                    f"{rel}: must not runtime-import bot "
                    f"(TYPE_CHECKING-only is allowed)"
                )

        if name in BUILDER:
            bad = sorted(local_runtime & {"bot", "channel"})
            if bad:
                failures.append(
                    f"{rel}: builder module must not import presentation {bad}"
                )

        if "build_pinyin" in local_all and name not in BUILD_PINYIN_ALLOWED_IMPORTERS:
            failures.append(
                f"{rel}: only db/builder may import build_pinyin "
                f"(found import of build_pinyin)"
            )

    # Explicit: presentation files must never mention build_pinyin import.
    for pres in sorted(PRESENTATION):
        if pres not in modules:
            continue
        path = modules[pres]
        for imported, _type_only, lineno in _iter_import_events(path):
            if imported == "build_pinyin":
                failures.append(
                    f"{path.relative_to(ROOT).as_posix()}:{lineno}: "
                    f"presentation must not import build_pinyin"
                )

    return failures


def main() -> int:
    if not PACKAGE.is_dir():
        print(f"missing package directory: {PACKAGE}", file=sys.stderr)
        return 2
    failures = check()
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("architecture boundary guard ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
