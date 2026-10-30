# Phase 1 Implementation Plan: Repository Skeleton & Telegram Polling Gateway

All implementation follows **Red/Green TDD**: write failing tests first, verify they fail, implement the minimal code to make them pass, and verify with `scripts/test` and `scripts/hooks`.

---

## Task Group 1: Project Configuration, Pre-Commit Hooks & Developer Scripts
1. **Create `pyproject.toml` and install dependencies:**
   - Configure package metadata (`telegram-documentaries`), Python `>=3.11`, dependencies (`pydantic>=2.0`, `python-dotenv`, `requests`), and tool configurations for `pytest`, `ruff`, and `mypy` (strict mode).
   - Ensure `pydantic`, `python-dotenv`, and `types-requests` (if needed for mypy) are installed in the environment.
2. **Create `scripts/test` and `scripts/hooks` (executable `chmod +x`):**
   - `scripts/test`: Runs `pytest`.
   - `scripts/hooks`: Runs `ruff check .`, `ruff format --check .`, `mypy src tests`, `pytest`, and `pre-commit run --all-files`.
3. **Create `.pre-commit-config.yaml` and install git hooks:**
   - Configure `https://github.com/astral-sh/ruff-pre-commit` (`v0.16.2`) with `ruff` and `ruff-format`.
   - Configure `https://github.com/gitleaks/gitleaks` (`v8.21.2`) with `gitleaks`.
   - Configure local hooks for `mypy` and `pytest`.
   - Run `pre-commit install` so `.git/hooks/pre-commit` is active.

---

## Task Group 2: Configuration & Decorator-Based Structured Logging (TDD)
1. **Red — Write failing tests (`tests/test_config_and_logging.py`):**
   - Test `load_settings()` succeeds with valid `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY`.
   - Test `load_settings()` raises `ValidationError` when `TELEGRAM_BOT_TOKEN` is missing, empty, or still set to `"your_telegram_bot_token_here"`.
   - Test `@log_stage("gateway")` decorator logs function start, completion, and logs exception details when the wrapped function raises.
2. **Green — Implement `src/telegram_documentaries/config.py` and `src/telegram_documentaries/logging_utils.py`:**
   - Implement `Settings(BaseModel)` with Pydantic v2 field validators and `load_settings()`.
   - Implement `configure_logging()` and `@log_stage` decorator using `functools.wraps`.
3. **Verify:** Run `scripts/test`.

---

## Task Group 3: Telegram Boundary Schemas & Client (TDD)
1. **Red — Write failing tests (`tests/test_telegram_client.py`):**
   - Test Pydantic parsing of valid Telegram `getUpdates` JSON payloads (text messages, photo/non-text messages, updates without a `message` field).
   - Test rejection (`ValidationError`) of malformed updates (e.g., missing `update_id` or non-integer `chat.id`).
   - Test `TelegramClient.get_updates(offset=...)` constructs the right request and returns validated `list[TelegramUpdate]`.
   - Test `TelegramClient.send_message(chat_id=..., text=...)` validates `SendMessagePayload` and posts to Telegram's `sendMessage` endpoint.
2. **Green — Implement `src/telegram_documentaries/models.py` and `src/telegram_documentaries/telegram_client.py`:**
   - Define `TelegramChat`, `TelegramMessage`, `TelegramUpdate`, `GetUpdatesResponse`, `SendMessagePayload` in `models.py`.
   - Implement `TelegramClient` in `telegram_client.py` decorated with `@log_stage`.
3. **Verify:** Run `scripts/test`.

---

## Task Group 4: Polling Loop & CLI Entrypoint (TDD)
1. **Red — Write failing tests (`tests/test_polling.py`):**
   - Happy path: `process_update(update, client)` sends `"hey mate!"` (`HARDCODED_REPLY`) to `update.message.chat.id`.
   - Edge case: `process_update` safely ignores updates where `update.message` is `None` (e.g., edited channel post or callback) without crashing or calling `send_message`.
   - Polling cycle (`poll_once(client, offset)`): fetches updates, replies `"hey mate!"` to each message, and advances offset to `max_update_id + 1`.
   - Resilience: if `client.get_updates` or `client.send_message` raises a network/API error during `poll_once`, the error is logged and caught gracefully without crashing the polling loop, preserving the current offset.
2. **Green — Implement `src/telegram_documentaries/poller.py` and `src/telegram_documentaries/main.py`:**
   - Implement `HARDCODED_REPLY = "hey mate!"`, `process_update()`, `poll_once()`, and `run_polling_loop()`.
   - Log a clear startup message when the polling loop starts (`"Telegram polling loop started..."`) so terminal operators can verify connection readiness.
   - Provide `python -m telegram_documentaries.main` (and `main.py` entrypoint) to launch the bot.
3. **Verify:** Run `scripts/test` and `scripts/hooks`.
