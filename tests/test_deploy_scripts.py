from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_vps_update_uses_atomic_database_build():
    text = (ROOT / "deploy" / "vps-update.sh").read_text(encoding="utf-8")

    assert "build-db --atomic" in text


def test_entrypoint_passes_extra_build_arguments():
    text = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'shift\n    exec python -m wuwaterm.cli build-db' in text
    assert '"$@"' in text


@pytest.mark.parametrize(
    "args",
    [
        ["refresh-data", "--help"],
        ["build-db", "--atomic", "--help"],
        ["verify-db", "--help"],
    ],
)
def test_entrypoint_forwards_extra_arguments_to_subcommands(args):
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        ["sh", str(ROOT / "deploy" / "entrypoint.sh"), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
