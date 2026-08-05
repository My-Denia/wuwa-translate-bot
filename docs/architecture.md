# Architecture

Maintainer entry for the **current** production Telegram translation bot
(`wuwaterm`). Claims map to source, tests, deploy scripts, and Compose
topology. This is not an aspirational redesign.

Operational detail lives in sibling guides. Decision rationale lives in
[ADRs](adr/). Automated import-direction and non-goal gates live under
`scripts/` and CI.

## System context

```
Telegram users / groups / linked discussion groups
        │
        │  Bot API (long polling getUpdates)
        ▼
┌───────────────────────────────────────────────┐
│  Single runtime process (Compose: wuwaterm)   │
│  presentation: bot.py, channel.py             │
│  domain: lookup, sentence, normalize, models  │
│  infra: settings JSON, reply index, SQLite RO │
└───────────────┬───────────────┬───────────────┘
                │               │
                │ read-only     │ HTTP (optional)
                ▼               ▼
         data/terms.db    OpenAI-compatible LLM
                ▲
                │ promote only via deploy/vps-update.sh
┌───────────────┴───────────────────────────────┐
│  Builder jobs (Compose profile: builder)      │
│  refresh-data → build-db → verify-*           │
│  never holds runtime secrets (no env_file)    │
└───────────────────────────────────────────────┘
```

### Users and actors

| Actor | Role | Evidence |
|-------|------|----------|
| Owner (`OWNER_USER_ID`) | Private `/tr`, `/authorize` / `/revoke`, `/public`, `/status` | `bot.py` `_is_owner`, `authorize_command`, `public_command` |
| Group admins | `/tr` when chat is allowlisted (unless public mode) | `bot.py` `_is_authorized_group_sender`, `ChatSettings` |
| Group members | `/tr` only when public mode is on for that chat | `settings.py` public map; `docs/telegram-behavior.md` |
| Linked channel posts | Auto-translated into the discussion group when gated | `channel.py` `channel_post_handler`; single channel auto-forward listener pin in `bot.py` |
| Operator on VPS | Deploy, data refresh, Compose up/down | `deploy/vps-update.sh`, `docs/deployment.md` |

### External systems

| System | Direction | Purpose |
|--------|-----------|---------|
| Telegram Bot API | Outbound long polling + send/edit/delete | Presentation only |
| OpenAI-compatible HTTP API | Outbound when LLM configured and dictionary miss | Sentence translation after term lock |
| GitHub game-data repos | Builder sparse checkout only | Source for `terms.db` (`data_source.py`, `constants.py` pins) |
| Local Docker Compose host | Runtime + optional builder jobs | Supported single-host topology |

### Trust boundaries

1. **Telegram update surface is untrusted input.** Command text, replied message
   HTML, and channel posts are treated as data, not instructions
   (`docs/privacy-and-llm.md`; sentence system prompt + HTML protect/restore).
2. **Secrets stay outside the image and the builder.** Runtime gets
   `TELEGRAM_BOT_TOKEN` / LLM keys via Compose `env_file`; builder has no
   `env_file` (`deploy/docker-compose.yml`, `docs/deployment.md`).
3. **Terminology DB is trusted after candidate verification**, then mounted
   read-only at runtime (`/app/data:ro`). Promotion is owner-scripted, not
   in-process self-update.
4. **Mutable allowlist / reply-index state is local and private.** Paths under
   `state/`; must not be committed (`scripts/check_repo_hygiene.py`).
5. **No public HTTP admin or webhook listener.** Inbound control plane is the
   Telegram Bot API only, plus operator SSH to the VPS.

## Telegram as presentation layer

Telegram is the UI and transport, not the domain model.

| Presentation | Responsibility | Module |
|--------------|----------------|--------|
| Command bot | Handlers, auth, rate limits, replies, polling bootstrap | `src/wuwaterm/bot.py` |
| Linked channel | Admission, translate, deliver/edit, flood retry | `src/wuwaterm/channel.py` |
| HTML/text helpers | Protect/validate Telegram HTML; chunk limits | `telegram_html.py`, `telegram_text.py` |

