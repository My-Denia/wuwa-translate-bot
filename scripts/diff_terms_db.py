from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.db import connect  # noqa: E402


SourceKey = tuple[str, str, str, str]
Pair = tuple[str, str]


@dataclass(frozen=True, order=True)
class TermRow:
    category: str
    source_file: str
    source_id: str
    text_key: str
    zh: str
    en: str

    @property
    def source_key(self) -> SourceKey:
        return (self.category, self.source_file, self.source_id, self.text_key)

    @property
    def pair(self) -> Pair:
        return (self.zh, self.en)


@dataclass(frozen=True, order=True)
class ChangedTerm:
    key: SourceKey
    old: TermRow
    new: TermRow

    @property
    def category(self) -> str:
        return self.key[0]


@dataclass(frozen=True, order=True)
class AmbiguousSourceKey:
    key: SourceKey
    old_pairs: tuple[Pair, ...]
    new_pairs: tuple[Pair, ...]

    @property
    def category(self) -> str:
        return self.key[0]


@dataclass(frozen=True, order=True)
class CountChange:
    category: str
    old: int
    new: int

    @property
    def delta(self) -> int:
        return self.new - self.old


@dataclass(frozen=True, order=True)
class MetadataChange:
    key: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class DbSnapshot:
    path: Path
    terms: tuple[TermRow, ...]
    metadata: dict[str, str]
    category_counts: dict[str, int]


@dataclass(frozen=True)
class DiffReport:
    old: DbSnapshot
    new: DbSnapshot
    added: tuple[TermRow, ...]
    removed: tuple[TermRow, ...]
    changed: tuple[ChangedTerm, ...]
    ambiguous_source_keys: tuple[AmbiguousSourceKey, ...]
    category_count_changes: tuple[CountChange, ...]
    metadata_differences: tuple[MetadataChange, ...]


def _term_sort_key(term: TermRow) -> tuple[str, str, str, str, str, str]:
    return (
        term.category,
        term.source_file,
        term.source_id,
        term.text_key,
        term.zh,
        term.en,
    )


def _key_sort_key(key: SourceKey) -> tuple[str, str, str, str]:
    return key


def _require_tables(conn: sqlite3.Connection, path: Path) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('metadata', 'terms')"
    ).fetchall()
    present = {str(row["name"]) for row in rows}
    missing = sorted({"metadata", "terms"} - present)
    if missing:
        raise ValueError(f"{path}: missing table(s): {', '.join(missing)}")


def load_snapshot(db_path: str | Path) -> DbSnapshot:
    path = Path(db_path)
    with connect(path) as conn:
        _require_tables(conn, path)
        term_rows = conn.execute(
            """
            SELECT category, source_file, source_id, text_key, zh, en
            FROM terms
            ORDER BY category, source_file, source_id, text_key, zh, en
            """
        ).fetchall()
        metadata_rows = conn.execute(
            "SELECT key, value FROM metadata ORDER BY key"
        ).fetchall()
        category_rows = conn.execute(
            "SELECT category, COUNT(*) AS count FROM terms GROUP BY category"
        ).fetchall()

    terms = tuple(
        TermRow(
            category=str(row["category"]),
            source_file=str(row["source_file"]),
            source_id=str(row["source_id"]),
            text_key=str(row["text_key"]),
            zh=str(row["zh"]),
            en=str(row["en"]),
        )
        for row in term_rows
    )
    return DbSnapshot(
        path=path,
        terms=terms,
        metadata={str(row["key"]): str(row["value"]) for row in metadata_rows},
        category_counts={
            str(row["category"]): int(row["count"]) for row in category_rows
        },
    )


def _source_map(terms: tuple[TermRow, ...]) -> dict[SourceKey, tuple[TermRow, ...]]:
    grouped: dict[SourceKey, list[TermRow]] = defaultdict(list)
    for term in terms:
        grouped[term.source_key].append(term)
    return {
        key: tuple(sorted(rows, key=_term_sort_key))
        for key, rows in sorted(grouped.items(), key=lambda item: _key_sort_key(item[0]))
    }


def _pair_lookup(rows: tuple[TermRow, ...]) -> dict[Pair, TermRow]:
    return {row.pair: row for row in rows}


