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
- **Status:** Completed
- **Goal:** Implement the Interviewer agent (`gemini-3.1-flash-lite-preview`) doubling as the hub-and-spoke orchestrator that conducts a sequential 5–7 question behavioral interview per `chat_id` and compiles a structured behavioral dossier plus a suggested spirit animal.
- **Acceptance Criteria:**
  - [x] Orchestrator tracks per-`chat_id` turn count and conversation history in memory.
  - [x] Asks one engaging question at a time for 5–7 turns following an accepted portrait.
  - [x] Produces a validated Pydantic `BehavioralDossier` (summary of habits, personality traits, habitat, and chosen spirit animal) upon completing the final question and transitions state to `GENERATING_MEDIA`.
- **Delivery Summary:** Added strict Pydantic v2 interview boundary schemas (`InterviewTurn`, `BehavioralDossier`, `InterviewerTurnResult`), extended `SessionPhase` (`AWAITING_PHOTO` -> `INTERVIEWING` -> `GENERATING_MEDIA`) and `SessionStore` (`start_interview`, `record_answer`, `complete_interview`, `reset`), implemented the structured-output `Interviewer` agent (`gemini-3.1-flash-lite-preview`) with 5–7 turn bounds enforcement and `event=interviewer_question`/`event=interviewer_dossier` logging, and wired multi-turn interview orchestration and resilient error recovery into `poller.py` and `main.py`. 144 tests pass.
- **Rubric Alignment:** Multi-turn stateful interview, per-`chat_id` session isolation, and structured dossier generation.

---

## Phase 4: The Converter (Hybrid Animal Portrait)
- **Status:** Completed
- **Goal:** Implement the Converter specialist agent (`gemini-3.1-flash-image-preview`) that fuses the user's original portrait photo and the behavioral dossier into a hybrid animal-human portrait and sends it directly to the user's Telegram chat.
- **Acceptance Criteria:**
  - [x] Original user photo and `BehavioralDossier` are passed together to `gemini-3.1-flash-image-preview`.
  - [x] Generated image bytes are validated and sent directly to the Telegram chat via `sendPhoto`.
  - [x] No intermediate text-only image description hop is used.
- **Delivery Summary:** Added strict Pydantic v2 boundary schemas (`GeneratedImage`, `SendPhotoPayload`, `SendPhotoResponse`), extended `SessionState`/`SessionStore` with `hybrid_portrait` (`set_hybrid_portrait`, cleared in `start_interview` and `reset`), implemented `TelegramClient.send_photo` multipart photo upload, created the `Converter` specialist agent (`gemini-3.1-flash-image-preview`, `response_modalities=["IMAGE", "TEXT"]`, `event=converter_portrait_generated` logging), and wired hybrid portrait delivery and retry resilience (`CONVERTER_ERROR_FALLBACK_MESSAGE`) into `poller.py` and `main.py`. 174 tests pass.
- **Rubric Alignment:** Native multimodal image transformation and delivery to Telegram.

---

## Phase 5: The Scripter (Documentary Narration Text)
- **Status:** Completed
- **Goal:** Implement the Scripter specialist agent (`gemini-3.1-flash-lite-preview`) that transforms the behavioral dossier into a single-paragraph (~60–90 words) dramatic British wildlife documentary script and sends it to the chat.
- **Acceptance Criteria:**
  - [x] Generates a cohesive, one-paragraph nature documentary narrative (~60–90 words) matching the user's dossier and spirit animal.
  - [x] Sends the narrative text to the user's Telegram chat and passes it to subsequent stages.
- **Delivery Summary:** Added strict Pydantic v2 boundary schema (`DocumentaryScript` with single-paragraph and markdown-cleaning validators), extended `SessionState`/`SessionStore` with `script` (`set_script`, cleared in `start_interview` and `reset`), implemented the structured-output `Scripter` agent (`gemini-3.1-flash-lite-preview`, `response_schema=DocumentaryScript`, `event=scripter_narration_generated` logging), wired sequential narration generation immediately following hybrid portrait delivery in `GENERATING_MEDIA`, and added retry resilience (`SCRIPTER_ERROR_FALLBACK_MESSAGE`) and out-of-order text handling. 208 tests pass.
- **Rubric Alignment:** Documentary script quality, persona adherence, and pipeline integration.

---

