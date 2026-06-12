from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.db import connect  # noqa: E402
from wuwaterm.lookup import TermService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--sample-size", type=int, default=500)
    args = parser.parse_args()

    with connect(args.db) as conn:
        rows = conn.execute(
            """
            SELECT t.*
            FROM terms t
            JOIN (
              SELECT zh_norm
              FROM terms
              GROUP BY zh_norm
              HAVING COUNT(DISTINCT en) = 1
            ) unique_zh ON unique_zh.zh_norm = t.zh_norm
            GROUP BY t.zh_norm, t.en
            ORDER BY t.id
            LIMIT ?
            """,
            (args.sample_size,),
        ).fetchall()
    if len(rows) < args.sample_size:
        print(
            f"only {len(rows)} unambiguous exact-hit rows available, need {args.sample_size}",
            file=sys.stderr,
        )
        return 1

    service = TermService(args.db)
    for row in rows:
        got = service.term_text(row["zh"])
        if got != row["en"]:
            print(
                f"exact-hit mismatch id={row['id']} zh={row['zh']!r} expected={row['en']!r} got={got!r}",
                file=sys.stderr,
            )
            return 1
    print(f"exact-hit byte-for-byte rows passed: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
