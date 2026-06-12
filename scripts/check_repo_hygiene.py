from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_GAME_TEXT_BYTES = 1_000_000
BULK_GAME_PATH_MARKERS = (
    "TextMap/",
    "Textmaps/",
    "ConfigDB/",
    "BinData/",
)


def candidate_files() -> list[Path]:
    files: list[Path] = []
    for args in (
        ["git", "ls-files", "--cached", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    ):
        proc = subprocess.run(
            args,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
        files.extend(ROOT / p.decode("utf-8") for p in proc.stdout.split(b"\0") if p)
    return sorted(set(files))


def is_game_text_path(rel: str) -> bool:
    return (
        any(marker in rel for marker in BULK_GAME_PATH_MARKERS)
        or rel.startswith("data/")
        or "wutheringdata" in rel.casefold()
    )


def main() -> int:
    failures: list[str] = []
    for path in candidate_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel.endswith((".db", ".sqlite", ".sqlite3")):
            failures.append(f"tracked generated DB: {rel}")
            continue
        if not path.exists():
            continue
        size = path.stat().st_size
        if is_game_text_path(rel) and size > MAX_GAME_TEXT_BYTES:
            failures.append(f"tracked bulk game-text file >1MB: {rel} ({size} bytes)")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("repo hygiene ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
