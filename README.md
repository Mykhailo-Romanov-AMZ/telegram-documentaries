# The Telegram Documentaries

An interactive Telegram bot powered by Google Agent Development Kit (ADK) and Gemini multimodal models. A user uploads a portrait photo, answers a brief 5–7 question behavioral interview, and receives a customized hybrid wildlife portrait along with a dramatic, British-style nature documentary narration delivered as both text and a synthesized voice note.

## Current Status (Phase 2)

- **Phase 1 (Completed):** Repository skeleton, strict Pydantic v2 boundary models, decorator-based structured logging (`@log_stage`), pre-commit quality/security hooks, and the Telegram Bot API long-polling gateway entrypoint that replies `"hey mate!"` to incoming messages.
- **Phase 2 (Completed):** The Bouncer vision gate (`google-genai` + `gemini-3.1-flash-lite-preview`) inspects uploaded photos for a human subject, admits human portraits to the session, and rejects non-human photos with a cheeky in-character message. Per-`chat_id` session state lives in memory.

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
│       ├── telegram_client.py           # Long-polling, messaging, and file download HTTP client
│       ├── session.py                   # In-memory per-chat_id session state machine
│       ├── bouncer.py                   # Gemini structured-output human-detection vision gate
│       ├── poller.py                    # Resilient polling cycle and Bouncer-aware update router
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

When started, the bot logs `Telegram polling loop started...`, long-polls `getUpdates` (`timeout=30`), and advances the polling offset (`update_id + 1`).

### Photo Gate Behaviour

- **Human portrait:** the largest photo size is downloaded, classified by the Bouncer (requiring a real human as the primary, central subject), the portrait bytes are stored in the chat's session (`INTERVIEWING`), and the bot replies `"hey mate!"`.
- **Non-human photo** (pet, car/vehicle even with a visible driver/reflection/bystander, food, landscape, object): the Bouncer's cheeky rejection is sent, naming what it actually saw, and the session resets to `AWAITING_PHOTO`.
- **Text while awaiting a photo:** the bot sends a polite in-character nudge (`AWAITING_PHOTO_NUDGE`) asking for a portrait.
- **`/start` or `/restart` in any phase:** resets the chat's session back to `AWAITING_PHOTO` (clearing any stored portrait) and sends `AWAITING_PHOTO_NUDGE`.
- **Download or Gemini failure:** the bot replies with an in-character fallback (`"My binoculars fogged up — send that photo again?"`) and leaves session state unchanged; the full traceback is logged.

The vision gate uses `google-genai` structured output (`BouncerVerdict` with fields ordered `subject`, `is_human`, `rejection_message`) with the `gemini-3.1-flash-lite-preview` model and logs every verdict (`event=bouncer_verdict stage=bouncer is_human=... subject=...`) at `INFO` level. `GEMINI_API_KEY` is used at runtime as well as validated at startup.

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
