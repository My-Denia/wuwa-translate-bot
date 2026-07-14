"""SQLite persistence for official term records."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .constants import CATEGORY_ORDER, PINNED_WUTHERINGDATA_COMMIT, SourceProfile
from .models import TermEntry, TermRecord
from .normalize import normalize_text


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS terms (
  id INTEGER PRIMARY KEY,
  category TEXT NOT NULL,
  source_file TEXT NOT NULL,
  source_id TEXT NOT NULL,
  text_key TEXT NOT NULL,
  zh TEXT NOT NULL,
  en TEXT NOT NULL,
  zh_norm TEXT NOT NULL,
  en_norm TEXT NOT NULL,
  pinyin TEXT NOT NULL,
  pinyin_abbrev TEXT NOT NULL,
  priority INTEGER NOT NULL,
  UNIQUE(category, source_file, source_id, text_key, zh, en)
);

CREATE INDEX IF NOT EXISTS idx_terms_zh_norm ON terms(zh_norm);
CREATE INDEX IF NOT EXISTS idx_terms_en_norm ON terms(en_norm);
CREATE INDEX IF NOT EXISTS idx_terms_pinyin ON terms(pinyin);
CREATE INDEX IF NOT EXISTS idx_terms_category ON terms(category);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def create_database(
    db_path: str | Path,
    records: Iterable[TermRecord],
    source_profile: SourceProfile | None = None,
) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = connect(path)
    try:
        initialize(conn)
        commit = (
            source_profile.pinned_commit
            if source_profile is not None
            else PINNED_WUTHERINGDATA_COMMIT
        )
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            ("wutheringdata_commit", commit),
        )
        if source_profile is not None:
            conn.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                (
                    ("source_profile", source_profile.name),
                    ("source_repo_url", source_profile.repo_url),
                ),
            )
        insert_records(conn, records)
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def insert_records(conn: sqlite3.Connection, records: Iterable[TermRecord]) -> int:
    from .build_pinyin import pinyin_abbrev_for, pinyin_for

    rows = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for record in sorted(
        records,
        key=lambda r: (
            CATEGORY_ORDER.get(r.category, 999),
            r.category,
            r.text_key,
            r.source_file,
            str(r.source_id),
            r.zh,
            r.en,
        ),
    ):
        key = (
            record.category,
            record.source_file,
            str(record.source_id),
            record.text_key,
            record.zh,
            record.en,
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            (
                record.category,
                record.source_file,
                str(record.source_id),
                record.text_key,
                record.zh,
                record.en,
                normalize_text(record.zh),
                normalize_text(record.en),
                pinyin_for(record.zh),
                pinyin_abbrev_for(record.zh),
                CATEGORY_ORDER.get(record.category, 999),
            )
        )
    conn.executemany(
        """
        INSERT INTO terms(
          category, source_file, source_id, text_key, zh, en, zh_norm, en_norm,
          pinyin, pinyin_abbrev, priority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def row_to_entry(row: sqlite3.Row) -> TermEntry:
    return TermEntry(
        id=int(row["id"]),
        category=row["category"],
        source_file=row["source_file"],
        source_id=row["source_id"],
        text_key=row["text_key"],
        zh=row["zh"],
        en=row["en"],
        pinyin=row["pinyin"],
        pinyin_abbrev=row["pinyin_abbrev"],
    )


def category_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT category, COUNT(*) AS count FROM terms GROUP BY category ORDER BY category"
    ).fetchall()
    return {row["category"]: int(row["count"]) for row in rows}
