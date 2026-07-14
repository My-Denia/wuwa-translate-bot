from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


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

    refresh = text.index("wuwaterm-builder refresh-data")
    build = text.index("wuwaterm-builder build-db --atomic")
    verify = text.index("wuwaterm-builder verify-db")
    stop = text.index("stop wuwaterm")
    migrate = text.index("for state_file in chat_settings.json channel_replies.json")
    start = text.index("up -d --build wuwaterm")

    assert refresh < build < verify < stop < migrate < start
    assert "restart_runtime_on_failure" in text
    assert "start wuwaterm" in text


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


def test_compose_uses_readonly_data_for_runtime_and_writable_data_for_builder():
    text = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "target: runtime" in text
    assert "target: builder" in text
    assert "wuwaterm-runtime:local" in text
    assert "wuwaterm-builder:local" in text
    assert "../data:/app/data:ro" in text
    assert "../state:/app/state" in text
    assert "WUWATERM_STATE_DIR: /app/state" in text
    assert "WUWATERM_SETTINGS_PATH: /app/state/chat_settings.json" in text
    assert (
        "WUWATERM_CHANNEL_REPLY_INDEX_PATH: /app/state/channel_replies.json"
        in text
    )
    assert "../data:/app/data\n" in text
