# Technical Contract: The Telegram Documentaries

## Technology Stack
- **Language:** Python 3.11+
- **Agent Framework:** Google Agent Development Kit (ADK) & `google-genai` SDK
- **Telegram Integration:** Telegram Bot API via **Long Polling** (no webhooks)
- **Audio Packaging:** `lameenc` for pure-Python PCM (`audio/L16` / `audio/pcm`) to MP3 encoding
- **Data Validation:** Pydantic v2 for strict schema validation at all module and external API boundaries
- **Configuration:** `python-dotenv` loading secrets from `.env` (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`)
- **Quality & Testing:** `pytest`, `ruff` (linting and formatting), `mypy` (strict static type checking)

## Model Assignments
| Pipeline Component | Role | Model Identifier | Type |
|---|---|---|---|
| **The Bouncer** | Human detection vision gate | `gemini-3.1-flash-lite-preview` | Specialist Agent |
| **The Interviewer** | Stateful 5–7 turn Q&A & dossier builder | `gemini-3.1-flash-lite-preview` | Orchestrator & Agent |
| **The Converter** | Multimodal portrait + dossier hybrid image synthesis | `gemini-3.1-flash-image-preview` | Specialist Agent |
| **The Scripter** | Single-paragraph (~60–90 words) British documentary script | `gemini-3.1-flash-lite-preview` | Specialist Agent |
| **The Narrator** | Text-to-Speech audio rendering (OGG/MP3) in Nikolai Drozdov's naturalist voice | `gemini-3.1-flash-tts-preview` | Execution Tool |

## Architecture: Hub-and-Spoke State Machine
- **Orchestrator Pattern:** The **Interviewer** (`Interviewer` in `src/telegram_documentaries/interviewer.py`) doubles as the central orchestrator in a hub-and-spoke architecture, managing conversation state and delegating work to the specialist agents (Bouncer, Converter, Scripter) and the TTS execution tool (Narrator).
- **Explicit Session Phases (`SessionPhase`):** Each user conversation is tracked via an explicit state machine enum (`AWAITING_PHOTO` -> `INTERVIEWING` -> `GENERATING_MEDIA` -> `COMPLETED`):
  - `AWAITING_PHOTO`: Initial state after `/start`, `/restart`, or Bouncer rejection; expects a user portrait photo.
  - `INTERVIEWING`: Human photo verified; `Interviewer.start_interview` generates Question 1 from the portrait, and `Interviewer.advance_interview` conducts a bounded 5–7 turn sequential interview (`MIN_INTERVIEW_QUESTIONS = 5`, `MAX_INTERVIEW_QUESTIONS = 7`), accumulating `interview_history: list[InterviewTurn]` and tracking `current_question`.
  - `GENERATING_MEDIA`: Entered when the 5–7 question interview completes (`SessionStore.complete_interview`), storing the validated `BehavioralDossier` (`summary` + `suggested_animal`). Sequentially executes:
    1. `Converter.generate_hybrid_portrait` (`gemini-3.1-flash-image-preview`), `TelegramClient.send_photo`, and `SessionStore.set_hybrid_portrait`.
    2. `Scripter.generate_script` (`gemini-3.1-flash-lite-preview`), `TelegramClient.send_message`, and `SessionStore.set_script`.
    3. `Narrator.synthesize_to_temp_file` (`gemini-3.1-flash-tts-preview` with `DEFAULT_NARRATOR_VOICE = "Charon"` and Nikolai Drozdov *"In the World of Animals"* voice direction), `TelegramClient.send_voice`, temporary audio file cleanup, and `SessionStore.complete_documentary`.
  - `COMPLETED`: Entered via `SessionStore.complete_documentary` once the hybrid portrait, documentary script, and voice narration are all delivered; subsequent text, photo, or unsupported messages reply with `INTERVIEW_COMPLETE_NUDGE` prompting `/restart` for a new documentary.
- **Phase & Payload Guards (`poller.py`):**
  - Reset commands (`/start`, `/restart`, including `@bot` suffixes) in either `TelegramMessage.text` or `TelegramMessage.caption` are intercepted at the top of `process_update` before photo/text routing, executing `SessionStore.reset(chat_id)` and replying with `AWAITING_PHOTO_NUDGE`.
  - Out-of-order photo uploads are guarded by phase: in `INTERVIEWING`, `_handle_photo` replies with `INTERVIEW_PHOTO_GUARD_MESSAGE` without downloading the photo or calling `Bouncer` (preserving the active interview); in `GENERATING_MEDIA`, it triggers stage-aware media retry (`_handle_generating_media_text`); in `COMPLETED`, it replies with `INTERVIEW_COMPLETE_NUDGE`.
  - Unsupported media messages (messages with neither `photo` nor `text`, such as stickers, voice notes, documents, or videos) log `event=unsupported_payload stage=gateway` and return phase-appropriate guidance (`AWAITING_PHOTO_NUDGE` in `AWAITING_PHOTO`, `INTERVIEW_PHOTO_GUARD_MESSAGE` in `INTERVIEWING`, stage retry in `GENERATING_MEDIA`, or `INTERVIEW_COMPLETE_NUDGE` in `COMPLETED`).
- **Direct Multimodal Fusion:** The `Converter` (`src/telegram_documentaries/converter.py`) passes the original portrait photo bytes (`types.Part.from_bytes`) and the structured `BehavioralDossier` directly to `gemini-3.1-flash-image-preview` (`response_modalities=["IMAGE", "TEXT"]`) in a single multimodal call without an intermediate text-only image description hop, validates the inline image via `GeneratedImage`, and streams the resulting image back to Telegram via `TelegramClient.send_photo`.
- **Deterministic TTS Synthesis & Temporary File Lifecycle:** The `Narrator` (`src/telegram_documentaries/narrator.py`) calls `gemini-3.1-flash-tts-preview` (`response_modalities=["AUDIO"]`, `SpeechConfig`/`VoiceConfig`/`PrebuiltVoiceConfig(voice_name="Charon")`) with `NARRATOR_VOICE_PROMPT_PREFIX` (Nikolai Drozdov's warm, affectionate, grandfatherly Russian-accented naturalist voice), validates inline audio via `GeneratedAudio`, encodes raw PCM (`audio/l16` or `audio/pcm`) into MP3 via `lameenc` (or passes through `audio/mpeg` / `audio/mp3` / `audio/ogg`), writes the audio to a scoped temporary file via `Narrator.synthesize_to_temp_file`, registers the temporary path via `SessionStore.register_ephemeral_file`, and unconditionally cleans up the file in a `finally` block (`SessionStore.cleanup_ephemeral_files` and `Path.unlink(missing_ok=True)`) after `TelegramClient.send_voice`.

## Boundary Contracts & Validation Policy
- **Untrusted External Inputs:** All incoming Telegram updates, LLM structured outputs, image payloads, and TTS responses are treated as untrusted external data.
- **Pydantic at the Edge:** Parse and validate all external payloads into typed Pydantic models at system boundaries. Never pass raw dictionaries or unvalidated JSON across internal module boundaries.
- **Schemas Over Regexes:** Use structured output and boundary schemas (Pydantic v2 models) for agent and API boundaries:
  - `TelegramMessage` (`message_id: StrictInt`, `chat: TelegramChat`, `text: str | None = None`, `caption: str | None = None`, `photo: list[TelegramPhotoSize] | None = None`) for incoming and outgoing Telegram messages.
  - `BouncerVerdict` (`subject`, `is_human: StrictBool`, `rejection_message`) for the Bouncer vision gate.
  - `InterviewTurn` (`question`, `answer` with non-empty validators) for recorded Q&A turns.
  - `BehavioralDossier` (`summary`, `suggested_animal` with field descriptions and non-empty validators) for the compiled wildlife profile.
  - `InterviewerTurnResult` (`is_complete: StrictBool`, `next_question: str`, `dossier: BehavioralDossier | None`) for each turn of the `Interviewer` agent, with code-enforced bounds preventing completion before 5 answers and requiring completion by the 7th answer.
  - `GeneratedImage` (`image_bytes: bytes` rejecting empty bytes, `mime_type: str = "image/png"` requiring a valid `image/*` MIME type) for validating inline image parts returned by `Converter`.
  - `SendPhotoPayload` (`chat_id: StrictInt`, `photo_bytes: bytes` rejecting empty bytes, `caption: str | None = None` rejecting blank strings) and `SendPhotoResponse` (`ok: StrictBool`, `result: TelegramMessage`) for `TelegramClient.send_photo`.
  - `DocumentaryScript` (`narration: str` with single-paragraph whitespace normalization and markdown-stripping validators) for the `Scripter` agent.
  - `GeneratedAudio` (`audio_bytes: bytes` rejecting empty bytes, `mime_type: str = "audio/mpeg"` requiring a non-empty `audio/*` MIME type normalized to lowercase) for validating inline audio parts returned by `Narrator`.
  - `SendVoicePayload` (`chat_id: StrictInt`, `audio_bytes: bytes` rejecting empty bytes, `caption: str | None = None` rejecting blank strings) and `SendVoiceResponse` (`ok: StrictBool`, `result: TelegramMessage`) for `TelegramClient.send_voice`.

## Session State & Storage Policy
- **In-Memory Store:** Maintain a single shared in-memory `SessionStore` keyed by `chat_id: int` mapping to a Pydantic `SessionState` model (`chat_id`, `phase: SessionPhase`, `portrait: bytes | None`, `current_question: str | None`, `interview_history: list[InterviewTurn]`, `dossier: BehavioralDossier | None`, `hybrid_portrait: bytes | None`, `script: str | None`, and `ephemeral_files: list[Path]`).
- **3-Step Reset Routine & Temporary Media Cleanup (`SessionStore.reset`):** Calling `SessionStore.reset(chat_id)` (triggered by `/start`, `/restart`, Bouncer rejection, or Question 1 failure) executes an explicit three-part reset without restarting the Python process:
  1. **Cancel Active Sessions:** Sets `phase = SessionPhase.AWAITING_PHOTO` and `current_question = None`.
  2. **Purge Ephemeral Assets:** Unlinks all tracked paths in `ephemeral_files` on disk (`Path.unlink(missing_ok=True)` via `cleanup_ephemeral_files`) and clears in-memory binary payloads (`portrait = None`, `hybrid_portrait = None`, `ephemeral_files = []`).
  3. **Re-initialize Agent Memory:** Clears `interview_history = []`, `dossier = None`, and `script = None`, and emits `event=session_reset stage=session chat_id=<id>`. `start_interview` and `complete_documentary` also invoke `cleanup_ephemeral_files(chat_id)`.

## Logging & Error Handling Policy
- **Structured Logging via Decorators:** Use comprehensive structured logging (`@log_stage`) across pipeline stages (`polling`, `gateway`, `bouncer`, `interviewer`, `converter`, `scripter`, `narrator`, `session`, `telegram_api`) and emit domain events (`event=bouncer_verdict`, `event=interviewer_question`, `event=interviewer_dossier`, `event=converter_portrait_generated`, `event=scripter_narration_generated`, `event=narrator_audio_generated`, `event=session_reset`, `event=unsupported_payload`).
- **No Silent Failures:** Never use bare `except:`, `except Exception: pass`, or un-logged fallbacks.
- **Graceful User Degradation & Fallback Send Guards:**
  - On non-critical or background tasks, fail loudly and log full context.
  - On a user's active Telegram conversation path, catch upstream API or validation errors, log them loudly with stack traces and `chat_id` context, and reply to the user with a polite, in-character fallback message (`ERROR_FALLBACK_MESSAGE` on photo/Question 1 errors resetting to `AWAITING_PHOTO`; `INTERVIEW_ERROR_FALLBACK_MESSAGE` on mid-interview errors preserving `interview_history` and `current_question` so the user can retry; `CONVERTER_ERROR_FALLBACK_MESSAGE` on `Converter` or `sendPhoto` errors preserving `portrait` and `dossier` in `GENERATING_MEDIA` with `hybrid_portrait=None` so any subsequent message retries from the Converter stage; `SCRIPTER_ERROR_FALLBACK_MESSAGE` on Scripter or script delivery errors preserving `hybrid_portrait` with `script=None` so any subsequent message retries from the Scripter stage; `NARRATOR_ERROR_FALLBACK_MESSAGE` on `Narrator` or `sendVoice` errors preserving `hybrid_portrait` and `script` in `GENERATING_MEDIA` so any subsequent message retries only the Narrator stage).
  - Every fallback `client.send_message` call across `_handle_photo`, `_handle_interview_text`, `_deliver_hybrid_portrait`, `_deliver_script`, and `_deliver_voice_narration` is wrapped in `try/except TelegramClientError` (logging `event=*_fallback_failed stage=gateway chat_id=<id>`) so a secondary Telegram outage during error reporting never crashes the polling loop.

## Testing & Verification Policy (Red/Green TDD)
- **Test-First Workflow:** Every feature and bug fix follows strict Red/Green TDD: write failing behavioral tests first, implement minimal code to pass, then refactor.
- **Developer Scripts (`scripts/`):**
  - `scripts/test`: Runs the full automated test suite (`pytest`).
  - `scripts/hooks`: Runs static analysis, formatting, and type checks (`ruff check`, `ruff format --check`, `mypy`).
- **Mocked External Boundaries in Unit Tests:** Unit and integration tests must mock Telegram API calls and Gemini SDK endpoints deterministically so the test suite runs fast, offline, and without consuming API quota.

## Repository Hygiene & Documentation Policy
- **Secrets Protection:** `.env` must remain in `.gitignore`. `.env.example` documents required environment variables with safe placeholders.
- **Synchronized Documentation:** `README.md`, `SPECS/TECH.md`, `SPECS/MISSION.md`, and `SPECS/ROADMAP.md` must stay synchronized with implemented commands, scripts, and architecture after every verified feature.
