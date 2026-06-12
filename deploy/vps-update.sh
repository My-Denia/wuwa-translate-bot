#!/bin/sh
set -eu

cd /opt/wuwaterm/current

env_mode="$(stat -c '%a' .env)"
if [ "$env_mode" != "600" ]; then
  echo "refusing to use /opt/wuwaterm/current/.env with mode $env_mode; expected 600" >&2
  exit 1
fi

docker compose -f deploy/docker-compose.yml run --rm wuwaterm refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm build-db
docker compose -f deploy/docker-compose.yml run --rm wuwaterm verify-db
sha256sum data/terms.db