Domain (`lookup`, `normalize`, `models`, term-lock policy in `sentence`) and
storage (`db` reads, `settings`, `channel_reply_index`) must remain usable from
CLI and tests without a live Telegram session. See [ADR 0001](adr/0001-telegram-as-presentation-layer.md).

## Component map and dependency direction

Intended layers (modular monolith — [ADR 0002](adr/0002-modular-monolith.md)):

```
cli (bootstrap)
  ├─► bot / channel          presentation (python-telegram-bot)
  │     ├─► lookup / sentence / normalize / translation_policy   domain
  │     ├─► settings / channel_reply_* / channel_runtime         local infra
  │     └─► telegram_html / telegram_text                        presentation helpers
  ├─► builder / data_source / db (write) / build_pinyin           builder path
  └─► lookup / sentence / db (read)                              offline CLI
```

| Layer | Modules | Must not import |
|-------|---------|-----------------|
| Domain core | `lookup`, `normalize`, `models` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Domain + provider | `sentence` | `bot`, `channel` (may use `telegram_html` for HTML term-lock); builder-only modules |
| Shared policy | `translation_policy`, `runtime_keys`, `constants` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Presentation | `bot`, `channel`, `telegram_html`, `telegram_text` | `builder`, `data_source`, `build_pinyin`, bootstrap `cli` |
| Local state | `settings`, `channel_reply_index`, `channel_reply_schema`, `channel_runtime` | presentation / Telegram SDK (including under `TYPE_CHECKING`); builder-only modules |
| Storage | `db` | presentation / Telegram SDK; `build_pinyin` only inside write helpers (lazy) |
| Builder | `builder`, `data_source`, `build_pinyin` | `bot`, `channel` |
| Bootstrap | `cli` | may wire both runtime and builder entrypoints |

Observed edges (static imports among `src/wuwaterm/*.py`):

- `bot` → `channel` (handler registration + flood retry); `channel` → `bot`
  only under `TYPE_CHECKING` for `BotConfig` (no runtime cycle).
- `sentence` → `lookup`, `normalize`, `telegram_html`, `httpx`.
- `lookup` → `db` (read helpers), `models`, `normalize`, `constants`.
- `db.insert_records` lazily imports `build_pinyin` (builder-only dependency).
- Runtime image refuses `build-db` and excludes builder tooling
  (`tests/test_runtime_imports.py`, CI `deploy-boundary` job).

Automated enforcement: `scripts/check_architecture_boundaries.py` plus existing
`scripts/check_non_goals.py` and `tests/test_runtime_imports.py`.

### Current coupling (honest)

| Coupling | Status | Notes |
|----------|--------|-------|
| Large `bot.py` (config, auth, rate limit, translate orchestration, migration, polling) | Accepted concentration | Documented; not mass-split in this formalization |
| `translate_query*` lives in presentation module | Accepted | Domain still reached via `TermService` / `SentenceTranslator`; CLI uses the same domain modules |
| `channel` TYPE_CHECKING-imports `BotConfig` from `bot` | Low aesthetic cycle | Runtime import graph is one-way; extraction deferred unless a check requires it |
| `sentence` → `telegram_html` | Accepted | HTML term-lock is part of Telegram-HTML translation fidelity |
| `db` top-level imports `data_source.SourceProvenance` | Mild storage/builder type coupling | Provenance metadata is shared with build path |

## Request flows

### Command `/tr` (and `/term`, `/sentence` / `/sent`)

Evidence: `bot.py` `create_application`, `_translation_command`,
`translate_request_async` / `translate_query_async`; tests under `tests/test_bot.py`.

1. `run_bot` → `create_application` → `app.run_polling()` ([ADR 0003](adr/0003-long-polling-not-webhook.md)).
2. `CommandHandler` for `tr`/`term` or `sentence`/`sent` → `_translation_command`.
3. **Auth**: owner private chat, or group admin (or public mode) + allowlist
   (`_translation_actor_or_reject`, `ChatSettings`).
