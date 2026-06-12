from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.lookup import TermService  # noqa: E402


SEEDS = {
    "共鸣者": "Resonator",
    "声骸": "Echo",
    "漂泊者": "Rover",
    "今汐": "Jinhsi",
    "守岸人": "Shorekeeper",
    "安可": "Encore",
    "卡卡罗": "Calcharo",
    "椿": "Camellya",
}

PINYIN_SEEDS = {
    "gongmingzhe": "Resonator",
    "shenghai": "Echo",
    "piaobozhe": "Rover",
    "jinxi": "Jinhsi",
    "shouanren": "Shorekeeper",
    "anke": "Encore",
    "kakaluo": "Calcharo",
    "chun": "Camellya",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument(
        "--discrepancies",
        default="goal-runs/wuwaterm-v2-translator/seed-discrepancies.json",
    )
    args = parser.parse_args()

    service = TermService(args.db)
    discrepancies = []
    failures = []

    for zh, expected in SEEDS.items():
        got = service.term_text(zh)
        print(f"{zh}\t{got}")
        if got is None:
            failures.append(f"{zh}: no DB hit")
        elif got != expected:
            discrepancies.append({"query": zh, "seed_expected": expected, "official_db": got})

    for query, expected in PINYIN_SEEDS.items():
        result = service.lookup(query)
        got = result.official_text()
        print(f"{query}\t{got}")
        if got != expected:
            failures.append(f"{query}: expected top-1 {expected}, got {got}")

    if discrepancies:
        path = ROOT / args.discrepancies
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(discrepancies, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"seed discrepancies recorded: {path}")
    else:
        path = ROOT / args.discrepancies
        if path.exists():
            path.unlink()
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
