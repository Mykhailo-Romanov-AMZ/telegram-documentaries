# Phase 7 Requirements: Pipeline Resilience, Session Resets & Edge Guards

## Context & Goal
The documentary pipeline spans five distinct phases: entry filtering via the Bouncer, 5–7 stateful interview questions via the Interviewer, multimodal image synthesis via the Converter, documentary script writing via the Scripter, and TTS audio rendering via the Narrator.

Phase 7 hardens state transitions across all phases so that:
1. Sending `/restart` or `/start` at any point executes an explicit three-part reset routine for the requesting `chat_id` without restarting the Python process:
   - **Cancel Active Sessions:** Terminate any active interview loop or waiting media state (`INTERVIEWING`, `GENERATING_MEDIA`, `COMPLETED`).
   - **Purge Ephemeral Assets:** Delete any temporary media files on disk tracked for `chat_id` (original photo, converted PNG, generated OGG/MP3 voice files) and clear in-memory binary payloads.
   - **Re-initialize Agent Memory:** Clear conversation history (`interview_history`), `current_question`, `dossier`, `hybrid_portrait`, and `script`, returning the session to `SessionPhase.AWAITING_PHOTO`.
2. Out-of-order payloads (such as uploading a photo during `INTERVIEWING`, sending text during `AWAITING_PHOTO`, or sending unsupported media types like stickers/voice notes) are caught by explicit phase guards that guide the user without corrupting session state.
3. Upstream Gemini or Telegram API errors/timeouts are caught and logged loudly at every stage, offering a retry without crashing the polling loop.

Roadmap reference: Phase 7 in `SPECS/ROADMAP.md`.

## User-Visible Behaviour
| Situation | Bot response | Session effect |
|---|---|---|
| `/start` or `/restart` in any phase (as message text or photo caption) | `AWAITING_PHOTO_NUDGE` | Cancels active session, deletes any tracked ephemeral files on disk (`ephemeral_files`), clears `portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`, `script`, and transitions phase → `AWAITING_PHOTO` |
| Text sent while `AWAITING_PHOTO` | `AWAITING_PHOTO_NUDGE` instructing the user to upload a clear human portrait photo | Unchanged (`AWAITING_PHOTO`) |
| Photo sent while `AWAITING_PHOTO` (human) | Bouncer approves; Interviewer generates and sends **Question 1** | Stores `portrait` and `current_question`; phase → `INTERVIEWING` |
| Photo sent while `AWAITING_PHOTO` (non-human) | Bouncer's cheeky rejection message | Purges session via `sessions.reset(chat_id)` → `AWAITING_PHOTO` |
| Photo sent while `INTERVIEWING` | `INTERVIEW_PHOTO_GUARD_MESSAGE` prompting the user to finish answering the current question with text, or type `/restart` to start fresh (does NOT call Bouncer or overwrite the active interview) | Preserves `portrait`, `current_question`, and `interview_history` in `INTERVIEWING` |
| Photo sent while `GENERATING_MEDIA` | Retries the pending media generation stage without overwriting `portrait` or `dossier` | Preserves `GENERATING_MEDIA` artifacts and retries pending stage |
| Photo sent while `COMPLETED` | `INTERVIEW_COMPLETE_NUDGE` prompting `/restart` to begin a fresh documentary | Unchanged (`COMPLETED`) |
| Unsupported payload (sticker, voice, document, video, etc.) in any phase | Phase-appropriate guidance nudge (`AWAITING_PHOTO_NUDGE` in `AWAITING_PHOTO`, `INTERVIEW_PHOTO_GUARD_MESSAGE` in `INTERVIEWING`, `INTERVIEW_COMPLETE_NUDGE` in `COMPLETED`, or stage retry in `GENERATING_MEDIA`) | Preserves session state |
| Upstream Gemini or Telegram API error/timeout (including fallback `sendMessage` failure) | Logs `event=*_failed` with full traceback and `chat_id`; sends stage-specific retry message (`ERROR_FALLBACK_MESSAGE`, `INTERVIEW_ERROR_FALLBACK_MESSAGE`, `CONVERTER_ERROR_FALLBACK_MESSAGE`, `SCRIPTER_ERROR_FALLBACK_MESSAGE`, `NARRATOR_ERROR_FALLBACK_MESSAGE`) | Preserves recoverable state (or resets to `AWAITING_PHOTO` on Question 1 failure) without crashing polling |

