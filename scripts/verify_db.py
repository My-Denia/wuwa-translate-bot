from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.db import category_counts, connect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--min-category", action="append", default=[])
    args = parser.parse_args()

    with connect(args.db) as conn:
        counts = category_counts(conn)
    for category in sorted(counts):
        print(f"{category}\t{counts[category]}")
    missing = [category for category in args.min_category if counts.get(category, 0) <= 0]
    if missing:
        print(f"missing or empty categories: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
