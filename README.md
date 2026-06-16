# WuWa Term Bot

Self-hosted Telegram bot for Wuthering Waves Chinese terms to exact official
English localization and term-locked sentence translation.

The bot is dictionary-first: exact database hits return the English string from
the local SQLite database byte-for-byte. Other Chinese input is translated
through an OpenAI-compatible endpoint after known DB terms are locked, so
official terms are not paraphrased.

## Data Source

Primary source:

- `https://github.com/Arikatsu/WutheringWaves_Data`
- pinned commit: `58ec43698d2b4e188cb285467ce1ae887612dd92`
- pinned version: `GameVer 3.4.0 | ResVer 3.4.13`

Fallback mirrors to try manually if the primary source is unavailable:

- `https://github.com/Dimbreath/WutheringData` is frozen at 3.1.0
  (`e9234ffe094b2d944d16b222d31102e8ab32d954`, 2026-03-13) and is kept only
  as a legacy fallback.

The active Arikatsu source profile uses sparse checkout for only:

- `BinData`
- `Textmaps`

Bulk TextMap data and generated `terms.db` are local artifacts and are ignored
by Git.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Build Dictionary

```powershell
.venv\Scripts\python -m wuwaterm.cli refresh-data --dest data\wutheringdata --profile arikatsu
.venv\Scripts\python -m wuwaterm.cli build-db --data-dir data\wutheringdata --db data\terms.db --profile arikatsu
.venv\Scripts\python scripts\verify_db.py data\terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location
```

## Lookup

```powershell
.venv\Scripts\python -m wuwaterm.cli lookup --db data\terms.db 声骸
.venv\Scripts\python -m wuwaterm.cli sentence --db data\terms.db "今汐装备了声骸"
```

## Telegram Bot

Create the bot and token in BotFather yourself, then run:

```powershell
$env:TELEGRAM_BOT_TOKEN="..."
$env:WUWATERM_DB_PATH="data\terms.db"
.venv\Scripts\python -m wuwaterm.cli bot
```

Commands:

- `/tr 声骸` returns `Echo`
- `/tr 今汐装备了声骸` translates the sentence after locking `Jinhsi` and `Echo`
- `/term 今汐` returns `Jinhsi`
- `/sentence 今汐装备了声骸` locks known DB terms before translation

Optional LLM environment variables:

- `WUWATERM_OPENAI_BASE_URL`, set explicitly to your LiteLLM gateway URL
- `WUWATERM_OPENAI_API_KEY`
- `WUWATERM_OPENAI_MODEL`
- `WUWATERM_RATE_LIMIT_PER_MINUTE`, default `10`
- `WUWATERM_GROUP_TR_REJECT_TEXT`, default is the bilingual two-line reply
  `仅群管理员可用 /tr` then `Only group admins can use /tr`
- `WUWATERM_PRIVATE_TR_REJECT_TEXT`, default is the bilingual two-line reply
  `此 bot 仅限群内由管理员使用` then `This bot can only be used by admins inside a group.`
- `WUWATERM_TR_REJECT_SILENT`, default `0`; set `1` to drop unauthorized
  `/tr` calls without replying
- `OWNER_USER_ID`, no default; the only Telegram user id allowed to use
  `/tr` in private chat — missing or empty means private `/tr` rejects
  everyone (fail-closed) and a startup warning is logged
- `WUWATERM_CHANNEL_AUTOTRANSLATE`, default on; set `0`/`false`/`no`/`off`
  to disable the linked-channel auto-translation listener (kill switch)
- `WUWATERM_CHANNEL_MIN_CJK`, default `1`; minimum number of CJK
  ideographs a channel post must contain to be auto-translated
