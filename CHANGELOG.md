# Changelog

## 2026-05-12

- Refactored storage from MongoDB/Motor to local SQLite.
- Moved API credentials and bot token into `.env`.
- Added missing callback handlers for report destination, delay setup, empty menu buttons, and back navigation.
- Replaced shared `temp_sess` userbot session name with per-account session files.
- Added quickstart, screen usage guide, and SQLite insert example.
