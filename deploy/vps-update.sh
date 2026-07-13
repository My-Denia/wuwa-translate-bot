#!/bin/sh
set -eu

cd /opt/wuwaterm/current

env_mode="$(stat -c '%a' .env)"
if [ "$env_mode" != "600" ]; then
  echo "refusing to use /opt/wuwaterm/current/.env with mode $env_mode; expected 600" >&2
  exit 1
fi

docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder build-db --atomic
docker compose -f deploy/docker-compose.yml run --rm wuwaterm-builder verify-db
sha256sum data/terms.db

runtime_stopped=0
restart_runtime_on_failure() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$runtime_stopped" -eq 1 ]; then
    docker compose -f deploy/docker-compose.yml start wuwaterm >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap restart_runtime_on_failure EXIT

# Freeze the legacy state before its one-time copy. Building the DB above can
# run while the old bot is live; this final migration window cannot.
docker compose -f deploy/docker-compose.yml stop wuwaterm
runtime_stopped=1

mkdir -p state
for state_file in chat_settings.json channel_replies.json; do
  if [ -f "data/$state_file" ] && [ -e "state/$state_file" ]; then
    python3 scripts/validate_state_file.py "state/$state_file" "$state_file" >/dev/null || {
      echo "refusing to continue with invalid state/$state_file while data/$state_file exists" >&2
      exit 1
    }
  fi
  if [ -f "data/$state_file" ] && [ ! -e "state/$state_file" ]; then
    tmp="state/.$state_file.migrate.$$"
    cp -p "data/$state_file" "$tmp"
    chmod 600 "$tmp"
    python3 scripts/validate_state_file.py "$tmp" "$state_file" >/dev/null || {
      echo "refusing to migrate invalid data/$state_file" >&2
      rm -f "$tmp"
      exit 1
    }
    if ! ln "$tmp" "state/$state_file" 2>/dev/null; then
      python3 scripts/validate_state_file.py "state/$state_file" "$state_file" >/dev/null || {
        echo "refusing to continue with invalid state/$state_file" >&2
        rm -f "$tmp"
        exit 1
      }
    fi
    rm -f "$tmp"
  fi
done

docker compose -f deploy/docker-compose.yml up -d --build wuwaterm
runtime_stopped=0
trap - EXIT
