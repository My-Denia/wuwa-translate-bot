# ADR 0005: File-backed single-instance state

- Status: Accepted
- Date: 2026-08-05

## Context

The bot needs durable per-chat allowlist/public flags and a channel reply index
(chat/message id mappings for edit/delete). Traffic is single-VPS scale. Redis,
Postgres, or an external lock service would add operational cost without a
current multi-host requirement.

## Decision

Persist mutable runtime state as JSON files under `state/`:

- `chat_settings.json` — `ChatSettings` with process `RLock` and sibling file
  lock (`fcntl` / `msvcrt`)
- `channel_replies.json` — `ChannelReplyIndex` with atomic replace and
  per-message asyncio edit locks

Process-local structures (`ChannelRuntime`, rate limiters, admin cache) stay in
memory and reset on restart.

## Consequences

- Positive: simple backup/migrate story; Compose volume is obvious.
- Positive: hygiene gate blocks committing these files
  (`scripts/check_repo_hygiene.py`).
- Negative: dual active processes can split-brain logically even when individual
  writes are locked; multi-instance remains unsupported
  (`docs/architecture.md`).
- Negative: in-memory budgets/dedup do not survive restart or share across hosts.

## Evidence

- `src/wuwaterm/settings.py` `_file_lock`, `ChatSettings`
- `src/wuwaterm/channel_reply_index.py` atomic save / `edit_delivery_lock`
- `deploy/docker-compose.yml` `WUWATERM_STATE_DIR`, `state/` volume
- `docs/deployment.md` state migration rules
