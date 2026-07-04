# Privacy And LLM

This guide describes the privacy boundary around dictionary lookups, LLM calls,
runtime state, and logs.

## Dictionary-First Boundary

An exact database hit returns the official string byte-for-byte from the local
SQLite database and does not call the LLM. This applies to command paths and
linked-channel auto-translation. Sentence translation locks known DB terms
before any LLM call, so official terms are restored verbatim in the target
language rather than paraphrased.

Generated TextMap data, SQLite DBs, runtime settings, channel reply indexes,
tokens, API keys, and real Telegram chat/user/message ids must stay out of
commits and public logs.

## LLM Configuration

Optional LLM environment variables:

- `WUWATERM_OPENAI_BASE_URL`, set explicitly to your OpenAI-compatible or
  LiteLLM gateway URL.
- `WUWATERM_OPENAI_API_KEY`.
- `WUWATERM_OPENAI_MODEL`.
- `WUWATERM_LLM_TIMEOUT_SECONDS`, default `45`.
- `WUWATERM_LLM_MAX_CONCURRENCY`, default `4`; max in-flight LLM calls.

`OPENAI_BASE_URL` and `OPENAI_API_KEY` are accepted fallbacks for the endpoint
and key; `WUWATERM_OPENAI_MODEL` is still required for LLM use.

No Telegram token, LLM key, endpoint, or model is hardcoded.

## Source Text Is Untrusted

The LLM system prompt treats user/channel text as untrusted source text: it is
translation input only, and instructions inside that source text must not be
followed. The prompt stays short, still requires placeholders exactly unchanged,
and in HTML mode still requires tags and attributes exactly unchanged.

This is a prompt-level guard. The stronger structural protection is that known
official terms are locked before the model sees the text.

## Placeholder Integrity

Term locking uses per-request nonce placeholders with the `__WUWA_TERM_...__`
shape. User literal placeholders such as `__TERM_0__` and strings that only
look like internal placeholders are not restored unless they were generated for
that request.

After the LLM returns, placeholder restore performs integrity checks. Missing,
duplicated, or malformed placeholders fail closed instead of producing a
translation that silently drops or corrupts official terms.

## Settings And Authorization

Private `/tr` is owner-only. Missing or empty `OWNER_USER_ID` means private
`/tr` rejects everyone and logs a startup warning. Group serving is gated on the
authorization allowlist, and public mode never bypasses that allowlist.

The allowlist and `/public` state are persisted to `WUWATERM_SETTINGS_PATH`,
defaulting alongside the DB. The linked-channel reply index defaults to
`<db parent>/channel_replies.json`. Both are runtime data files and should stay
in the ignored `data/` volume.

## Logging And Status Output

`/status` is owner-only and reports operational counts and flags only:
dictionary term count, data profile/short commit, LLM configured yes/no,
channel auto-translation on/off, tracked channel-post count, allowlist/public
counts, channel reply persistence health, and message limits. It does not print
secrets, storage paths, or chat ids.

Deployment smoke checks do not print Telegram tokens or chat ids.