4. **Rate limit**: per-chat `PerChatRateLimiter`.
5. Parse args / optional `--to` / replied text; invalid `--to` → usage, **no LLM**.
6. `prepare_text` → `lookup_exact`:
   - exact hit → official string, **no LLM**;
   - short ASCII fuzzy dictionary short-circuit → **no LLM** (`_fuzzy_dictionary_answer`).
7. Length gate (`translation_policy` / config limits).
8. `SentenceTranslator`: lock DB terms → optional LLM → restore placeholders.
9. `reply_to_user` (HTML with plain fallback; flood retry via channel helper).

### Linked-channel auto-translation

Evidence: `channel.py` `channel_post_handler`; single listener pin in
`scripts/check_non_goals.py` / `bot.py`.

1. Update matches `filters.IS_AUTOMATIC_FORWARD & filters.SenderChat.CHANNEL`.
2. Age / size / CJK-Latin thresholds; kill switch `WUWATERM_CHANNEL_AUTOTRANSLATE`.
3. Auth: discussion group must still satisfy allowlist gate.
4. `ChannelRuntime.reserve` (process-local admission / budget).
5. Claim original in `ChannelReplyIndex` (file-backed).
6. Translate: exact DB / term lock / LLM with re-gates before send.
7. Deliver reply or edit existing chunks with `send_with_flood_retry`.
8. Telemetry outcomes via channel runtime counters.

Channel path is always auto-detected direction; it does not accept command
`--to` flags (`docs/telegram-behavior.md`).

### Paths that never call the LLM

| Path | Why |
|------|-----|
| Exact dictionary hit (`lookup_exact` + early returns in `translate_query*`, channel exact branch) | Official string from SQLite |
| Fuzzy dictionary short answers (`_fuzzy_dictionary_answer`) | DB-only |
| Invalid leading `--to` | Usage reply only |
| Unauthorized / rate-limited / silent reject | No translation work |
| Channel below CJK/Latin thresholds, stale posts, admission reject, kill switch off | Skipped before translate |
| LLM env incomplete | `SentenceTranslator` restores locked placeholders / fails closed without inventing terms (`sentence.py` `_llm_configured`) |
| `/about`, `/status`, authorize/revoke/public membership housekeeping | No translation |

## Data model: immutable vs mutable

| Kind | Location | Mutability | Notes |
|------|----------|------------|-------|
| Terminology SQLite | `data/terms.db` (Compose: `/app/data` **ro**) | Immutable at runtime | Built offline; promoted by `deploy/vps-update.sh` ([ADR 0004](adr/0004-sqlite-terminology-data.md), [ADR 0008](adr/0008-candidate-verification-and-transactional-deployment.md)) |
| Chat allowlist + public mode | `state/chat_settings.json` | Mutable | Process `RLock` + `fcntl`/`msvcrt` file lock ([ADR 0005](adr/0005-file-backed-single-instance-state.md)) |
| Channel reply index | `state/channel_replies.json` | Mutable | Atomic replace + asyncio edit locks |
| Channel admission / budgets | `ChannelRuntime` in process memory | Mutable, lost on restart | Not shared across processes |
| Rate limiters, admin cache | Process memory in `bot.py` | Mutable, lost on restart | |
| Source pins / profile | `constants.py` + DB metadata | Immutable until rebuild | |
| Secrets | Host `.env` mode `600` | Operator-managed | Not in image; not in builder |

## Single-instance assumptions

