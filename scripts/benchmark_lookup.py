from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wuwaterm.lookup import TermService  # noqa: E402


def _measure(call, *, warmup: int, iterations: int) -> tuple[float, float]:
    for _ in range(warmup):
        call()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000.0)
    ordered = sorted(samples)
    p95_index = max(0, min(len(ordered) - 1, round(len(ordered) * 0.95) - 1))
    return statistics.median(ordered), ordered[p95_index]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure exact, pinyin, and long exact-only dictionary lookups."
    )
    parser.add_argument("db")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0:
        parser.error("iterations must be positive and warmup non-negative")

    service = TermService(args.db)
    cases = {
        "exact": lambda: service.lookup_exact("声骸"),
        "pinyin": lambda: service.lookup("shenghai"),
        "long_2000_exact_only": lambda: service.lookup_exact("中" * 2000),
        "long_4096_exact_only": lambda: service.lookup_exact("中" * 4096),
    }
    metadata = service.metadata()
    print(f"terms\t{service.term_count()}")
    print(f"source_commit\t{metadata.get('wutheringdata_commit', 'unknown')}")
    print(f"iterations\t{args.iterations}")
    print(f"warmup\t{args.warmup}")
    for name, call in cases.items():
        median_ms, p95_ms = _measure(
            call,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        print(f"{name}\tmedian_ms={median_ms:.3f}\tp95_ms={p95_ms:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
