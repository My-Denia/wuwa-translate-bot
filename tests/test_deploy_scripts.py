from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from wuwaterm.constants import SOURCE_PROFILES, get_source_profile
from wuwaterm.data_source import SourceProvenance
from wuwaterm.db import create_database
from wuwaterm.models import TermRecord

import scripts.deployment_manifest as deployment_manifest_module
from scripts.deployment_manifest import (
    build_manifest,
    durable_remove,
    durable_replace,
    verify_manifest,
    write_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def test_vps_update_uses_atomic_database_build():
    text = (ROOT / "deploy" / "vps-update.sh").read_text(encoding="utf-8")

    assert "wuwaterm-builder build-db --atomic" in text


def test_vps_update_uses_builder_service_for_data_commands():
    text = (ROOT / "deploy" / "vps-update.sh").read_text(encoding="utf-8")

    assert "wuwaterm refresh-data" not in text
    assert "wuwaterm build-db" not in text
    assert "wuwaterm verify-db" not in text
    assert "wuwaterm-builder refresh-data" in text
    assert "wuwaterm-builder build-db --atomic" in text
    assert "wuwaterm-builder verify-db" in text
    assert "scripts/verify_exact_hits.py" in text
    assert "scripts/verify_seed_terms.py" in text
    assert "scripts/diff_terms_db.py" in text
    assert "${TELEGRAM_BOT_TOKEN+x}" in text
    assert "${TELEGRAM_TEST_CHAT_ID+x}" in text
    assert "${WUWATERM_OPENAI_API_KEY+x}" in text
    assert "${WUWATERM_OPENAI_BASE_URL+x}" in text
    assert "${WUWATERM_REDACTION_SECRET+x}" in text
    assert "chat_settings.json" in text
    assert "channel_replies.json" in text
    assert 'data/$state_file' in text
    assert 'state/$state_file' in text
    assert 'python3 scripts/validate_state_file.py "$tmp" "$state_file"' in text
    assert 'ln "$tmp" "state/$state_file"' in text
    assert (
        'python3 scripts/validate_state_file.py "state/$state_file" "$state_file"'
        in text
    )


def test_vps_update_freezes_legacy_state_only_after_database_is_ready():
    text = (ROOT / "deploy" / "vps-update.sh").read_text(encoding="utf-8")

    builder = text.index("compose build wuwaterm-builder")
    refresh = text.index("wuwaterm-builder refresh-data")
    build = text.index("wuwaterm-builder build-db --atomic")
    verify = text.index("wuwaterm-builder verify-db")
    transaction = text.index("# Candidate DB and immutable image")
    stop = text.index("stop wuwaterm", transaction)
    migrate = text.index("for state_file in chat_settings.json channel_replies.json")
    start = text.index("compose up -d --no-build", transaction)

    assert builder < refresh < build < verify < stop < migrate < start
    assert "rollback_on_failure" in text
    assert "rollback_image_ref" in text


def test_vps_update_durably_promotes_and_restores_database_and_pointer():
    text = (ROOT / "deploy" / "vps-update.sh").read_text(encoding="utf-8")

    promotion = text.index(
        'db_promoted=1\npython3 scripts/deployment_manifest.py durable-replace'
    )
    pointer = text.index(
        'pointer_published=1\npython3 scripts/deployment_manifest.py publish-pointer'
    )
    assert promotion < pointer
    assert (
        'python3 scripts/deployment_manifest.py durable-remove --path "$db_path"'
        in text
    )
    assert '--path "$pointer_path"' in text
    assert '--source "$restore_tmp" --destination "$db_path"' in text


def test_durable_replace_and_remove_fsync_changed_directories(monkeypatch, tmp_path):
    source_dir = tmp_path / "candidates"
    destination_dir = tmp_path / "data"
    source_dir.mkdir()
    destination_dir.mkdir()
    source = source_dir / "candidate.db"
    destination = destination_dir / "terms.db"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    synced_files: list[Path] = []
    synced: list[Path] = []
    monkeypatch.setattr(
        deployment_manifest_module, "_fsync_file", synced_files.append
    )
    monkeypatch.setattr(
        deployment_manifest_module, "_fsync_directory", synced.append
    )

    durable_replace(source, destination)

    assert destination.read_bytes() == b"new"
    assert not source.exists()
    assert synced_files == [source]
    assert synced == [destination_dir.resolve(), source_dir.resolve()]

    synced.clear()
    durable_remove(destination)
    assert not destination.exists()
    assert synced == [destination_dir.resolve()]


def test_deployment_docs_do_not_recommend_live_non_atomic_state_copy():
    text = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Do not manually copy these files while the old bot is running" in text
    assert "cp -p data/chat_settings.json state/chat_settings.json" not in text
    assert "cp -p data/channel_replies.json state/channel_replies.json" not in text
    assert "Do not manually copy\nstate files while the old bot is running" in readme
    assert "copy any existing `data/chat_settings.json`" not in readme


def test_docker_context_excludes_data_and_runtime_state():
    entries = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "data" in entries
    assert "state" in entries
    assert "goal-runs" in entries
    assert ".deployments" in entries
    assert ".deploy_commit" in entries


def test_deployment_markers_are_ignored_runtime_state():
    entries = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".deployments/" in entries
    assert ".deploy_commit" in entries


def test_state_file_validator_accepts_current_state_formats(tmp_path):
    settings_path = tmp_path / "chat_settings.json"
    reply_index_path = tmp_path / "channel_replies.json"
    settings_path.write_text(
        '{"public":{"-2001":true},"allowed":[-2001]}',
        encoding="utf-8",
    )
    reply_index_path.write_text(
        '{"version":1,"entries":[{"chat_id":-2001,"message_id":4001,'
        '"expires_at":9999999999,"reply_message_ids":[5001]}]}',
        encoding="utf-8",
    )

    settings_result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_state_file.py"),
            str(settings_path),
            "chat_settings.json",
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    reply_result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_state_file.py"),
            str(reply_index_path),
            "channel_replies.json",
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert settings_result.returncode == 0, settings_result.stderr
    assert reply_result.returncode == 0, reply_result.stderr


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("chat_settings.json", '{"public":{"01":true},"allowed":[]}'),
        ("channel_replies.json", "{}"),
    ],
)
def test_state_file_validator_rejects_json_valid_schema_drift(
    tmp_path, filename, payload
):
    path = tmp_path / filename
    path.write_text(payload, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_state_file.py"),
            str(path),
            filename,
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1


def test_builder_entrypoint_passes_extra_build_arguments():
    text = (ROOT / "deploy" / "builder-entrypoint.sh").read_text(encoding="utf-8")

    assert 'exec python -m wuwaterm.cli build-db' in text
    assert '"$@"' in text


def test_runtime_entrypoint_forwards_bot_arguments():
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        ["sh", str(ROOT / "deploy" / "entrypoint.sh"), "bot", "--help"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


@pytest.mark.parametrize(
    "args",
    [
        ["refresh-data", "--help"],
        ["build-db", "--atomic", "--help"],
        ["verify-db", "--help"],
    ],
)
def test_builder_entrypoint_forwards_extra_arguments_to_subcommands(args):
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        ["sh", str(ROOT / "deploy" / "builder-entrypoint.sh"), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_builder_entrypoint_preserves_fallback_command():
    result = subprocess.run(
        ["sh", str(ROOT / "deploy" / "builder-entrypoint.sh"), "printf", "ok"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"


def test_runtime_entrypoint_rejects_data_builder_commands():
    result = subprocess.run(
        ["sh", str(ROOT / "deploy" / "entrypoint.sh"), "refresh-data"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 64
    assert "runtime image only supports" in result.stderr


def test_env_examples_are_byte_identical():
    assert (ROOT / ".env.example").read_bytes() == (
        ROOT / "deploy" / "env.example"
    ).read_bytes()


def test_env_example_covers_runtime_state_and_llm_controls():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
        "WUWATERM_LLM_TIMEOUT_SECONDS",
        "WUWATERM_LLM_MAX_CONCURRENCY",
        "WUWATERM_CHANNEL_MIN_LATIN",
        "WUWATERM_CHANNEL_MAX_PENDING",
        "WUWATERM_CHANNEL_LLM_CALLS_PER_MINUTE",
        "WUWATERM_CHANNEL_REPLY_INDEX_PATH",
        "WUWATERM_SETTINGS_PATH",
        "WUWATERM_STATE_DIR",
        "OWNER_USER_ID",
        "WUWATERM_TR_REJECT_SILENT",
    ):
        assert name in text
    assert "WUWATERM_STATE_DIR=state" in text
    assert "#WUWATERM_SETTINGS_PATH=state/chat_settings.json" in text
    assert "#WUWATERM_CHANNEL_REPLY_INDEX_PATH=state/channel_replies.json" in text
    assert "WUWATERM_SETTINGS_PATH=data/chat_settings.json" not in text
    assert "WUWATERM_CHANNEL_REPLY_INDEX_PATH=data/channel_replies.json" not in text


def test_pypinyin_is_build_only_dependency():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    dependencies = pyproject["project"]["dependencies"]
    optional = pyproject["project"]["optional-dependencies"]

    assert not any(item.startswith("pypinyin") for item in dependencies)
    assert any(item.startswith("pypinyin") for item in optional["build"])
    assert any(item.startswith("pypinyin") for item in optional["dev"])


def test_dockerfile_declares_separate_locked_runtime_and_builder_targets():
    text = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python-base AS runtime" in text
    assert "FROM python-base AS builder" in text
    assert "uv sync --locked --no-dev --no-editable --no-cache" in text
    assert "uv sync --locked --no-dev --no-editable --extra build --no-cache" in text
    runtime_section = text.split("FROM python-base AS runtime", 1)[1].split(
        "FROM python-base AS builder", 1
    )[0]
    builder_section = text.split("FROM python-base AS builder", 1)[1]
    assert "git" not in runtime_section
    assert "--extra build" not in runtime_section
    assert "apt-get install -y --no-install-recommends git ca-certificates" in builder_section
    assert "deploy/entrypoint.sh" in runtime_section
    assert "deploy/builder-entrypoint.sh" in builder_section
    assert "org.opencontainers.image.revision=$SOURCE_COMMIT" in runtime_section


def test_compose_uses_readonly_data_for_runtime_and_writable_data_for_builder():
    text = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "target: runtime" in text
    assert "target: builder" in text
    assert "wuwaterm-runtime:local" in text
    assert "wuwaterm-builder:local" in text
    assert "../data:/app/data:ro" in text
    assert "../state:/app/state" in text
    assert "WUWATERM_STATE_DIR: /app/state" in text
    assert "WUWATERM_SOURCE_PROFILE: ${WUWATERM_SOURCE_PROFILE:-arikatsu}" in text
    assert "WUWATERM_SETTINGS_PATH: /app/state/chat_settings.json" in text
    assert (
        "WUWATERM_CHANNEL_REPLY_INDEX_PATH: /app/state/channel_replies.json"
        in text
    )
    assert "../data:/app/data\n" in text
    builder_section = text.split("  wuwaterm-builder:", 1)[1]
    assert "env_file:" not in builder_section
    assert "${WUWATERM_RUNTIME_IMAGE:-wuwaterm-runtime:local}" in text
    assert "SOURCE_COMMIT: ${SOURCE_COMMIT:-unknown}" in text


NEW_COMMIT = "2" * 40
OLD_COMMIT = "1" * 40
OLD_IMAGE = "sha256:oldimage"
NEW_IMAGE = "sha256:newimage"


def _deployment_db(path: Path, suffix: str) -> None:
    profile = get_source_profile("arikatsu")
    provenance = SourceProvenance(
        profile=profile.name,
        repo_url=profile.repo_url,
        commit=profile.pinned_commit,
        game_version=profile.expected_game_version or "",
        resource_version=profile.expected_resource_version or "",
        changelist=profile.expected_changelist or "",
    )
    records = [
        TermRecord(
            category=category,
            source_file=f"{category}.json",
            source_id=f"{index}-{suffix}",
            text_key=f"{category}_{index}_{suffix}",
            zh="穗穗" if category == "resonator" else f"测试{index}{suffix}",
            en="Suisui" if category == "resonator" else f"Test {index} {suffix}",
        )
        for index, category in enumerate(
            (
                "core_term",
                "resonator",
                "weapon",
                "echo",
                "item",
                "skill",
                "sonata_effect",
                "location",
            ),
            start=1,
        )
    ]
    create_database(
        path,
        records,
        source_profile=profile,
        source_provenance=provenance,
    )


def test_historical_manifest_readback_survives_a_later_data_pin(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "terms.db"
    _deployment_db(db_path, "historical")
    manifest_path = tmp_path / "historical.json"
    payload = build_manifest(
        source_commit=OLD_COMMIT,
        image_ref="wuwaterm-runtime:historical",
        image_id=OLD_IMAGE,
        image_digest=OLD_IMAGE,
        image_revision=OLD_COMMIT,
        db_path=db_path,
        db_display_path="data/terms.db",
        backup_path="historical-backup",
        deployment_utc="2026-07-15T00:00:00Z",
    )
    write_manifest(manifest_path, payload)

    current = SOURCE_PROFILES["arikatsu"]
    monkeypatch.setitem(
        SOURCE_PROFILES,
        "arikatsu",
        replace(
            current,
            pinned_commit="f" * 40,
            expected_game_version="9.9.0",
            expected_resource_version="9.9.1",
            expected_changelist="9999999",
        ),
    )

    verified = verify_manifest(
        manifest_path,
        source_commit=OLD_COMMIT,
        image_id=OLD_IMAGE,
        image_digest=OLD_IMAGE,
        db_path=db_path,
    )

    assert verified["database"]["provenance"]["source_commit"] == (
        current.pinned_commit
    )


def test_read_db_provenance_closes_database_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "terms.db"
    _deployment_db(db_path, "close-check")
    real_connect = sqlite3.connect
    closed: list[bool] = []

    class TrackingConnection(sqlite3.Connection):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def tracking_connect(*args, **kwargs):
        return real_connect(*args, factory=TrackingConnection, **kwargs)

    monkeypatch.setattr(
        deployment_manifest_module.sqlite3, "connect", tracking_connect
    )

    deployment_manifest_module.read_db_provenance(db_path)

    assert closed == [True]


@pytest.fixture()
def deploy_harness(tmp_path):
    root = tmp_path / "deploy-root"
    (root / "deploy").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "data").mkdir()
    (root / "state").mkdir()
    (root / ".deployments").mkdir()
    shutil.copytree(ROOT / "src", root / "src")
    for relative in (
        "deploy/vps-update.sh",
        "deploy/docker-compose.yml",
        "scripts/deployment_manifest.py",
        "scripts/validate_state_file.py",
        "scripts/verify_db.py",
    ):
        shutil.copy2(ROOT / relative, root / relative)
    env_file = root / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=not-loaded-by-builder\n", encoding="utf-8")
    env_file.chmod(0o600)

    old_db = root / "data" / "terms.db"
    seed_db = root / "data" / "candidate-seed.db"
    _deployment_db(old_db, "old")
    _deployment_db(seed_db, "new")
    (root / ".deploy_commit").write_text(f"{OLD_COMMIT}\n", encoding="ascii")
    (root / ".deploy_commit").chmod(0o444)
    old_manifest = build_manifest(
        source_commit=OLD_COMMIT,
        image_ref="wuwaterm-runtime:old",
        image_id=OLD_IMAGE,
        image_digest=OLD_IMAGE,
        image_revision=OLD_COMMIT,
        db_path=old_db,
        db_display_path="data/terms.db",
        backup_path="legacy",
        deployment_utc="2026-07-15T00:00:00Z",
    )
    write_manifest(root / ".deployments" / f"{OLD_COMMIT}.json", old_manifest)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_script = fake_bin / "git"
    git_script.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  'status --porcelain --untracked-files=all') exit 0 ;;\n"
        "  'fetch --quiet origin main:refs/remotes/origin/main') exit 0 ;;\n"
        f"  'rev-parse HEAD') echo '{NEW_COMMIT}'; exit 0 ;;\n"
        f"  'rev-parse refs/remotes/origin/main') echo '{NEW_COMMIT}'; exit 0 ;;\n"
        "esac\n"
        "exit 2\n",
        encoding="utf-8",
    )
    git_script.chmod(0o755)

    docker_script = fake_bin / "docker"
    docker_script.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        ": \"${FAKE_DEPLOY_ROOT:?}\"\n"
        "echo \"${WUWATERM_RUNTIME_IMAGE:-unset}|$*\" >> \"$FAKE_DEPLOY_ROOT/docker.log\"\n"
        "if [ \"${1:-}\" = inspect ]; then\n"
        "  cat \"$FAKE_DEPLOY_ROOT/running-image\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${1:-}\" = image ] && [ \"${2:-}\" = tag ]; then exit 0; fi\n"
        "if [ \"${1:-}\" = image ] && [ \"${2:-}\" = inspect ]; then\n"
        "  case \"$*\" in\n"
        f"    *Config.Labels*) echo '{NEW_COMMIT}' ;;\n"
        f"    *RepoDigests*) echo '{NEW_IMAGE}' ;;\n"
        f"    *) echo '{NEW_IMAGE}' ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "case \"$*\" in\n"
        "  *'build-db --atomic'*)\n"
        "    candidate=''\n"
        "    for arg in \"$@\"; do\n"
        "      case \"$arg\" in WUWATERM_DB_PATH=*) candidate=${arg#WUWATERM_DB_PATH=} ;; esac\n"
        "    done\n"
        "    candidate=$FAKE_DEPLOY_ROOT/data/${candidate#/app/data/}\n"
        "    cp \"$FAKE_DEPLOY_ROOT/data/candidate-seed.db\" \"$candidate\"\n"
        "    if [ \"${FAKE_CORRUPT_CANDIDATE:-0}\" = 1 ]; then\n"
        "      python3 -c \"import sqlite3,sys; "
        "c=sqlite3.connect(sys.argv[1]); "
        "c.execute('DELETE FROM terms WHERE category = \\\'item\\\''); "
        "c.commit()\" \"$candidate\"\n"
        "    fi\n"
        "    ;;\n"
        "  *'verify-db'*)\n"
        "    candidate=''\n"
        "    for arg in \"$@\"; do\n"
        "      case \"$arg\" in WUWATERM_DB_PATH=*) candidate=${arg#WUWATERM_DB_PATH=} ;; esac\n"
        "    done\n"
        "    candidate=$FAKE_DEPLOY_ROOT/data/${candidate#/app/data/}\n"
        "    python3 \"$FAKE_DEPLOY_ROOT/scripts/verify_db.py\" \"$candidate\"\n"
        "    ;;\n"
        "  *' up -d '*wuwaterm*)\n"
        "    case \"${WUWATERM_RUNTIME_IMAGE:-}\" in\n"
        f"      *rollback-*) echo '{OLD_IMAGE}' > \"$FAKE_DEPLOY_ROOT/running-image\" ;;\n"
        f"      *) echo '{NEW_IMAGE}' > \"$FAKE_DEPLOY_ROOT/running-image\" ;;\n"
        "    esac\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_script.chmod(0o755)

    python_script = fake_bin / "python3"
    python_script.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        f"real_python={shlex.quote(sys.executable)}\n"
        "case \"$*\" in\n"
        "  *'deployment_manifest.py durable-replace --source data/candidates/'*)\n"
        "    if [ \"${FAKE_DB_PROMOTION_PRE_REPLACE_FAILURE:-0}\" = 1 ]; then\n"
        "      exit 94\n"
        "    fi\n"
        "    \"$real_python\" \"$@\"\n"
        "    if [ \"${FAKE_DB_PROMOTION_DURABILITY_FAILURE:-0}\" = 1 ]; then\n"
        "      exit 95\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        f"  *'deployment_manifest.py publish-pointer --path .deploy_commit --source-commit {OLD_COMMIT}'*)\n"
        "    \"$real_python\" \"$@\"\n"
        "    if [ \"${FAKE_POINTER_ROLLBACK_DURABILITY_FAILURE:-0}\" = 1 ]; then\n"
        "      echo 'injected pointer rollback durability failure' >&2\n"
        "      exit 92\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "  *'deployment_manifest.py durable-replace --source data/terms.db.rollback.'*)\n"
        "    \"$real_python\" \"$@\"\n"
        "    if [ \"${FAKE_DB_ROLLBACK_DURABILITY_FAILURE:-0}\" = 1 ]; then\n"
        "      echo 'injected rollback durability failure' >&2\n"
        "      exit 93\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        f"  *'deployment_manifest.py publish-pointer --path .deploy_commit --source-commit {NEW_COMMIT}'*)\n"
        "    \"$real_python\" \"$@\"\n"
        "    if [ \"${FAKE_POINTER_DURABILITY_FAILURE:-0}\" = 1 ]; then\n"
        "      exit 96\n"
        "    fi\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exec \"$real_python\" \"$@\"\n",
        encoding="utf-8",
    )
    python_script.chmod(0o755)
    (root / "running-image").write_text(f"{OLD_IMAGE}\n", encoding="ascii")

    old_hash = hashlib.sha256(old_db.read_bytes()).hexdigest()
    new_hash = hashlib.sha256(seed_db.read_bytes()).hexdigest()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["WUWATERM_DEPLOY_ROOT"] = str(root)
    env["FAKE_DEPLOY_ROOT"] = str(root)
    return root, env, old_hash, new_hash


