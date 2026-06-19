# WuWa Term Bot

Self-hosted Telegram bot for Wuthering Waves official localization: Chinese
terms to exact official English and the reverse, with term-locked sentence
translation in both directions.

The bot is dictionary-first: an exact database hit returns the official string
from the local SQLite database byte-for-byte. Direction is auto-detected by
script — a Chinese source translates to English (the default), an English/Latin
source translates to Chinese. Free text in either language goes through an
OpenAI-compatible endpoint after known DB terms are locked, so official terms
are restored verbatim in the target language rather than paraphrased.

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
by Git. This project does **not** redistribute Wuthering Waves game data; only a
small derived term dictionary is built locally from the public source above. All
Wuthering Waves game data and in-game terminology are © Kuro Games.

## Local Development

WSL/Linux is the primary development environment. Keep the checkout on the WSL
filesystem (for example under `~/projects/...`) so file watching,
permissions, line endings, and virtualenv scripts behave like Linux.

```bash
test -x .venv/bin/python || uv venv .venv
uv pip install -e ".[dev]"
```

Use `uv venv --clear .venv` only when intentionally rebuilding the local
virtualenv from scratch.

If the WSL image has `python3-venv` and pip installed, the standard-library
path also works:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```

## Build Dictionary

```bash
.venv/bin/python -m wuwaterm.cli refresh-data --dest data/wutheringdata --profile arikatsu
.venv/bin/python -m wuwaterm.cli build-db --data-dir data/wutheringdata --db data/terms.db --profile arikatsu
.venv/bin/python scripts/verify_db.py data/terms.db --min-category resonator --min-category weapon --min-category echo --min-category item --min-category skill --min-category sonata_effect --min-category location
```

## Lookup

```bash
.venv/bin/python -m wuwaterm.cli lookup --db data/terms.db 声骸
.venv/bin/python -m wuwaterm.cli sentence --db data/terms.db "今汐装备了声骸"
```

## Telegram Bot

Create the bot and token in BotFather yourself, then run:

```bash
export TELEGRAM_BOT_TOKEN="..."
export WUWATERM_DB_PATH="data/terms.db"
.venv/bin/python -m wuwaterm.cli bot
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
- `WUWATERM_CHANNEL_MIN_CJK`, default `1`; minimum number of CJK ideographs a
  channel post needs to be auto-translated Chinese -> English
- `WUWATERM_CHANNEL_MIN_LATIN`, default `2`; for a channel post with no CJK,
  the minimum number of Latin letters it needs to be auto-translated
  English -> Chinese (below both thresholds the post is skipped)
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
  authorization gate. In a group the chat must first be on the authorization
  allowlist (see below); commands in a non-authorized group are rejected
  outright, even for admins and even in public mode. Within an authorized group
  they are admin-only by default: each call resolves the sender via
  `getChatMember`, and only `creator`/`administrator` may use them. Anonymous
  group admins (posting as the group itself) are allowed. Membership verdicts
  are cached about 5 minutes per (chat, user). An admin may open an authorized
  chat to all members with `/public on` (see below).
- Unauthorized callers get a short bilingual reply (Chinese line then
  English), default `仅群管理员可用 /tr` then `Only group admins can use /tr`;
  the wording is configurable, and a config flag switches to silent ignore
  (default replies). Rejected calls never invoke the LLM but still consume
  the per-chat throttle budget.
- Authorized `/tr 声骸` and `/tr@<botusername> 声骸` return dictionary hits;
  `/tr <sentence>` translates with DB terms locked. Direction is auto-detected:
  Chinese input -> English, English input -> Chinese (`/tr Echo` returns `声骸`).
- Group replies quote the asking message.
- Private chat: all translate commands answer only the configured owner
  user id; everyone else gets a short bilingual reply, default
  `此 bot 仅限群内由管理员使用` then `This bot can only be used by admins inside a group.`
  With the owner id unset, private chat rejects everyone (fail-closed).
  Channel-type chats are rejected entirely.
- Per-chat throttling defaults to 10 lookups per minute.
- LLM-path input is capped at 1000 characters.

### Group Authorization / auto-leave (`/authorize`, `/revoke`)

The bot only stays in groups the owner has authorized. When it is added to a
chat that is not on the allowlist, it posts a short bilingual notice and then
leaves automatically — this keeps a public bot from being pulled into arbitrary
groups and abused. The allowlist is also the serving gate: translate commands
only run in a group that is on it, so even if the auto-leave or the
persisted-write fails, an unauthorized group still gets no translations
(fail-closed).

- The owner adding the bot to a group auto-authorizes that group (its id goes on
  the allowlist), so the owner can drop the bot into their own groups with no
  extra step.
- `/authorize` (owner only) — in a group, authorizes the current chat; in
  private chat, `/authorize <chat_id>` authorizes by id and `/authorize list`
  shows the allowlist.
- `/revoke` (owner only) — removes a chat from the allowlist (current chat in a
  group, or `/revoke <chat_id>` in private).