## Phase 6: The Narrator (Gemini TTS Audio Synthesis)
- **Status:** Completed
- **Goal:** Implement the Narrator TTS execution tool using `gemini-3.1-flash-tts-preview` to synthesize the Scripter's paragraph into a Telegram-compatible audio format (OGG/MP3) and deliver it to the chat.
- **Acceptance Criteria:**
  - [x] Deterministic tool (not an autonomous agent) sends the script text to `gemini-3.1-flash-tts-preview`.
  - [x] Converts/packages the returned audio into a valid OGG/MP3 payload and delivers it to Telegram via `sendVoice` or `sendAudio`.
  - [x] Transitions session state to `COMPLETED` and cleans up temporary media files.
- **Delivery Summary:** Added strict Pydantic v2 boundary schemas (`GeneratedAudio`, `SendVoicePayload`, `SendVoiceResponse`), extended `SessionPhase` with `COMPLETED = "completed"` and `SessionStore.complete_documentary`, added `TelegramClient.send_voice` (`SEND_VOICE_TIMEOUT_SECONDS = 30`), implemented the deterministic `Narrator` TTS tool (`gemini-3.1-flash-tts-preview`, `DEFAULT_NARRATOR_VOICE = "Charon"`, Nikolai Drozdov *"In the World of Animals"* voice prompt prefix, `lameenc` raw PCM-to-MP3 encoding, `synthesize_to_temp_file` `try...finally` cleanup, and `event=narrator_audio_generated` logging), and wired voice delivery and stage-aware retry (`NARRATOR_ERROR_FALLBACK_MESSAGE`) into `poller.py` and `main.py`. 246 tests pass.
- **Rubric Alignment:** End-to-end multimodal delivery (voice note narration).

---

## Phase 7: Resilience, Reset Commands & Edge Guards
- **Status:** Completed
- **Goal:** Harden the pipeline with an explicit three-step `/start` and `/restart` reset routine (1. Cancel Active Sessions, 2. Purge Ephemeral Assets on disk and in memory, 3. Re-initialize Agent Memory), out-of-order payload guards at every state-machine phase, temporary file cleanup, and graceful error recovery for upstream API failures.
- **Acceptance Criteria:**
  - [x] `/start` and `/restart` immediately perform three actions for the requesting `chat_id` without restarting the process:
    1. **Cancel Active Sessions:** Terminate any running interview loops or waiting states (`INTERVIEWING`, `GENERATING_MEDIA`, `COMPLETED`).
    2. **Purge Ephemeral Assets:** Delete temporary media on disk (original photo, converted PNG, generated OGG/MP3 voice files) and clear in-memory binary payloads.
    3. **Re-initialize Agent Memory:** Clear conversation history buffers (`interview_history`), `current_question`, `dossier`, `hybrid_portrait`, and `script`, and reset state trackers back to the greeting stage (`AWAITING_PHOTO`).
  - [x] Sending text in `AWAITING_PHOTO`, sending a photo during `INTERVIEWING` / `GENERATING_MEDIA` / `COMPLETED`, or sending unsupported media payloads returns a helpful guidance prompt without corrupting state.
  - [x] Gemini or Telegram API timeouts/errors are logged loudly via structured logging decorators and surface a friendly retry message to the user.
- **Delivery Summary:** Extended `TelegramMessage` with `caption: str | None = None` so `/start` and `/restart` work in both text messages and photo captions; added per-`chat_id` `ephemeral_files: list[Path]` tracking (`register_ephemeral_file`, `cleanup_ephemeral_files`) and an explicit 3-step `SessionStore.reset` routine (canceling active sessions, unlinking tracked temporary files on disk and clearing in-memory binary payloads, re-initializing agent memory, and emitting `event=session_reset stage=session`); registered temporary voice narration files with `SessionStore` during `_synthesize_and_send_voice`; added `INTERVIEW_PHOTO_GUARD_MESSAGE` and phase guards for out-of-order photos (`INTERVIEWING`, `GENERATING_MEDIA`, `COMPLETED`) and unsupported media payloads (`event=unsupported_payload`); wrapped fallback `send_message` calls in `try/except TelegramClientError` (`event=*_fallback_failed`); and verified back-to-back full documentary runs in a single process. 256 tests pass.
- **Rubric Alignment:** `/restart` state hygiene, wrong-payload guards, and production-grade error resilience.
