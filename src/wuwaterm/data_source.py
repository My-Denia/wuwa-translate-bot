"""Sparse checkout management for WutheringData."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .constants import WUTHERINGDATA_REPO, get_source_profile


class DataSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceProvenance:
    """Observed source identity written into a generated dictionary."""

    profile: str
    repo_url: str
    commit: str
    game_version: str
    resource_version: str
    changelist: str


_VERSION_PATTERNS = {
    "game_version": re.compile(r"^>\s*Game Version:\s*(\S+)\s*$"),
    "resource_version": re.compile(r"^>\s*Resource Version:\s*(\S+)\s*$"),
    "changelist": re.compile(r"^>\s*Changelist:\s*(\d+)\s*$"),
}
_TRAILING_HTML_BREAK = re.compile(r"\s*</?br\s*/?>\s*$", re.IGNORECASE)


def parse_source_version(path: str | Path) -> dict[str, str]:
    version_path = Path(path)
    try:
        lines = version_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DataSourceError(f"cannot read source version file {version_path}: {exc}") from exc

    values: dict[str, str] = {}
    for raw_line in lines:
        # The upstream README currently terminates its version lines with the
        # non-standard but harmless ``</br>`` spelling. Strip only one trailing
        # HTML break; all field names and values remain exact-match checked.
        line = _TRAILING_HTML_BREAK.sub("", raw_line)
        for key, pattern in _VERSION_PATTERNS.items():
            match = pattern.fullmatch(line)
            if match:
                if key in values:
                    raise DataSourceError(
                        f"duplicate {key.replace('_', ' ')} in {version_path}"
                    )
                values[key] = match.group(1)
    missing = sorted(set(_VERSION_PATTERNS) - set(values))
    if missing:
        raise DataSourceError(
            f"missing version fields in {version_path}: {', '.join(missing)}"
        )
    return values


def inspect_data_source(
    data_dir: str | Path,
    profile_name: str | None = None,
    *,
    expected_repo_url: str | None = None,
) -> SourceProvenance:
    """Measure and validate the active checkout before a database build."""

    profile = get_source_profile(profile_name)
    root = Path(data_dir)
    if not (root / ".git").exists():
        raise DataSourceError(f"{root} is not a Git checkout")

    actual_repo = _run(["git", "remote", "get-url", "origin"], cwd=root)
    expected_repo = (
        profile.repo_url if expected_repo_url is None else expected_repo_url
    )
    if actual_repo != expected_repo:
        raise DataSourceError(
            f"expected origin {expected_repo}, got {actual_repo}"
        )
    actual_commit = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if actual_commit != profile.pinned_commit:
        raise DataSourceError(
            f"expected {profile.pinned_commit}, got checkout {actual_commit}"
        )
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if dirty:
        raise DataSourceError("source checkout has modifications or untracked files")
    if profile.version_file is None:
        return SourceProvenance(
            profile=profile.name,
            repo_url=actual_repo,
            commit=actual_commit,
            game_version="unavailable",
            resource_version="unavailable",
            changelist="unavailable",
        )

    version = parse_source_version(root / profile.version_file)
    expected = {
        "game_version": profile.expected_game_version,
        "resource_version": profile.expected_resource_version,
        "changelist": profile.expected_changelist,
    }
    for key, expected_value in expected.items():
        if expected_value is None:
            raise DataSourceError(f"profile {profile.name} has no expected {key}")
        if version[key] != expected_value:
            raise DataSourceError(
                f"expected {key} {expected_value}, got {version[key]}"
            )
    return SourceProvenance(
        profile=profile.name,
        repo_url=actual_repo,
        commit=actual_commit,
        game_version=version["game_version"],
        resource_version=version["resource_version"],
        changelist=version["changelist"],
    )


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
    """Refresh the pinned checkout, optionally through an explicit mirror.

    The override is validated for this refresh only. Subsequent database builds
    remain strictly bound to the profile origin; this function does not weaken
    the default provenance anchor.
    """

    profile = get_source_profile(profile_name)
    effective_repo_url = repo_url or profile.repo_url or WUTHERINGDATA_REPO
    dest_path = Path(dest)
    if not dest_path.exists():
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--sparse",
                effective_repo_url,
                str(dest_path),
            ]
        )
    elif not (dest_path / ".git").exists():
        raise DataSourceError(f"{dest_path} exists but is not a Git checkout")
    else:
        _run(
            ["git", "remote", "set-url", "origin", effective_repo_url],
            cwd=dest_path,
        )

    sparse_paths = [
        f"/{path}" if path == profile.version_file else f"/{path}/"
        for path in profile.sparse_paths
    ]
    _run(["git", "sparse-checkout", "set", "--no-cone", *sparse_paths], cwd=dest_path)
    _run(["git", "fetch", "--depth", "1", "origin", profile.pinned_commit], cwd=dest_path)
    _run(["git", "checkout", "--detach", profile.pinned_commit], cwd=dest_path)
    _run(["git", "sparse-checkout", "set", "--no-cone", *sparse_paths], cwd=dest_path)
    inspect_data_source(
        dest_path,
        profile.name,
        expected_repo_url=effective_repo_url,
    )
    return dest_path
