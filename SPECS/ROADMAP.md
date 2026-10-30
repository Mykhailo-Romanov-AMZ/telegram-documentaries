# Project Roadmap: The Telegram Documentaries

This roadmap defines the ordered build phases for **The Telegram Documentaries**. Each phase is specified, implemented via Red/Green TDD, reviewed, and verified before moving to the next.

---

## Phase 1: Repository Skeleton & Telegram Polling Gateway
- **Status:** Completed
- **Goal:** Establish the Python project structure, dependency configuration, developer scripts (`scripts/test`, `scripts/hooks`), `.env` configuration loader, and a basic Telegram Long Polling loop that echoes or replies with a hardcoded greeting.
- **Acceptance Criteria:**
  - [x] `scripts/test` and `scripts/hooks` execute cleanly (`pytest`, `ruff`, `mypy`, `pre-commit` with `gitleaks`).
  - [x] Pydantic settings validate `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` from `.env` (rejecting empty or placeholder values).
  - [x] Long polling gateway connects to Telegram (`getUpdates` / `sendMessage` with Pydantic v2 boundary models and `@log_stage` structured logging) and replies `"hey mate!"` to incoming messages.
- **Delivery Summary:** Implemented `pyproject.toml`, `.pre-commit-config.yaml`, `scripts/test`, `scripts/hooks`, `src/telegram_documentaries/{config,logging_utils,models,telegram_client,poller,main}.py`, `main.py`, and 24 unit/boundary tests under `tests/`.
- **Rubric Alignment:** Project setup, repository hygiene, and working Telegram long-polling connection.

---

## Phase 2: The Bouncer (Vision Gate)
- **Status:** Completed
- **Goal:** Implement the Bouncer specialist agent using `gemini-3.1-flash-lite-preview` to inspect uploaded photos, confirm a human subject is present, and reject non-human photos (cars, pets, food, objects) with a cheeky message while resetting session state.
- **Acceptance Criteria:**
  - [x] Incoming photo is downloaded/buffered and passed to the Bouncer with a structured Pydantic verdict schema (`subject: str`, `is_human: StrictBool`, `rejection_message: str`).
  - [x] Human photos transition the user's `SessionState` from `AWAITING_PHOTO` to `INTERVIEWING` and retain the portrait bytes in memory.
  - [x] Non-human photos send the Bouncer's cheeky rejection message to Telegram and reset the session cleanly.
- **Delivery Summary:** Added `google-genai`, extended boundary models (`TelegramPhotoSize`, `TelegramFile`, `GetFileResponse`, `BouncerVerdict` with `subject` ordered first and field descriptions), added `TelegramClient.get_file`/`download_file`, an in-memory `SessionStore` (`AWAITING_PHOTO`/`INTERVIEWING`), the structured-output `Bouncer` agent (primary-subject human check + `event=bouncer_verdict` info logging), and gateway photo routing with `/start` & `/restart` session reset and in-character error fallbacks. 93 tests pass.
- **Rubric Alignment:** Vision gate accuracy, cheeky rejection handling, and state transition.

---

## Phase 3: The Interviewer & State Orchestrator
- **Status:** Pending
- **Goal:** Implement the Interviewer agent (`gemini-3.1-flash-lite-preview`) doubling as the hub-and-spoke orchestrator that conducts a sequential 5–7 question behavioral interview per `chat_id` and compiles a structured behavioral dossier plus a suggested spirit animal.
- **Acceptance Criteria:**
  - Orchestrator tracks per-`chat_id` turn count and conversation history in memory.
  - Asks one engaging question at a time for 5–7 turns following an accepted portrait.
  - Produces a validated Pydantic `BehavioralDossier` (summary of habits, personality traits, habitat, and chosen spirit animal) upon completing the final question and transitions state to `GENERATING_MEDIA`.
- **Rubric Alignment:** Multi-turn stateful interview, per-`chat_id` session isolation, and structured dossier generation.

---

## Phase 4: The Converter (Hybrid Animal Portrait)
- **Status:** Pending
- **Goal:** Implement the Converter specialist agent (`gemini-3.1-flash-image-preview`) that fuses the user's original portrait photo and the behavioral dossier into a hybrid animal-human portrait and sends it directly to the user's Telegram chat.
- **Acceptance Criteria:**
  - Original user photo and `BehavioralDossier` are passed together to `gemini-3.1-flash-image-preview`.
  - Generated image bytes are validated and sent directly to the Telegram chat via `sendPhoto`.
  - No intermediate text-only image description hop is used.
- **Rubric Alignment:** Native multimodal image transformation and delivery to Telegram.

---

## Phase 5: The Scripter (Documentary Narration Text)
- **Status:** Pending
- **Goal:** Implement the Scripter specialist agent (`gemini-3.1-flash-lite-preview`) that transforms the behavioral dossier into a single-paragraph (~60–90 words) dramatic British wildlife documentary script and sends it to the chat.
- **Acceptance Criteria:**
  - Generates a cohesive, one-paragraph nature documentary narrative (~60–90 words) matching the user's dossier and spirit animal.
  - Sends the narrative text to the user's Telegram chat and passes it to the TTS stage.
- **Rubric Alignment:** Documentary script quality, persona adherence, and pipeline integration.

---

## Phase 6: The Narrator (Gemini TTS Audio Synthesis)
- **Status:** Pending
- **Goal:** Implement the Narrator TTS execution tool using `gemini-3.1-flash-tts-preview` to synthesize the Scripter's paragraph into a Telegram-compatible audio format (OGG/MP3) and deliver it to the chat.
- **Acceptance Criteria:**
  - Deterministic tool (not an autonomous agent) sends the script text to `gemini-3.1-flash-tts-preview`.
  - Converts/packages the returned audio into a valid OGG/MP3 payload and delivers it to Telegram via `sendVoice` or `sendAudio`.
  - Transitions session state to `COMPLETED` and cleans up temporary media files.
- **Rubric Alignment:** End-to-end multimodal delivery (voice note narration).

---

## Phase 7: Resilience, Reset Commands & Edge Guards
- **Status:** Pending
- **Goal:** Harden the pipeline with `/start` and `/restart` reset handlers, out-of-order payload guards at every state-machine phase, temporary file cleanup, and graceful error recovery for upstream API failures.
- **Acceptance Criteria:**
  - `/start` and `/restart` immediately wipe the user's in-memory session state and temporary files at any point without restarting the process.
  - Sending text in `AWAITING_PHOTO` or sending a photo during `INTERVIEWING` returns a helpful prompt without corrupting state.
  - Gemini or Telegram API timeouts/errors are logged loudly via structured logging decorators and surface a friendly retry message to the user.
- **Rubric Alignment:** `/restart` state hygiene, wrong-payload guards, and production-grade error resilience.