@pytest.mark.parametrize(
    "failure_step",
    (
        "builder_image",
        "refresh",
        "build",
        "verify",
        "image",
        "backup",
        "state",
        "start",
        "smoke",
        "manifest",
        "pointer",
    ),
)
def test_vps_update_failure_injection_preserves_previous_binding(
    deploy_harness, failure_step
):
    root, env, old_hash, _new_hash = deploy_harness
    env["WUWATERM_FAIL_STEP"] = failure_step

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 97, result.stderr
    assert hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest() == old_hash
    assert (root / ".deploy_commit").read_text(encoding="ascii") == f"{OLD_COMMIT}\n"
    assert (root / "running-image").read_text(encoding="ascii").strip() == OLD_IMAGE
    assert list((root / "data" / "deployment-backups").glob("*.deploy_commit"))


@pytest.mark.parametrize(
    ("failure_env", "returncode"),
    (
        ("FAKE_DB_PROMOTION_PRE_REPLACE_FAILURE", 94),
        ("FAKE_DB_PROMOTION_DURABILITY_FAILURE", 95),
        ("FAKE_POINTER_DURABILITY_FAILURE", 96),
    ),
)
def test_vps_update_rolls_back_after_replace_before_durability_confirmation(
    deploy_harness, failure_env, returncode
):
    root, env, old_hash, _new_hash = deploy_harness
    env[failure_env] = "1"

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == returncode, result.stderr
    assert (
        hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest()
        == old_hash
    )
    assert (root / ".deploy_commit").read_text(encoding="ascii") == f"{OLD_COMMIT}\n"
    assert (root / "running-image").read_text(encoding="ascii").strip() == OLD_IMAGE


