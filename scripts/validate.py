"""Run every offline gate this repository has, in one command.

The checks existed before this script did; what did not exist was one way to
run them. CI listed them as five separate workflow steps, the README listed
four of them, and `docs/validation.md` listed eleven POSIX-flavoured command
lines. Three lists drift: a gate added to CI is invisible to a contributor
reading the README, and a contributor who runs the README's list can be green
locally and red on the pull request. So the list lives HERE, once, and both CI
and the documentation point at this file instead of repeating it.

Standard library only, deliberately: this is the script someone runs BEFORE
they have worked out which extras to install, and it must be able to tell them
that the linter or the test runner is missing rather than fail on its own
imports. Every step is spawned with ``sys.executable``, so the interpreter that
runs this script is the interpreter the checks run under - on Windows as well
as on Linux, where the documented `.venv/bin/python` prefix does not exist.

The working directory does not matter; the repository root is derived from this
file's location and every step is run there.

    python scripts/validate.py            # everything, in order
    python scripts/validate.py --list     # what it would run, runs nothing
    python scripts/validate.py --quick    # everything except the test suite
    python scripts/validate.py --only ruff
    python scripts/validate.py --client   # additionally: the desktop client suite

Exit status is 0 only when every selected step passed. The first failing step
stops the run and its name is printed, so the output says which gate failed
rather than leaving the reader to match a traceback to a command.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# (name, what a failure means, argv after the interpreter, working directory)
SERVER_STEPS: list[tuple[str, str, list[str], Path]] = [
    (
        "hygiene",
        "generated data, a database blob or runtime state would be committed",
        ["scripts/check_repo_hygiene.py"],
        ROOT,
    ),
    (
        "non-goals",
        "a product boundary this project has refused was crossed in text",
        ["scripts/check_non_goals.py"],
        ROOT,
    ),
    (
        "architecture",
        "an import crosses a layer boundary the architecture forbids",
        ["scripts/check_architecture_boundaries.py"],
        ROOT,
    ),
    (
        "api-contract",
        "the committed docs/api/openapi.json no longer equals what the app generates",
        ["scripts/check_api_contract.py"],
        ROOT,
    ),
    (
        "ruff",
        "the linter reports an error under the rule set pinned in pyproject.toml",
        ["-m", "ruff", "check", "."],
        ROOT,
    ),
    (
        "pytest",
        "the repository test suite is red",
        ["-m", "pytest"],
        ROOT,
    ),
]

# Not part of the default run: it needs Qt, keyring and a Windows toolchain,
# which is exactly why the client's text-level gates were put in the main suite
# instead. Opt in with --client from an interpreter that has client[dev].
CLIENT_STEP: tuple[str, str, list[str], Path] = (
    "client-tests",
    "the desktop client suite is red",
    ["-m", "pytest"],
    ROOT / "client",
)

SLOW_STEPS = {"pytest", "client-tests"}


def selected_steps(args: argparse.Namespace) -> list[tuple[str, str, list[str], Path]]:
    steps = list(SERVER_STEPS)
    if args.client:
        steps.append(CLIENT_STEP)
    if args.only:
        by_name = {step[0]: step for step in steps}
        if args.only not in by_name:
            raise SystemExit(
                f"validate: unknown step {args.only!r}; known steps: "
                + ", ".join(by_name)
            )
        return [by_name[args.only]]
    if args.quick:
        steps = [step for step in steps if step[0] not in SLOW_STEPS]
    return steps


def describe(steps: list[tuple[str, str, list[str], Path]]) -> None:
    width = max(len(name) for name, _, _, _ in steps)
    for name, meaning, argv, cwd in steps:
        where = "" if cwd == ROOT else f"  (in {cwd.relative_to(ROOT).as_posix()}/)"
        print(f"{name.ljust(width)}  python {' '.join(argv)}{where}")
        print(f"{' ' * width}  fails when: {meaning}")


def run(steps: list[tuple[str, str, list[str], Path]]) -> int:
    for position, (name, meaning, argv, cwd) in enumerate(steps, start=1):
        command = [sys.executable, *argv]
        print(f"[{position}/{len(steps)}] {name}: {' '.join(command)}", flush=True)
        started = time.monotonic()
        try:
            completed = subprocess.run(command, cwd=cwd, check=False)
        except OSError as exc:  # interpreter or working directory unusable
            print(f"validate: step {name!r} could not start: {exc}", file=sys.stderr)
            return 1
        elapsed = time.monotonic() - started
        if completed.returncode != 0:
            print(
                f"validate: step {name!r} FAILED with exit code "
                f"{completed.returncode} after {elapsed:.1f}s - {meaning}",
                file=sys.stderr,
            )
            return completed.returncode
        print(f"    ok ({elapsed:.1f}s)", flush=True)
    print(f"validate: {len(steps)} steps passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate",
        description="Run the repository's offline gates in one command.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="print the steps and their commands, run nothing",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="skip the test suites; the fast gates only",
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        help="run a single step by name",
    )
    parser.add_argument(
        "--client",
        action="store_true",
        help="also run the desktop client suite (needs the client's extras)",
    )
    args = parser.parse_args(argv)

    steps = selected_steps(args)
    if args.list_only:
        describe(steps)
        return 0
    return run(steps)


if __name__ == "__main__":
    raise SystemExit(main())