- `WUWATERM_CHANNEL_TEXT_LIMIT`, default `4096`; max text-post length for
  auto-translation (Telegram's own text limit)
- `WUWATERM_CHANNEL_CAPTION_LIMIT`, default `1024`; max caption length for
  auto-translation (Telegram's own caption limit)
- `WUWATERM_CHANNEL_MAX_AGE_SECONDS`, default `300`; channel posts older
  than this are never auto-translated — guards against Telegram update
  replays (restart backlog, bot admin promotion) translating channel
  history
- `WUWATERM_SOURCE_PROFILE`, default `arikatsu`; supported profiles are listed
  by `refresh-data --help` and `build-db --help`

No Telegram token, LLM key, endpoint, or model is hardcoded.

### Group Chats

In groups, slash commands work with Telegram privacy mode left on. The bot
does not listen to free-text messages from members; the only passive listener
is the linked-channel auto-translation described below — note that receiving
those channel auto-forwards at all requires the bot to be a discussion-group
admin (see that section).

- All translate commands — `/tr`, `/term`, `/sentence`, `/sent` — share one
  authorization gate and are admin-only in groups: each call resolves the
  sender via `getChatMember`, and only `creator`/`administrator` may use
  them. Anonymous group admins (posting as the group itself) are allowed.
  Membership verdicts are cached about 5 minutes per (chat, user).
- Unauthorized callers get a short bilingual reply (Chinese line then
  English), default `仅群管理员可用 /tr` then `Only group admins can use /tr`;
  the wording is configurable, and a config flag switches to silent ignore
  (default replies). Rejected calls never invoke the LLM but still consume
  the per-chat throttle budget.
- Authorized `/tr 声骸` and `/tr@<botusername> 声骸` return dictionary hits;
  `/tr <Chinese sentence>` translates with DB terms locked.
- Group replies quote the asking message.
- Private chat: all translate commands answer only the configured owner
  user id; everyone else gets a short bilingual reply, default
  `此 bot 仅限群内由管理员使用` then `This bot can only be used by admins inside a group.`
  With the owner id unset, private chat rejects everyone (fail-closed).
  Channel-type chats are rejected entirely.
- Per-chat throttling defaults to 10 lookups per minute.
- LLM-path input is capped at 1000 characters.

### Linked-Channel Auto-Translation

When the channel linked to a group posts, Telegram auto-forwards the post
into the group. The bot detects Chinese content in that forward and replies
in-thread with an English translation that preserves the original Telegram
formatting (bold, links, spoilers, ...). No command is involved.

- Trigger (hard boundary): only automatic forwards whose sender is a
  channel (`is_automatic_forward` + channel `sender_chat`). Ordinary member
  messages, manual forwards of channel posts, and anonymous-admin posts
  never trigger it. No authorization gate applies: posting rights in the
  linked channel are already owner-controlled.
- Posts without Chinese characters are skipped silently with zero LLM
  calls and zero throttle consumption. The minimum CJK ideograph count is
  configurable (`WUWATERM_CHANNEL_MIN_CJK`).
- The reply uses Telegram HTML (`parse_mode=HTML`) rendered from the
  post's entities, with DB terms locked before the LLM call. If the
  translated markup fails validation against Telegram's HTML subset, the
  bot strips the tags and sends a plain-text reply instead — formatting
  never fails the reply.
- Dictionary-first still applies: a post that is exactly one official term
  gets the official English string byte-for-byte, plain, without the LLM.
- Caption posts (photo/video announcements) are handled the same as text
  posts. Length caps are Telegram's own limits (4096 text / 1024 caption)
  instead of the 1000-char command cap.
- The per-chat throttle is shared with the slash commands. Throttle denials
  and budget exhaustion on this path skip silently with one log line — no
  notice comment under the post (command paths keep their visible notices).
- Kill switch: `WUWATERM_CHANNEL_AUTOTRANSLATE`, default on.
- Freshness gate: posts older than `WUWATERM_CHANNEL_MAX_AGE_SECONDS`
  (default 300) are skipped silently. Telegram replays updates — restart
  backlog, or a burst of recent group history when the bot is promoted to
  admin — and without this gate the bot would translate channel history.
- Edited posts update the existing reply in place: when the linked channel
  edits a post, the bot re-translates and edits the reply it already sent for
  that post rather than adding a second one. The post-to-reply map is held in
  memory, so an edit with no tracked reply — after a bot restart, for a post
  that was never translated, or an edit made after the freshness window — is
  skipped silently; an edit never produces a duplicate reply.
- Delivery precondition: Telegram privacy mode withholds channel
  auto-forwards from non-admin bots (slash commands still arrive). Make
  the bot an admin of the discussion group (any single right suffices);
  the alternative — disabling privacy mode via BotFather — also requires
  removing and re-adding the bot to the group.

## VPS Docker Compose

The VPS target uses Docker Compose because the current system Python there is
older than the project target. Copy the repo to `/opt/wuwaterm/current`, create
`/opt/wuwaterm/current/.env` from `deploy/env.example`, and set it to mode
`600`. Secrets are injected only through Compose `env_file`; `.env` is ignored
and excluded from the image build context.

Prepare or refresh data without starting the service:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml run --rm wuwaterm refresh-data
docker compose -f deploy/docker-compose.yml run --rm wuwaterm build-db
docker compose -f deploy/docker-compose.yml run --rm wuwaterm verify-db
```

For each real game-version refresh, pick at least one term that exists only in
the new game data and run a live `/tr <term>` check in Telegram after the DB
build. Counts and hashes prove rebuild mechanics; a new-term live check proves
the running bot is serving the refreshed content.

The compose service uses long polling (`wuwaterm bot`) and
`restart: unless-stopped`. No webhook, inline mode, or alias layer is configured.
Starting the service is owner-gated:

```bash
cd /opt/wuwaterm/current
docker compose -f deploy/docker-compose.yml up -d
```

## Validation

```powershell
.venv\Scripts\python scripts\verify_seed_terms.py data\terms.db --discrepancies goal-runs\wuwaterm-v2-translator\seed-discrepancies.json
.venv\Scripts\python scripts\verify_exact_hits.py data\terms.db --sample-size 500
.venv\Scripts\python scripts\verify_idempotent_build.py --data-dir data\wutheringdata --out-dir goal-runs\wuwaterm-v2-translator --profile arikatsu
.venv\Scripts\python scripts\check_repo_hygiene.py
pytest
```

`verify_idempotent_build.py` compares SHA256 over LF-normalized SQLite logical
dumps, not raw database bytes, so Windows/Linux SQLite formatting differences
do not create false mismatches.

Live Telegram smoke is owner-gated. If `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_TEST_CHAT_ID` are not supplied, only the live smoke criterion is
blocked; offline handler tests still validate the bot code.