Supported topology (also [supported vs unsupported](#supported-vs-unsupported-topology)):

- One Bot token (`TELEGRAM_BOT_TOKEN`).
- One active long-polling runtime container (`container_name: wuwaterm-bot`).
- One Compose host; `network_mode: host`.
- Builder is profile-gated one-shot work, not a second always-on replica.

### Dual-instance failure modes (unsupported)

If two processes share the same token and state files:

1. **getUpdates contention** — Telegram effectively serves one consumer; offsets
   race and updates are lost or double-handled.
2. **Allowlist / public split-brain** — file locks reduce corrupt JSON writes
   but not logical divergence between two writers with interleaved intent.
3. **Double channel work** — `ChannelRuntime` budgets and in-memory dedup are
   process-local → duplicate LLM spend and duplicate replies.
4. **Reply-index races** — claim/edit windows can conflict across processes even
   when individual file replaces are atomic.

Docs and ops must not treat multi-instance as HA. File locks are durability
aids for a single writer, not a cluster protocol.

## Failure propagation

| Dependency | Failure mode | User-visible / recovery |
|------------|--------------|-------------------------|
| Telegram API | NetworkError, RetryAfter, BadRequest | Flood retry helper; HTML parse fallback to plain; missing reply target handling; process keeps polling (`restart: unless-stopped`) |
| LLM HTTP | Timeout, 4xx/5xx, empty content | `LLMTranslationError` mapped to safe user message; placeholders not left raw when restore fails closed |
| SQLite terms DB | Missing/corrupt file | Startup/use fails when `TermService` cannot read; deploy keeps previous DB on failed promotion |
| Filesystem state | Disk full, lock errors, durability errors | Settings/reply-index raise typed errors; startup migration refuses overwrite of existing targets |
| Docker / Compose | Image/build failure, wrong revision | `vps-update.sh` aborts before stop, or rolls back image + DB + pointer after failed post-promote steps |

No automatic cross-region failover. Recovery is restart, restore from
timestamped backups under `data/deployment-backups`, or re-run transactional
deploy after fixing the root cause.

## Data refresh and transactional deploy

From repo scripts and docs only (no live VPS mutation required to understand):

1. **Refresh** — `wuwaterm-builder refresh-data` sparse-checkouts pinned source
   (`data_source.refresh_data`, `docs/data-refresh.md`).
2. **Build candidate** — `build-db --atomic` → unique path under
   `data/candidates/` (updater), not live `terms.db`.
3. **Verify** — `verify-db`, seed/exact/idempotent scripts as wired in
   `deploy/vps-update.sh` and `docs/validation.md`.
4. **Build immutable runtime image** labeled with source commit revision.
5. **Promote** — stop runtime, swap DB, start exact image, smoke
   (`scripts/deploy_smoke.py` with diagnostic send disabled in updater path),
   write immutable `.deployments/<commit>.json`, atomic `.deploy_commit`.
6. **Rollback** — on post-promote failure restore previous DB, rollback image
   tag, and pointer (`deploy/vps-update.sh` `rollback_on_failure`).

Runtime vs builder separation: [ADR 0007](adr/0007-runtime-builder-separation.md).
Dictionary-before-LLM: [ADR 0006](adr/0006-dictionary-first-before-llm.md).

## Conditions that would justify later extensions

These are **not** current capabilities. They would need a new goal, ADRs, and
likely product/ops changes:

| Extension | Real trigger (examples) |
|-----------|-------------------------|
| Web admin | Operators need bulk allowlist/audit without Telegram commands; multi-owner RBAC |
| Multi-instance / external state | Sustained load exceeds one process **and** Telegram delivery model is redesigned (e.g. webhook + shared lease); shared durable admission for channels |
| Postgres / Redis / queue | State or job volume exceeds single-host JSON/SQLite operational comfort; multi-host deploy becomes a requirement |
| API gateway | Non-Telegram clients become first-class; auth model expands beyond Bot token + chat admin |

Until those triggers are real, adding the machinery would be ceremony.

## Supported vs unsupported topology

| Supported | Unsupported |
|-----------|-------------|
| Single VPS, Compose, long polling | Webhook HA, multi-replica polling |
| Single bot token, one active runtime | Multi-token shard, active-active |
| `data/terms.db` RO + `state/*.json` RW | Runtime writing game TextMaps or self-promoting DB |
| Builder one-shot jobs | Always-on builder with runtime secrets |
| Dictionary-first + optional LLM | LLM-only glossary, secondary name-map layer, inline mode |
| Owner-gated deploy from clean `HEAD == origin/main` | Unverified candidate copy over live DB |

## Related documents

- [Deployment](deployment.md)
- [Telegram Behavior](telegram-behavior.md)
- [Privacy And LLM](privacy-and-llm.md)
- [Data Refresh](data-refresh.md)
- [Validation](validation.md)
- [ADR index](adr/README.md)