def test_vps_update_surfaces_rollback_durability_failure(deploy_harness):
    root, env, old_hash, _new_hash = deploy_harness
    env["FAKE_DB_PROMOTION_DURABILITY_FAILURE"] = "1"
    env["FAKE_DB_ROLLBACK_DURABILITY_FAILURE"] = "1"

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 93
    assert "injected rollback durability failure" in result.stderr
    assert (
        hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest()
        == old_hash
    )


def test_vps_update_reports_pointer_rollback_durability_failure(deploy_harness):
    root, env, old_hash, _new_hash = deploy_harness
    env["FAKE_POINTER_DURABILITY_FAILURE"] = "1"
    env["FAKE_POINTER_ROLLBACK_DURABILITY_FAILURE"] = "1"

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 96
    assert "injected pointer rollback durability failure" in result.stderr
    assert "restored pointer durability could not be confirmed" in result.stderr
    assert (
        hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest()
        == old_hash
    )
    assert (root / ".deploy_commit").read_text(encoding="ascii") == f"{OLD_COMMIT}\n"
    assert (root / "running-image").read_text(encoding="ascii").strip() == OLD_IMAGE


def test_vps_update_removes_new_pointer_when_no_previous_pointer_existed(
    deploy_harness,
):
    root, env, old_hash, _new_hash = deploy_harness
    (root / ".deploy_commit").unlink()
    (root / ".deployments" / f"{OLD_COMMIT}.json").unlink()
    env["FAKE_POINTER_DURABILITY_FAILURE"] = "1"

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 96, result.stderr
    assert (
        hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest()
        == old_hash
    )
    assert not (root / ".deploy_commit").exists()
    assert (root / "running-image").read_text(encoding="ascii").strip() == OLD_IMAGE


