<!--
Thanks for sending a change. Small and focused merges fastest.
Read CONTRIBUTING.md if you have not yet — especially the non-goals, which are
enforced by a gate and cannot be merged past.
Never put tokens, API keys, device credentials, real Telegram ids or .env
contents in this description or in the diff.
-->

## What

<!-- What this change does, in a sentence or two. -->

## Why

<!--
The problem it solves. Link the issue if there is one (Fixes #123).
If this changes behaviour somebody could be relying on, say so here.
-->

## How It Was Validated

<!--
Paste the result of the single entry point, run locally:

    python scripts/validate.py

Say which host and Python version you ran it on. The server suite is supported
on Linux; it is not supported on a Windows host.
-->

```
python scripts/validate.py
<paste the tail of the output here>
```

Anything extra that was run — the client suite (`python scripts/validate.py
--client`), a container build, a manual check against a running instance:

<!-- Describe it, or write "none". -->

## Checklist

- [ ] One concern. This change does one thing; anything else is a separate pull request.
- [ ] `python scripts/validate.py` passes locally, and the output above is from this branch.
- [ ] An entry was added under `## Unreleased` in `CHANGELOG.md`.
- [ ] If either README was touched, `README.md` and `README.en.md` were both updated and their headings and code fences still match.
- [ ] Documentation was updated wherever this change made an existing statement untrue.
- [ ] Tests were added or changed such that they fail without this change, and the description says which ones.
- [ ] No secrets and no generated data: no `.env`, no tokens or API keys, no generated SQLite database, no upstream TextMap or game data, no runtime state (`state/`, `state-api/`, `chat_settings.json`, `channel_replies.json`).
- [ ] This change does not cross a non-goal (see `CONTRIBUTING.md`).

## Notes For The Reviewer

<!--
Anything worth knowing: a decision you were unsure about, a part that deserves
a closer look, a follow-up you deliberately left out of scope.
Automated reviewers run on this pull request. If one of them is wrong, say why
in the thread rather than changing the code to satisfy it.
-->