def diff_snapshots(old: DbSnapshot, new: DbSnapshot) -> DiffReport:
    old_by_source = _source_map(old.terms)
    new_by_source = _source_map(new.terms)

    added: list[TermRow] = []
    removed: list[TermRow] = []
    changed: list[ChangedTerm] = []
    ambiguous: list[AmbiguousSourceKey] = []

    for key in sorted(set(old_by_source) | set(new_by_source), key=_key_sort_key):
        old_rows = old_by_source.get(key, ())
        new_rows = new_by_source.get(key, ())
        if not old_rows:
            added.extend(new_rows)
            continue
        if not new_rows:
            removed.extend(old_rows)
            continue

        if len(old_rows) == 1 and len(new_rows) == 1:
            if old_rows[0].pair != new_rows[0].pair:
                changed.append(ChangedTerm(key=key, old=old_rows[0], new=new_rows[0]))
            continue

        old_pairs = tuple(sorted(row.pair for row in old_rows))
        new_pairs = tuple(sorted(row.pair for row in new_rows))
        if old_pairs == new_pairs:
            continue

        ambiguous.append(
            AmbiguousSourceKey(key=key, old_pairs=old_pairs, new_pairs=new_pairs)
        )
        old_lookup = _pair_lookup(old_rows)
        new_lookup = _pair_lookup(new_rows)
        for pair in sorted(set(new_lookup) - set(old_lookup)):
            added.append(new_lookup[pair])
        for pair in sorted(set(old_lookup) - set(new_lookup)):
            removed.append(old_lookup[pair])

    category_changes = tuple(
        CountChange(category=category, old=old.category_counts.get(category, 0), new=new.category_counts.get(category, 0))
        for category in sorted(set(old.category_counts) | set(new.category_counts))
        if old.category_counts.get(category, 0) != new.category_counts.get(category, 0)
    )
    metadata_changes = tuple(
        MetadataChange(key=key, old=old.metadata.get(key), new=new.metadata.get(key))
        for key in sorted(set(old.metadata) | set(new.metadata))
        if old.metadata.get(key) != new.metadata.get(key)
    )
    return DiffReport(
        old=old,
        new=new,
        added=tuple(sorted(added, key=_term_sort_key)),
        removed=tuple(sorted(removed, key=_term_sort_key)),
        changed=tuple(sorted(changed)),
        ambiguous_source_keys=tuple(sorted(ambiguous)),
        category_count_changes=category_changes,
        metadata_differences=metadata_changes,
    )


def diff_databases(old_db: str | Path, new_db: str | Path) -> DiffReport:
    return diff_snapshots(load_snapshot(old_db), load_snapshot(new_db))


def _source_key_to_dict(key: SourceKey) -> dict[str, str]:
    category, source_file, source_id, text_key = key
    return {
        "category": category,
        "source_file": source_file,
        "source_id": source_id,
        "text_key": text_key,
    }


def _term_to_dict(term: TermRow) -> dict[str, str]:
    return {
        **_source_key_to_dict(term.source_key),
        "zh": term.zh,
        "en": term.en,
    }


def _changed_to_dict(change: ChangedTerm) -> dict[str, Any]:
    return {
        "key": _source_key_to_dict(change.key),
        "old": {"zh": change.old.zh, "en": change.old.en},
        "new": {"zh": change.new.zh, "en": change.new.en},
    }


def _ambiguous_to_dict(item: AmbiguousSourceKey) -> dict[str, Any]:
    return {
        "key": _source_key_to_dict(item.key),
        "old_pairs": [{"zh": zh, "en": en} for zh, en in item.old_pairs],
        "new_pairs": [{"zh": zh, "en": en} for zh, en in item.new_pairs],
    }


def _examples_by_category(report: DiffReport, limit: int = 3) -> dict[str, dict[str, list[dict[str, Any]]]]:
    categories = sorted(
        {term.category for term in report.added}
        | {term.category for term in report.removed}
        | {change.category for change in report.changed}
    )
    examples: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for category in categories:
        added = [term for term in report.added if term.category == category][:limit]
        removed = [term for term in report.removed if term.category == category][:limit]
        changed = [
            change for change in report.changed if change.category == category
        ][:limit]
        examples[category] = {
            "added": [_term_to_dict(term) for term in added],
            "removed": [_term_to_dict(term) for term in removed],
            "changed": [_changed_to_dict(change) for change in changed],
        }
    return examples


def report_to_dict(report: DiffReport) -> dict[str, Any]:
    return {
        "old_db": str(report.old.path),
        "new_db": str(report.new.path),
        "totals": {
            "old_terms": len(report.old.terms),
            "new_terms": len(report.new.terms),
        },
        "summary": {
            "added": len(report.added),
            "removed": len(report.removed),
            "changed_zh_en_pairs": len(report.changed),
            "ambiguous_source_keys": len(report.ambiguous_source_keys),
        },
        "category_count_changes": [
            {
                "category": change.category,
                "old": change.old,
                "new": change.new,
                "delta": change.delta,
            }
            for change in report.category_count_changes
        ],
        "metadata_differences": [
            {"key": change.key, "old": change.old, "new": change.new}
            for change in report.metadata_differences
        ],
        "added": [_term_to_dict(term) for term in report.added],
        "removed": [_term_to_dict(term) for term in report.removed],
        "changed": [_changed_to_dict(change) for change in report.changed],
        "ambiguous_source_keys": [
            _ambiguous_to_dict(item) for item in report.ambiguous_source_keys
        ],
        "examples_by_category": _examples_by_category(report),
    }


