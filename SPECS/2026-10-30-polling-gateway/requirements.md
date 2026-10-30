# Phase 1 Requirements: Repository Skeleton, Pre-Commit Hooks & Telegram Polling Gateway

## Context & Goal
This specification covers **Phase 1** of `SPECS/ROADMAP.md` for **The Telegram Documentaries**. The goal is to establish the foundational Python package structure, developer tooling (`scripts/test`, `scripts/hooks`, and `.pre-commit-config.yaml` with `ruff`, `ruff-format`, `mypy`, `pytest`, and `gitleaks`), strict Pydantic boundary schemas, decorator-based structured logging, and a Telegram Bot API long-polling gateway entrypoint that replies `"hey mate!"` to every incoming user message without invoking Gemini APIs yet.

## Scope & Decisions
1. **Hardcoded Reply:** Every valid incoming Telegram message triggers the hardcoded reply `"hey mate!"`. No Gemini API calls are made in Phase 1.
2. **Transport:** Long polling against the Telegram Bot API (`getUpdates` with `timeout=30` and `offset` progression, plus `sendMessage`). Uses `requests` (with explicit timeouts) wrapped in a clean gateway client and validated at the boundary with Pydantic v2 models.
3. **Boundary Validation (Pydantic v2):**
   - `Settings`: Loads and validates `TELEGRAM_BOT_TOKEN` (non-empty, must not be placeholder) and `GEMINI_API_KEY` from `.env` (via `python-dotenv` / `pydantic`).
   - Telegram Update Schemas:
     - `TelegramChat(id: int)` (strictly integer `chat_id` so session keys never suffer string-vs-int drift).
     - `TelegramMessage(message_id: int, chat: TelegramChat, text: str | None = None)`.
     - `TelegramUpdate(update_id: int, message: TelegramMessage | None = None)`.
     - `GetUpdatesResponse(ok: bool, result: list[TelegramUpdate] = [])`.
     - `SendMessagePayload(chat_id: int, text: str)`.
4. **Structured Logging via Decorators:**
   - Provide a reusable `@log_stage(stage_name: str)` decorator in `src/telegram_documentaries/logging_utils.py` that logs entry, exit, duration, `chat_id` (when available), and full exception tracebacks before re-raising or handling.
   - Business logic remains clean and free of scattered boilerplate log statements.
5. **Error Handling Policy:**
   - Startup configuration errors (missing or placeholder `TELEGRAM_BOT_TOKEN`) fail loudly on startup.
   - Malformed updates or transient Telegram HTTP errors during polling are logged loudly with full context and handled gracefully so the polling loop continues without crashing.
6. **Pre-Commit Hooks & Developer Scripts:**
   - `.pre-commit-config.yaml` configured and installed (`pre-commit install`) with:
     - `ruff` (linting) and `ruff-format` (`https://github.com/astral-sh/ruff-pre-commit` at `v0.16.2`)
     - `mypy` (`https://github.com/pre-commit/mirrors-mypy` at `v2.3.0` or local strict `mypy`)
     - `gitleaks` (`https://github.com/gitleaks/gitleaks` at `v8.21.2`)
     - `pytest` (local hook running `scripts/test`)
   - `scripts/test`: Executes `pytest`.
   - `scripts/hooks`: Executes `ruff check`, `ruff format --check`, `mypy`, `pytest`, and `pre-commit run --all-files`.

## Out of Scope (YAGNI)
- No Gemini / ADK model calls in Phase 1 (deferred to Phase 2+).
- No webhooks or web servers.
- No persistent database.