- Only the genuine "added" event triggers the leave. A promotion, demotion, or
  any status change inside a group the bot already belongs to never makes it
  leave, so an existing authorized group is safe.
- Requires `OWNER_USER_ID`. With it unset (fail-closed) the bot is not
  authorized to stay in any newly-added group.
- The allowlist is persisted in the same file as the `/public` state
  (`WUWATERM_SETTINGS_PATH`).

> First deploy into an existing group: the bot is already a member there, so it
> does not auto-leave (no "added" event fires) — but because serving is gated on
> the allowlist, `/tr` and the other commands will NOT respond there until the
> group is authorized. Run `/authorize` once inside that group (or
> `/authorize <chat_id>` from a private chat with the owner) to add it to the
> allowlist; translations resume immediately.

### Opening a Group to Non-Admins (`/public`)

Admins can open translate commands to everyone in a specific group with
`/public on`, and restrict them back to admins with `/public off`. The default
for every new chat is admin-only (no behavior change for groups that don't
opt in).

- `/public on` — open `/tr`, `/term`, `/sentence`, `/sent` to all members.
- `/public off` — restrict back to admins (default).
- `/public` or `/public status` — report the current state.
- `/public` is ALWAYS admin-only — public mode never unlocks the toggle
  itself, so a non-admin can never flip a public chat back off or on.
- Public mode only applies inside an authorized group; it never bypasses the
  authorization allowlist (an un-authorized group serves no one, public or not).
- Per-chat throttling and the 1000-char LLM cap still apply.
- State is persisted to `WUWATERM_SETTINGS_PATH` (default
  `<db parent>/chat_settings.json`); on the supported Docker layout this lives
  in the bind-mounted `data/` volume and survives image rebuilds.

### Linked-Channel Auto-Translation

When the channel linked to a group posts, Telegram auto-forwards the post
into the group. The bot auto-detects the post's language and replies in-thread
with a translation that preserves the original Telegram formatting (bold,
links, spoilers, ...): a Chinese post is translated to English, an English
post to Chinese. No command is involved.

- Trigger (hard boundary): only automatic forwards whose sender is a
  channel (`is_automatic_forward` + channel `sender_chat`). Ordinary member
  messages, manual forwards of channel posts, and anonymous-admin posts
  never trigger it.
- Authorization: the discussion group must be on the same authorization
  allowlist as the slash commands. An unauthorized or revoked group — including
  one the bot has not yet managed to leave (e.g. a `leave_chat` that failed) —
  gets no auto-translations and burns no LLM budget. (Posting rights in the
  linked channel are owner-controlled, but the allowlist is what bounds where
  the bot will spend tokens.)
- Direction by script: a post with enough Chinese (`WUWATERM_CHANNEL_MIN_CJK`,
  default 1) is translated to English; a post with no Chinese but enough Latin
  letters (`WUWATERM_CHANNEL_MIN_LATIN`, default 2) is translated to Chinese; a
  post with neither (emoji / links / numbers only) is skipped silently with
  zero LLM calls and zero throttle consumption.
- The reply uses Telegram HTML (`parse_mode=HTML`) rendered from the
  post's entities, with DB terms locked before the LLM call. If the
  translated markup fails validation against Telegram's HTML subset, the
  bot strips the tags and sends a plain-text reply instead — formatting
  never fails the reply.
- Dictionary-first still applies: a post that is exactly one official term
  gets the official string byte-for-byte (English for a Chinese term, Chinese
  for an English term), plain, without the LLM.
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

```bash
.venv/bin/python scripts/verify_seed_terms.py data/terms.db --discrepancies goal-runs/wuwaterm-v2-translator/seed-discrepancies.json
.venv/bin/python scripts/verify_exact_hits.py data/terms.db --sample-size 500
.venv/bin/python scripts/verify_idempotent_build.py --data-dir data/wutheringdata --out-dir goal-runs/wuwaterm-v2-translator --profile arikatsu
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/check_non_goals.py
.venv/bin/python -m pytest
```

`verify_idempotent_build.py` compares SHA256 over LF-normalized SQLite logical
dumps, not raw database bytes, so Windows/Linux SQLite formatting differences
do not create false mismatches.

Live Telegram smoke is owner-gated. If `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_TEST_CHAT_ID` are not supplied, only the live smoke criterion is
blocked; offline handler tests still validate the bot code.

### Windows Reference

Windows commands are still supported when needed:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[dev]"
$env:TELEGRAM_BOT_TOKEN="..."
$env:WUWATERM_DB_PATH="data\terms.db"
```

## Maintenance

This is a personal hobby project, maintained on a best-effort basis. There is no
guarantee of responses to issues or pull requests.

## License

Released under the [MIT License](LICENSE), © 2026 My-Denia. The MIT license
covers this project's source code only — not the upstream Wuthering Waves game
data or in-game terminology, which are © Kuro Games (see [Data Source](#data-source)).