def _format_term(term: TermRow) -> str:
    return (
        f"{term.category} {term.source_file}:{term.source_id} "
        f"{term.text_key}: {term.zh} / {term.en}"
    )


def _format_change(change: ChangedTerm) -> str:
    category, source_file, source_id, text_key = change.key
    return (
        f"{category} {source_file}:{source_id} {text_key}: "
        f"{change.old.zh} / {change.old.en} -> {change.new.zh} / {change.new.en}"
    )


def _format_value(value: str | None) -> str:
    return "<missing>" if value is None else value


def _append_term_section(lines: list[str], title: str, rows: tuple[TermRow, ...]) -> None:
    lines.append("")
    lines.append(title)
    if not rows:
        lines.append("- none")
        return
    for row in rows:
        lines.append(f"- {_format_term(row)}")


def _append_changed_section(
    lines: list[str], title: str, rows: tuple[ChangedTerm, ...]
) -> None:
    lines.append("")
    lines.append(title)
    if not rows:
        lines.append("- none")
        return
    for row in rows:
        lines.append(f"- {_format_change(row)}")


def report_to_text(report: DiffReport) -> str:
    lines = [
        "Term DB diff",
        "============",
        f"Old DB: {report.old.path}",
        f"New DB: {report.new.path}",
        "",
        "Totals",
        f"- old terms: {len(report.old.terms)}",
        f"- new terms: {len(report.new.terms)}",
        "",
        "Summary",
        f"- added: {len(report.added)}",
        f"- removed: {len(report.removed)}",
        f"- changed zh/en pairs: {len(report.changed)}",
        f"- ambiguous source keys: {len(report.ambiguous_source_keys)}",
    ]

    lines.append("")
    lines.append("Category count changes")
    if report.category_count_changes:
        for change in report.category_count_changes:
            sign = "+" if change.delta > 0 else ""
            lines.append(
                f"- {change.category}: {change.old} -> {change.new} ({sign}{change.delta})"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Metadata differences")
    if report.metadata_differences:
        for change in report.metadata_differences:
            lines.append(
                f"- {change.key}: {_format_value(change.old)} -> {_format_value(change.new)}"
            )
    else:
        lines.append("- none")

    _append_changed_section(lines, "Changed zh/en pairs", report.changed)
    _append_term_section(lines, "Added terms", report.added)
    _append_term_section(lines, "Removed terms", report.removed)

    lines.append("")
    lines.append("Ambiguous source keys")
    if report.ambiguous_source_keys:
        for item in report.ambiguous_source_keys:
            category, source_file, source_id, text_key = item.key
            lines.append(
                f"- {category} {source_file}:{source_id} {text_key}: "
                f"old pairs={len(item.old_pairs)}, new pairs={len(item.new_pairs)}; "
                "reported as added/removed pairs"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Examples by category")
    examples = _examples_by_category(report)
    if not examples:
        lines.append("- none")
    for category, grouped in examples.items():
        lines.append(f"- {category}:")
        for label in ("added", "removed", "changed"):
            if not grouped[label]:
                continue
            rendered = []
            for item in grouped[label]:
                if label == "changed":
                    old_pair = item["old"]
                    new_pair = item["new"]
                    key = item["key"]
                    rendered.append(
                        f"{key['source_file']}:{key['source_id']} {key['text_key']} "
                        f"{old_pair['zh']} / {old_pair['en']} -> "
                        f"{new_pair['zh']} / {new_pair['en']}"
                    )
                else:
                    rendered.append(
                        f"{item['source_file']}:{item['source_id']} "
                        f"{item['text_key']} {item['zh']} / {item['en']}"
                    )
            lines.append(f"  - {label}: {'; '.join(rendered)}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two generated Wuwa term SQLite databases."
    )
    parser.add_argument("old_db")
    parser.add_argument("new_db")
    parser.add_argument("--json", action="store_true", help="emit JSON output")
    args = parser.parse_args(argv)

    try:
        report = diff_databases(args.old_db, args.new_db)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"diff failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    else:
        print(report_to_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
