"""Sparse checkout management for WutheringData."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .constants import WUTHERINGDATA_REPO, get_source_profile


class DataSourceError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise DataSourceError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def refresh_data(
    dest: str | Path,
    repo_url: str | None = None,
    profile_name: str | None = None,
) -> Path:
    profile = get_source_profile(profile_name)
    repo_url = repo_url or profile.repo_url or WUTHERINGDATA_REPO
    dest_path = Path(dest)
    if not dest_path.exists():
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--filter=blob:none", "--sparse", repo_url, str(dest_path)])
    elif not (dest_path / ".git").exists():
        raise DataSourceError(f"{dest_path} exists but is not a Git checkout")
    else:
        _run(["git", "remote", "set-url", "origin", repo_url], cwd=dest_path)

    sparse_paths = list(profile.sparse_paths)
    _run(["git", "sparse-checkout", "set", *sparse_paths], cwd=dest_path)
    _run(["git", "fetch", "--depth", "1", "origin", profile.pinned_commit], cwd=dest_path)
    _run(["git", "checkout", "--detach", profile.pinned_commit], cwd=dest_path)
    _run(["git", "sparse-checkout", "set", *sparse_paths], cwd=dest_path)
    actual = _run(["git", "rev-parse", "HEAD"], cwd=dest_path)
    if actual != profile.pinned_commit:
        raise DataSourceError(f"expected {profile.pinned_commit}, got checkout {actual}")
    return dest_path