## Decisions (explicit, no legacy)
1. **Explicit 3-Step Reset Routine (`SessionStore.reset`):**
   - `SessionState` tracks `ephemeral_files: list[Path] = Field(default_factory=list)` alongside in-memory artifacts.
   - `SessionStore.register_ephemeral_file(chat_id: int, path: Path)` registers any temporary disk file created for a chat (and `SessionStore.cleanup_ephemeral_files(chat_id: int)` unlinks all tracked paths with `missing_ok=True` and clears the list).
   - `SessionStore.reset(chat_id: int)` executes:
     1. **Cancel Active Sessions:** Resets `state.phase = SessionPhase.AWAITING_PHOTO` and `state.current_question = None`.
     2. **Purge Ephemeral Assets:** Unlinks all paths in `state.ephemeral_files` from disk (`Path.unlink(missing_ok=True)`) and clears `state.portrait = None` and `state.hybrid_portrait = None`.
     3. **Re-initialize Agent Memory:** Clears `state.interview_history = []`, `state.dossier = None`, and `state.script = None`, and emits `event=session_reset stage=session chat_id=%s`.
2. **No Legacy Replacement-Photo Behavior in `INTERVIEWING`:**
   - Replace the old Phase 3 behavior (where a photo uploaded during `INTERVIEWING` overwrote the session) with an explicit phase guard: `_handle_photo` checks `sessions.get(chat_id).phase` first.
   - If `phase == SessionPhase.INTERVIEWING`, do not download the photo or invoke `Bouncer`; immediately send `INTERVIEW_PHOTO_GUARD_MESSAGE` (prompting the user to finish answering the current question with text or type `/restart` to start fresh) while preserving the active interview state.
   - If `phase == SessionPhase.COMPLETED`, send `INTERVIEW_COMPLETE_NUDGE` without calling `Bouncer`.
   - If `phase == SessionPhase.GENERATING_MEDIA`, trigger `_handle_generating_media_text` so any stuck media step retries cleanly without overwriting the portrait.
3. **Caption & Unsupported Media Handling:**
   - Extend `TelegramMessage` in `models.py` with `caption: str | None = None`.
   - If a message has a reset command in either `message.text` or `message.caption` (`/start` or `/restart`), execute the reset routine immediately before any photo/text phase routing.
   - If a message has neither `photo` nor `text`, route by the user's current `SessionPhase` to return a helpful guidance prompt instead of silently ignoring the message.
4. **Defensive Fallback Send Guards Across All Stages:**
   - Wrap fallback `client.send_message(...)` calls in `_handle_photo`, `_handle_interview_text`, and `_deliver_hybrid_portrait` (matching `_deliver_script` and `_deliver_voice_narration`) in `try/except TelegramClientError` so a transient Telegram outage during error reporting logs `event=*_fallback_failed` and never raises an unhandled exception.

## Boundary Contracts (Pydantic v2)
- `TelegramMessage`:
  - `message_id: StrictInt`
  - `chat: TelegramChat`
  - `text: str | None = None`
  - `caption: str | None = None`
  - `photo: list[TelegramPhotoSize] | None = None`
- `SessionState`:
  - `chat_id: StrictInt`
  - `phase: SessionPhase = SessionPhase.AWAITING_PHOTO`
  - `portrait: bytes | None = None`
  - `current_question: str | None = None`
  - `interview_history: list[InterviewTurn] = Field(default_factory=list)`
  - `dossier: BehavioralDossier | None = None`
  - `hybrid_portrait: bytes | None = None`
  - `script: str | None = None`
  - `ephemeral_files: list[Path] = Field(default_factory=list)`

## Acceptance Criteria
- `/start` and `/restart` (in `text` or `caption`) cancel active sessions, delete any tracked ephemeral files on disk for that `chat_id`, clear all agent memory fields, log `event=session_reset stage=session`, and reply with `AWAITING_PHOTO_NUDGE`.
- Sending a photo during `INTERVIEWING` replies with `INTERVIEW_PHOTO_GUARD_MESSAGE` asking the user to answer the current question with text or type `/restart`, without calling `Bouncer` or altering `interview_history`.
- Sending a photo during `COMPLETED` replies with `INTERVIEW_COMPLETE_NUDGE` without calling `Bouncer`.
- Sending text during `AWAITING_PHOTO` replies with `AWAITING_PHOTO_NUDGE`.
- Sending an unsupported media message (neither `photo` nor `text`) replies with a phase-appropriate prompt rather than silently dropping the update.
- All Gemini and Telegram API calls (including error fallback `sendMessage` calls) catch and log errors with `chat_id` and traceback without crashing the polling loop.
- Two consecutive end-to-end documentary runs for the same `chat_id` succeed back-to-back in a single process with clean state transitions.
- `./scripts/test` and `./scripts/hooks` pass completely.
