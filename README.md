# The Telegram Documentaries

An interactive Telegram bot powered by Google Agent Development Kit (ADK) and Gemini multimodal models. A user uploads a portrait photo, answers a brief 5–7 question behavioral interview, and receives a customized hybrid wildlife portrait along with a dramatic, British-style nature documentary narration delivered as both text and a synthesized voice note.

## Current Status (Phase 1)

- **Phase 1 (Completed):** Repository skeleton, strict Pydantic v2 boundary models, decorator-based structured logging (`@log_stage`), pre-commit quality/security hooks, and the Telegram Bot API long-polling gateway entrypoint that replies `"hey mate!"` to incoming messages.

## Project Structure

```text
.
├── main.py                              # Top-level convenience entrypoint
├── pyproject.toml                       # Package metadata, dependencies, ruff/mypy/pytest config
├── .pre-commit-config.yaml              # Pre-commit hooks (ruff, ruff-format, gitleaks, mypy, pytest)
├── scripts/
│   ├── test                             # Runs the automated pytest suite
│   └── hooks                            # Runs ruff, mypy, pytest, and pre-commit hooks
├── src/
│   └── telegram_documentaries/
│       ├── config.py                    # .env loader and Pydantic Settings validation
│       ├── logging_utils.py             # Structured logging setup and @log_stage decorator
│       ├── models.py                    # Pydantic v2 Telegram Bot API boundary schemas
│       ├── telegram_client.py           # Long-polling getUpdates & sendMessage HTTP client
│       ├── poller.py                    # Resilient polling cycle and update processor
│       └── main.py                      # CLI entrypoint
└── tests/                               # Unit and boundary tests
```

## Setup & Configuration

1. **Install dependencies (editable mode with dev tools):**
   ```bash
   pip install -e ".[dev]"
   ```

2. **Configure environment variables (`.env`):**
   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
   Required variables in `.env`:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram Bot token from `@BotFather` (must not be empty or the placeholder value).
   - `GEMINI_API_KEY`: Your Google Gemini API key (must not be empty or the placeholder value).

   > **Security Note:** `.env` is gitignored and protected by `gitleaks` pre-commit hooks. Never commit real tokens or API keys.

3. **Install Git Pre-Commit Hooks:**
   ```bash
   pre-commit install
   ```

## Running the Bot

Start the Telegram long-polling gateway using either entrypoint:

```bash
python main.py
```

or as a module:

```bash
python -m telegram_documentaries.main
```

When started, the bot logs `Telegram polling loop started...`, long-polls `getUpdates` (`timeout=30`), replies `"hey mate!"` to every incoming message, and advances the polling offset (`update_id + 1`).

## Developer Scripts & Quality Checks

- **Run the test suite:**
  ```bash
  ./scripts/test
  ```

- **Run all linters, formatters, static type checks, tests, and pre-commit hooks:**
  ```bash
  ./scripts/hooks
  ```
  `./scripts/hooks` runs:
  - `ruff check .`
  - `ruff format --check .`
  - `mypy src tests`
  - `pytest`
  - `pre-commit run --all-files` (`ruff-check`, `ruff-format`, `gitleaks`, `mypy`, `pytest`)
