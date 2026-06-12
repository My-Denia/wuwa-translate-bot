from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.builder import build_database  # noqa: E402
from wuwaterm.constants import source_profile_choices  # noqa: E402


def normalized_dump_sha256(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        dump = "\n".join(conn.iterdump()) + "\n"
    dump = dump.replace("\r\n", "\n")
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--profile", choices=source_profile_choices())
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    db1 = out / "terms-idempotency-1.db"
    db2 = out / "terms-idempotency-2.db"
    build_database(args.data_dir, db1, profile_name=args.profile)
    build_database(args.data_dir, db2, profile_name=args.profile)
    h1 = normalized_dump_sha256(db1)
    h2 = normalized_dump_sha256(db2)
    (out / "idempotency-hashes.txt").write_text(
        f"{h1}  {db1.name}  lf-normalized-dump-sha256\n"
        f"{h2}  {db2.name}  lf-normalized-dump-sha256\n",
        encoding="utf-8",
    )
    print(f"{h1}  {db1}")
    print(f"{h2}  {db2}")
    if h1 != h2:
        print("idempotency hash mismatch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
