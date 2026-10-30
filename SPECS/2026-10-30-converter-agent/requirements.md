# Phase 4 Requirements: The Converter Agent (Hybrid Animal Portrait)

## Context & Goal
Phase 3 implemented **The Interviewer** (`gemini-3.1-flash-lite-preview`), which conducts a sequential 5–7 question behavioral interview, compiles a validated `BehavioralDossier` (`summary` + `suggested_animal`), and transitions the user's session to `GENERATING_MEDIA`.

Phase 4 implements **The Converter** (`gemini-3.1-flash-image-preview`), the multimodal image transformation specialist agent. Immediately upon completing the final interview question, the Converter takes both the user's original portrait photo bytes and the `BehavioralDossier` in a single direct multimodal call to `gemini-3.1-flash-image-preview` (no intermediate text-only image description hop), synthesizes a human-animal hybrid portrait that fuses the user's facial features, posture, and composition with their spirit animal and behavioral quirks, and sends the rendered image directly to the user's Telegram chat via `sendPhoto`.

Roadmap reference: Phase 4 in `SPECS/ROADMAP.md`.

## User-Visible Behaviour
| Situation | Bot response | Session effect |
|---|---|---|
| Photo containing a human (`AWAITING_PHOTO` or re-upload) | Bouncer approves; Interviewer generates and sends **Question 1** | Portrait stored; `interview_history=[]`; `dossier=None`; `hybrid_portrait=None`; `current_question` set to Question 1; phase → `INTERVIEWING` |
| Text sent while `INTERVIEWING` (final answer, between turn 5 and turn 7 inclusive) | Records final `(current_question, user_answer)` and `BehavioralDossier`; immediately invokes `Converter.generate_hybrid_portrait(portrait, dossier)` and sends the rendered hybrid portrait image via `TelegramClient.send_photo` (replaces Phase 3 interim text dossier message) | `dossier` stored; `current_question = None`; `hybrid_portrait` stored; phase → `GENERATING_MEDIA` |
| Gemini / Telegram error during Converter image synthesis or `sendPhoto` | Logs full traceback (`event=converter_failed stage=gateway`); sends `CONVERTER_ERROR_FALLBACK_MESSAGE` | Preserves `portrait` and `dossier` in `GENERATING_MEDIA` with `hybrid_portrait=None` so the user can retry without redoing the interview |
| Text sent while `GENERATING_MEDIA` when `hybrid_portrait is None` (after a Converter failure) | Retries `Converter.generate_hybrid_portrait(portrait, dossier)` and `send_photo` | On success, stores `hybrid_portrait` and sends the photo; on failure, logs and sends `CONVERTER_ERROR_FALLBACK_MESSAGE` |
| Text sent while `GENERATING_MEDIA` when `hybrid_portrait is not None` (portrait already delivered) | Polite in-character nudge (`INTERVIEW_COMPLETE_NUDGE`) noting field research is complete and offering `/restart` | Unchanged (`GENERATING_MEDIA`) |
| `/start` or `/restart` in any phase | `AWAITING_PHOTO_NUDGE` | Full reset (`portrait=None`, `interview_history=[]`, `current_question=None`, `dossier=None`, `hybrid_portrait=None`, phase → `AWAITING_PHOTO`) |

## Decisions (explicit, no legacy)
1. **Model Selection:** Route Converter image synthesis requests to `gemini-3.1-flash-image-preview` (`DEFAULT_CONVERTER_MODEL = "gemini-3.1-flash-image-preview"`), matching `SPECS/TECH.md` and `SPECS/MISSION.md`.
2. **Pure Multimodal Routing (No Intermediate Text Step):** Pass the user's raw portrait bytes (`types.Part.from_bytes(data=portrait_bytes, mime_type="image/jpeg")`) and the textual prompt incorporating `dossier.suggested_animal` and `dossier.summary` together in a single `client.models.generate_content` call with `response_modalities=["IMAGE", "TEXT"]`.
3. **Hybrid Portrait Replaces Interim Dossier Text Message:** Upon completing the final interview question, the bot sends **only** the synthesized hybrid animal portrait via `TelegramClient.send_photo` (replacing the Phase 3 interim `format_dossier_message` text reply, as Phase 5's Scripter will provide the documentary narration text).
4. **Error Recovery Preserves Dossier:** If `Converter.generate_hybrid_portrait` or `TelegramClient.send_photo` fails, log the exception with `event=converter_failed stage=gateway`, keep `portrait` and `dossier` in `GENERATING_MEDIA` (`hybrid_portrait=None`), and send `CONVERTER_ERROR_FALLBACK_MESSAGE` (`"My darkroom chemicals fizzled out — send any message to retry developing your portrait, or /restart to begin anew!"`). Any non-command text sent while `phase == SessionPhase.GENERATING_MEDIA` and `hybrid_portrait is None` retries the Converter synthesis and delivery.
5. **Observability:** Decorate `Converter.generate_hybrid_portrait` with `@log_stage("converter")` and `TelegramClient.send_photo` with `@log_stage("telegram_api")`. Emit `event=converter_portrait_generated stage=converter suggested_animal=%r mime_type=%r bytes=%d` at `INFO` level when an image is synthesized.

## Boundary Contracts (Pydantic v2)
- `GeneratedImage`:
  - `image_bytes: bytes` (non-empty validator rejecting `b""`)
  - `mime_type: str = "image/png"` (validator requiring a non-empty `image/*` MIME type)
- `SendPhotoPayload`:
  - `chat_id: StrictInt`
  - `photo_bytes: bytes` (non-empty validator rejecting `b""`)
  - `caption: str | None = None`
- `SendPhotoResponse`:
  - `ok: StrictBool`
  - `result: TelegramMessage`
- `SessionState`:
  - `chat_id: StrictInt`
  - `phase: SessionPhase = SessionPhase.AWAITING_PHOTO`
  - `portrait: bytes | None = None`
  - `current_question: str | None = None`
  - `interview_history: list[InterviewTurn] = Field(default_factory=list)`
  - `dossier: BehavioralDossier | None = None`
  - `hybrid_portrait: bytes | None = None`

## Out of Scope (YAGNI)
- Documentary narration text generation (`Scripter`, Phase 5) and TTS voice note synthesis (`Narrator`, Phase 6).
- Persistent database or disk-cached image galleries across bot restarts.

## Acceptance Criteria
- Answering the final interview question (turn 5–7) stores the `BehavioralDossier`, transitions the session to `GENERATING_MEDIA`, calls `Converter.generate_hybrid_portrait(portrait, dossier)` with `gemini-3.1-flash-image-preview`, stores `hybrid_portrait`, and delivers the image to the chat via `TelegramClient.send_photo`.
- Both the raw portrait photo bytes and the behavioral dossier (`suggested_animal` + `summary`) are passed in a single multimodal `generate_content` call without an intermediate text-only image description hop.
- Empty, safety-blocked, text-only (no inline image part), or malformed Gemini responses raise `ConverterError` with diagnostic context.
- If Converter synthesis or `sendPhoto` fails, the bot logs `event=converter_failed`, sends `CONVERTER_ERROR_FALLBACK_MESSAGE`, and preserves `portrait` and `dossier` in `GENERATING_MEDIA` so the user can retry with any text message.
- `/start` and `/restart` clear `portrait`, `current_question`, `interview_history`, `dossier`, and `hybrid_portrait` and return the chat to `AWAITING_PHOTO`.
- Separate `chat_id` sessions remain 100% isolated.
- `./scripts/test` and `./scripts/hooks` pass completely.
