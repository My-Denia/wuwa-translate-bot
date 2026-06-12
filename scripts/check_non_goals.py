from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".sh", ".example", ".txt"}
FORBIDDEN = {
    "webhook": re.compile(r"\b(set_webhook|run_webhook|WEBHOOK_URL|webhook_url)\b"),
    "inline mode": re.compile(r"\bInlineQueryHandler\b"),
    "alias layer": re.compile(r"\balias(?:es)?\b", re.IGNORECASE),
    "free-text listener": re.compile(r"\bMessageHandler\b"),
}
ALLOWED = {
    "webhook": {
        "scripts/check_non_goals.py",
        "tests/test_non_goals.py",
        "README.md",
        "goal-runs/wuwa-vps-group-hardening/plan.md",
    },
    "inline mode": {
        "scripts/check_non_goals.py",
        "tests/test_non_goals.py",
        "README.md",
        "goal-runs/wuwa-vps-group-hardening/plan.md",
    },
    "alias layer": {
        "scripts/check_non_goals.py",
        "tests/test_non_goals.py",
        "AGENTS.md",
        "README.md",
        "goal-runs/wuwa-vps-group-hardening/plan.md",
        "goal-runs/wuwa-vps-group-hardening/execution-log.md",
    },
    "free-text listener": {
        "scripts/check_non_goals.py",
        "tests/test_non_goals.py",
        "goal-runs/wuwa-vps-group-hardening/plan.md",
    },
}


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "data/", "goal-runs/")):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {"Dockerfile"}:
            files.append(path)
    return files


def main() -> int:
    failures: list[str] = []
    for path in iter_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in FORBIDDEN.items():
            if rel in ALLOWED[label]:
                continue
            if pattern.search(text):
                failures.append(f"{label} marker found in {rel}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("non-goal guard ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
