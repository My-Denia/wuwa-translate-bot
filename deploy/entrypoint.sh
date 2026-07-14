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
  *)
    echo "runtime image only supports the Telegram bot; use the builder image for data commands" >&2
    exit 64
    ;;
esac
