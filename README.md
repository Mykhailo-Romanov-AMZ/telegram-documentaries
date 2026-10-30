# The Telegram Documentaries

An interactive Telegram bot powered by Google Agent Development Kit (ADK) and Gemini multimodal models. A user uploads a portrait photo, answers a brief 5–7 question behavioral interview, and receives a customized hybrid wildlife portrait along with a dramatic, British-style nature documentary narration delivered as both text and a synthesized voice note.

## Current Status (Phase 3)

- **Phase 1 (Completed):** Repository skeleton, strict Pydantic v2 boundary models, decorator-based structured logging (`@log_stage`), pre-commit quality/security hooks, and the Telegram Bot API long-polling gateway entrypoint.
- **Phase 2 (Completed):** The Bouncer vision gate (`google-genai` + `gemini-3.1-flash-lite-preview`) inspects uploaded photos for a human subject, admits human portraits to the session, and rejects non-human photos with a cheeky in-character message. Per-`chat_id` session state lives in memory.
- **Phase 3 (Completed):** The Interviewer & stateful orchestrator (`Interviewer` + `gemini-3.1-flash-lite-preview`) conducts a sequential 5–7 question behavioral interview one question at a time per `chat_id`, enforces turn bounds, compiles a validated `BehavioralDossier` (`summary` + `suggested_animal`), transitions the session to `GENERATING_MEDIA`, and delivers the dossier summary to the user's chat.

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
│       ├── models.py                    # Pydantic v2 Telegram & Gemini boundary schemas
│       ├── telegram_client.py           # Long-polling, messaging, and file download HTTP client
│       ├── session.py                   # In-memory per-chat_id session state machine
│       ├── bouncer.py                   # Gemini structured-output human-detection vision gate
│       ├── interviewer.py               # Gemini 5-7 turn behavioral interviewer & dossier builder
│       ├── poller.py                    # Resilient polling cycle and Bouncer + Interviewer router
│       └── main.py                      # CLI entrypoint
└── tests/                               # Unit and boundary tests (144 tests)
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

### Vision Gate & 5–7 Turn Interview Flow (`AWAITING_PHOTO` -> `INTERVIEWING` -> `GENERATING_MEDIA`)

- **Human portrait (`AWAITING_PHOTO` or replacement upload):** The largest photo size is downloaded and verified by the `Bouncer` (`BouncerVerdict`). When `is_human=True`, `Interviewer.start_interview` generates **Question 1** from the portrait, `SessionStore.start_interview` stores the portrait and `current_question` (clearing any prior `interview_history` or `dossier`) and transitions the chat to `INTERVIEWING`, and the bot sends Question 1.
- **Non-human photo** (pet, car/vehicle even with a visible driver/reflection/bystander, food, landscape, object): The Bouncer's cheeky rejection is sent, naming what it actually saw, and the session resets to `AWAITING_PHOTO`.
- **Text while `AWAITING_PHOTO`:** Sends a polite in-character nudge (`AWAITING_PHOTO_NUDGE`) asking for a human portrait photo.
- **Text while `INTERVIEWING` (5–7 sequential turns):**
  - Each non-empty answer is paired with `current_question` into an `InterviewTurn` and passed with the chat's `interview_history` to `Interviewer.advance_interview` (`response_schema=InterviewerTurnResult`).
  - Turn bounds (`MIN_INTERVIEW_QUESTIONS = 5`, `MAX_INTERVIEW_QUESTIONS = 7`) are strictly enforced: the interview never completes with fewer than 5 answers and always completes by the 7th answer at the latest.
  - While `is_complete=False`, `SessionStore.record_answer` appends the `InterviewTurn`, updates `current_question`, and sends the single `next_question` (`event=interviewer_question stage=interviewer turn=...`).
  - When `is_complete=True` (after 5–7 answers), `SessionStore.complete_interview` records the final turn, stores the validated `BehavioralDossier` (`summary` + `suggested_animal`), transitions the session to `GENERATING_MEDIA`, and sends the formatted dossier summary and spirit animal to Telegram (`event=interviewer_dossier stage=interviewer turns=... suggested_animal=...`).
- **Text while `GENERATING_MEDIA`:** Replies with `INTERVIEW_COMPLETE_NUDGE` letting the user know field research is complete and offering `/restart`.
- **`/start` or `/restart` in any phase:** Resets the chat's session back to `AWAITING_PHOTO` (clearing `portrait`, `current_question`, `interview_history`, and `dossier`) and sends `AWAITING_PHOTO_NUDGE`.
- **Error Resilience:**
  - Photo download, Bouncer, or Question 1 failures reply with `ERROR_FALLBACK_MESSAGE` (`"My binoculars fogged up — send that photo again?"`) and ensure the chat is never stranded in `INTERVIEWING` without a `current_question`.
  - Mid-interview Gemini failures reply with `INTERVIEW_ERROR_FALLBACK_MESSAGE` (`"My field notebook got rained on — could you repeat that answer, mate?"`) while preserving `interview_history` and `current_question` so the user can retry their answer.

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
