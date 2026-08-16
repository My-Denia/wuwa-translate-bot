#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for wuwaterm.
#
# Installs the project's pinned toolchain (uv) and the locked dependency set
# into a project-local virtualenv. Kept minimal and idempotent: it only
# prepares durable, source-derived state (dependencies) and starts no service.
# The dictionary database and any running bot/API server are dev tasks left to
# the documented commands in README.md / docs/, not to environment setup.
set -euo pipefail

# uv is pinned to the same version the deploy images and CI use
# (deploy/Dockerfile, .github/workflows/ci.yml).
UV_VERSION="0.11.3"

# Prefer a user-site install so no root privileges are needed; ~/.local/bin is
# added to PATH by the login shell's ~/.profile, so uv is available to future
# interactive sessions too. The fallback keeps --user (so it stays writable
# without root) and only adds --break-system-packages for images whose system
# Python is externally managed (PEP 668): --break-system-packages permits
# modifying an externally managed install but does not by itself select the
# user scope, so both flags are needed together.
if ! python3 -m pip install --user --quiet "uv==${UV_VERSION}"; then
  python3 -m pip install --user --break-system-packages --quiet "uv==${UV_VERSION}"
fi
export PATH="${HOME}/.local/bin:${PATH}"

# Create the project virtualenv if it does not already exist, then install the
# locked dependency graph plus the dev extra (pytest, FastAPI, uvicorn,
# pypinyin). --locked fails closed if uv.lock is out of date rather than
# silently resolving something new.
test -x .venv/bin/python || uv venv .venv
uv sync --locked --extra dev

echo "wuwaterm environment ready: $(.venv/bin/python --version)"
