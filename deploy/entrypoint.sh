#!/bin/sh
set -eu

DB_PATH="${WUWATERM_DB_PATH:-/app/data/terms.db}"
COMMAND="${1:-bot}"
if [ "$#" -gt 0 ]; then
  shift
fi

case "$COMMAND" in
  bot)
    exec python -m wuwaterm.cli bot --db "$DB_PATH" "$@"
    ;;
  api)
    # Serving surface, same image, same read-only database. It binds loopback
    # by default (WUWATERM_API_BIND) and never gains a data-build command.
    WUWATERM_DB_PATH="$DB_PATH" exec python -m wuwaterm_api.cli serve "$@"
    ;;
  device)
    # Operator-only credential management, run as a one-shot container.
    exec python -m wuwaterm_api.cli device "$@"
    ;;
  *)
    echo "runtime image only serves the bot and api surfaces; use the builder image for data commands" >&2
    exit 64
    ;;
esac
