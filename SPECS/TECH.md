# Technical Contract: The Telegram Documentaries

## Technology Stack
- **Language:** Python 3.11+
- **Agent Framework:** Google Agent Development Kit (ADK) & `google-genai` SDK
- **Telegram Integration:** Telegram Bot API via **Long Polling** (no webhooks)
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
| **The Narrator** | Text-to-Speech audio rendering (OGG/MP3) | `gemini-3.1-flash-tts-preview` | Execution Tool |

## Architecture: Hub-and-Spoke State Machine
- **Orchestrator Pattern:** The **Interviewer** (`Interviewer` in `src/telegram_documentaries/interviewer.py`) doubles as the central orchestrator in a hub-and-spoke architecture, managing conversation state and delegating work to the specialist agents (Bouncer, Converter, Scripter) and the TTS execution tool (Narrator).
- **Explicit Session Phases (`SessionPhase`):** Each user conversation is tracked via an explicit state machine enum (`AWAITING_PHOTO` -> `INTERVIEWING` -> `GENERATING_MEDIA`):
  - `AWAITING_PHOTO`: Initial state after `/start`, `/restart`, or Bouncer rejection; expects a user portrait photo.
  - `INTERVIEWING`: Human photo verified; `Interviewer.start_interview` generates Question 1 from the portrait, and `Interviewer.advance_interview` conducts a bounded 5–7 turn sequential interview (`MIN_INTERVIEW_QUESTIONS = 5`, `MAX_INTERVIEW_QUESTIONS = 7`), accumulating `interview_history: list[InterviewTurn]` and tracking `current_question`.
  - `GENERATING_MEDIA`: Entered when the 5–7 question interview completes (`SessionStore.complete_interview`), storing the validated `BehavioralDossier` (`summary` + `suggested_animal`), invoking `Converter.generate_hybrid_portrait` (`gemini-3.1-flash-image-preview`), storing `hybrid_portrait` via `SessionStore.set_hybrid_portrait`, and delivering the synthesized hybrid animal portrait to Telegram via `TelegramClient.send_photo`; sequentially invokes Scripter and Narrator TTS in subsequent phases.
  - `COMPLETED`: Documentary delivered; prompts user to `/restart` for a new documentary.
- **Direct Multimodal Fusion:** The `Converter` (`src/telegram_documentaries/converter.py`) passes the original portrait photo bytes (`types.Part.from_bytes`) and the structured `BehavioralDossier` directly to `gemini-3.1-flash-image-preview` (`response_modalities=["IMAGE", "TEXT"]`) in a single multimodal call without an intermediate text-only image description hop, validates the inline image via `GeneratedImage`, and streams the resulting image back to Telegram via `TelegramClient.send_photo`.

## Boundary Contracts & Validation Policy
- **Untrusted External Inputs:** All incoming Telegram updates, LLM structured outputs, image payloads, and TTS responses are treated as untrusted external data.
- **Pydantic at the Edge:** Parse and validate all external payloads into typed Pydantic models at system boundaries. Never pass raw dictionaries or unvalidated JSON across internal module boundaries.
- **Schemas Over Regexes:** Use structured output and boundary schemas (Pydantic v2 models) for agent and API boundaries:
  - `BouncerVerdict` (`subject`, `is_human: StrictBool`, `rejection_message`) for the Bouncer vision gate.
  - `InterviewTurn` (`question`, `answer` with non-empty validators) for recorded Q&A turns.
  - `BehavioralDossier` (`summary`, `suggested_animal` with field descriptions and non-empty validators) for the compiled wildlife profile.
  - `InterviewerTurnResult` (`is_complete: StrictBool`, `next_question: str`, `dossier: BehavioralDossier | None`) for each turn of the `Interviewer` agent, with code-enforced bounds preventing completion before 5 answers and requiring completion by the 7th answer.
  - `GeneratedImage` (`image_bytes: bytes` rejecting empty bytes, `mime_type: str = "image/png"` requiring a valid `image/*` MIME type) for validating inline image parts returned by `Converter`.
  - `SendPhotoPayload` (`chat_id: StrictInt`, `photo_bytes: bytes` rejecting empty bytes, `caption: str | None = None` rejecting blank strings) and `SendPhotoResponse` (`ok: StrictBool`, `result: TelegramMessage`) for `TelegramClient.send_photo`.

## Session State & Storage Policy
- **In-Memory Store:** Maintain a single shared in-memory `SessionStore` keyed by `chat_id: int` mapping to a Pydantic `SessionState` model (`chat_id`, `phase: SessionPhase`, `portrait: bytes | None`, `current_question: str | None`, `interview_history: list[InterviewTurn]`, `dossier: BehavioralDossier | None`, and `hybrid_portrait: bytes | None`).
- **Temporary Media Cleanup:** In-memory session artifacts (`portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`) and any temporary files (synthesized audio/image files) must be scoped to the user's session and cleared immediately upon `/start`, `/restart`, Bouncer rejection, or pipeline completion.

## Logging & Error Handling Policy
- **Structured Logging via Decorators:** Use comprehensive structured logging (`@log_stage`) across pipeline stages (`polling`, `gateway`, `bouncer`, `interviewer`, `converter`, `session`, `telegram_api`) and emit domain events (`event=bouncer_verdict`, `event=interviewer_question`, `event=interviewer_dossier`, `event=converter_portrait_generated`).
- **No Silent Failures:** Never use bare `except:`, `except Exception: pass`, or un-logged fallbacks.
- **Graceful User Degradation:**
  - On non-critical or background tasks, fail loudly and log full context.
  - On a user's active Telegram conversation path, catch upstream API or validation errors, log them loudly with stack traces and `chat_id` context, and reply to the user with a polite, in-character fallback message (`ERROR_FALLBACK_MESSAGE` on photo/Question 1 errors resetting to `AWAITING_PHOTO`; `INTERVIEW_ERROR_FALLBACK_MESSAGE` on mid-interview errors preserving `interview_history` and `current_question` so the user can retry; `CONVERTER_ERROR_FALLBACK_MESSAGE` on `Converter` or `sendPhoto` errors preserving `portrait` and `dossier` in `GENERATING_MEDIA` with `hybrid_portrait=None` so any subsequent message retries synthesis).

## Testing & Verification Policy (Red/Green TDD)
- **Test-First Workflow:** Every feature and bug fix follows strict Red/Green TDD: write failing behavioral tests first, implement minimal code to pass, then refactor.
- **Developer Scripts (`scripts/`):**
  - `scripts/test`: Runs the full automated test suite (`pytest`).
  - `scripts/hooks`: Runs static analysis, formatting, and type checks (`ruff check`, `ruff format --check`, `mypy`).
- **Mocked External Boundaries in Unit Tests:** Unit and integration tests must mock Telegram API calls and Gemini SDK endpoints deterministically so the test suite runs fast, offline, and without consuming API quota.

## Repository Hygiene & Documentation Policy
- **Secrets Protection:** `.env` must remain in `.gitignore`. `.env.example` documents required environment variables with safe placeholders.
- **Synchronized Documentation:** `README.md`, `SPECS/TECH.md`, `SPECS/MISSION.md`, and `SPECS/ROADMAP.md` must stay synchronized with implemented commands, scripts, and architecture after every verified feature.