def test_vps_update_success_publishes_verified_immutable_binding(deploy_harness):
    root, env, _old_hash, new_hash = deploy_harness

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest() == new_hash
    assert (root / ".deploy_commit").read_text(encoding="ascii") == f"{NEW_COMMIT}\n"
    assert (root / "running-image").read_text(encoding="ascii").strip() == NEW_IMAGE
    manifest_path = root / ".deployments" / f"{NEW_COMMIT}.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["source_commit"] == NEW_COMMIT
    assert payload["image"]["id"] == NEW_IMAGE
    assert payload["database"]["sha256"] == new_hash
    assert payload["database"]["provenance"]["source_commit"] == (
        "dae29691c04ef0f48d0810b5d244fb0b37288c60"
    )
    assert manifest_path.stat().st_mode & 0o222 == 0


def test_vps_update_does_not_promote_candidate_rejected_by_real_verifier(
    deploy_harness,
):
    root, env, old_hash, _new_hash = deploy_harness
    env["FAKE_CORRUPT_CANDIDATE"] = "1"

    result = subprocess.run(
        ["sh", str(root / "deploy" / "vps-update.sh")],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "missing or empty categories: item" in result.stderr
    assert hashlib.sha256((root / "data" / "terms.db").read_bytes()).hexdigest() == old_hash
    assert (root / ".deploy_commit").read_text(encoding="ascii") == f"{OLD_COMMIT}\n"
    assert (root / "running-image").read_text(encoding="ascii").strip() == OLD_IMAGE
