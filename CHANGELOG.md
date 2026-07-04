# Changelog

All notable source changes for this project are tracked here. This repository
does not distribute generated game data or generated SQLite databases.

## 0.1.0 - 2026-07-04

Initial release preparation for the self-hosted WuWa Term Bot.

### Added

- Dictionary-first Chinese to official English and English to Chinese term
  lookup from a locally built SQLite database.
- Term-locked sentence translation through an OpenAI-compatible endpoint, with
  exact database hits returned before any LLM call.
- Telegram bot commands for private and group use, including group allowlists,
  public-mode controls, linked-channel auto-translation, rate limits, and
  privacy-safe operational logging.
- Local validation and hygiene scripts that guard against committing generated
  game data, generated databases, secrets, and private Telegram identifiers.

### Data Source

- Supported source profile: `arikatsu`
- Supported game data version: `GameVer 3.4.0 | ResVer 3.4.13`
- Pinned source repository: `https://github.com/Arikatsu/WutheringWaves_Data`
- Pinned source commit: `58ec43698d2b4e188cb285467ce1ae887612dd92`

### Known Limitations

- The bot is self-hosted; no public hosted service is provided by this
  repository.
- Live Telegram behavior still requires owner-provided bot credentials and
  chat configuration.
- Free-text sentence translation requires a separately configured
  OpenAI-compatible endpoint.
- Generated TextMap data and generated term databases are local artifacts and
  are not published in this source repository.
