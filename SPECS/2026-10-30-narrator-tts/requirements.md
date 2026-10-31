# Phase 6 Requirements: The Narrator (Gemini TTS Audio Synthesis)

## Context & Goal
Phase 5 implemented **The Scripter** (`gemini-3.1-flash-lite-preview`), which transforms the user's `BehavioralDossier` into a single-paragraph (~60–90 words) comedy wildlife documentary script, stores it in `SessionState.script`, and delivers it as a Telegram text message immediately after the hybrid animal portrait (`Converter`).

Phase 6 implements **The Narrator** (`gemini-3.1-flash-tts-preview`), a deterministic Text-to-Speech (TTS) execution tool (not an autonomous reasoning agent) that synthesizes the Scripter's English documentary paragraph into spoken audio using a warm, affectionate, grandfatherly Russian-accented naturalist voice inspired by **Nikolai Drozdov** (*"In the World of Animals" / «В мире животных»*). The synthesized audio is packaged into a Telegram-compatible audio format (MP3/OGG), persisted to a scoped temporary audio file during delivery (`TelegramClient.send_voice` / `send_audio`), immediately cleaned up from disk, and transitions the user's session to `SessionPhase.COMPLETED`.

Roadmap reference: Phase 6 in `SPECS/ROADMAP.md`.

## User-Visible Behaviour
| Situation | Bot response | Session effect |
|---|---|---|
| Photo containing a human (`AWAITING_PHOTO` or re-upload in any phase) | Bouncer approves; Interviewer generates and sends **Question 1** | Portrait stored; `interview_history=[]`; `dossier=None`; `hybrid_portrait=None`; `script=None`; `current_question` set to Question 1; phase → `INTERVIEWING` |
| Text sent while `INTERVIEWING` (final answer, between turn 5 and turn 7 inclusive) | Records final `(current_question, user_answer)` and `BehavioralDossier`; runs `Converter.generate_hybrid_portrait` + `send_photo`; runs `Scripter.generate_script` + `send_message`; immediately runs `Narrator` TTS synthesis + temporary audio file upload via `send_voice` | `dossier` stored; `current_question=None`; `hybrid_portrait` stored; `script` stored; temporary audio file deleted; phase → `COMPLETED` |
| Gemini / Telegram error during Converter image synthesis or `sendPhoto` | Logs full traceback (`event=converter_failed stage=gateway`); sends `CONVERTER_ERROR_FALLBACK_MESSAGE` | Preserves `portrait` and `dossier` in `GENERATING_MEDIA` with `hybrid_portrait=None` and `script=None` so a follow-up message retries from the Converter stage |
| Gemini / Telegram error during Scripter generation or `sendMessage` (after `hybrid_portrait` succeeded) | Logs full traceback (`event=scripter_failed stage=gateway`); sends `SCRIPTER_ERROR_FALLBACK_MESSAGE` | Preserves `hybrid_portrait` (`hybrid_portrait is not None`, `script=None`) in `GENERATING_MEDIA` so a follow-up message skips the Converter and retries from the Scripter stage |
| Gemini / Telegram error during Narrator TTS synthesis or `sendVoice` (after `hybrid_portrait` and `script` succeeded) | Logs full traceback (`event=narrator_failed stage=gateway`); cleans up any temporary audio file; sends `NARRATOR_ERROR_FALLBACK_MESSAGE` | Preserves `hybrid_portrait` and `script` in `GENERATING_MEDIA` (does not transition to `COMPLETED`) so a follow-up message skips both Converter and Scripter and retries **only** the Narrator TTS stage |
| Text sent while `GENERATING_MEDIA` when `hybrid_portrait is None` | Retries Converter → Scripter → Narrator in sequence | Stores artifacts as each stage succeeds; transitions to `COMPLETED` when Narrator finishes |
| Text sent while `GENERATING_MEDIA` when `hybrid_portrait is not None` and `script is None` | Skips Converter; retries Scripter → Narrator in sequence | Stores `script` and transitions to `COMPLETED` when Narrator finishes |
| Text sent while `GENERATING_MEDIA` when both `hybrid_portrait is not None` and `script is not None` | Skips Converter and Scripter; retries **only** Narrator TTS + `send_voice` | On success, transitions phase → `COMPLETED`; on failure, logs `event=narrator_failed` and sends `NARRATOR_ERROR_FALLBACK_MESSAGE` |
| Text sent while `COMPLETED` | Polite in-character nudge (`INTERVIEW_COMPLETE_NUDGE`) noting field research is complete and offering `/restart` | Unchanged (`COMPLETED`) |
| `/start` or `/restart` in any phase (including `COMPLETED`) | `AWAITING_PHOTO_NUDGE` | Full reset (`portrait=None`, `interview_history=[]`, `current_question=None`, `dossier=None`, `hybrid_portrait=None`, `script=None`, phase → `AWAITING_PHOTO`) |

## Decisions (explicit, no legacy)
1. **Model & Deterministic Execution Tool (`Narrator`):**
   - Route TTS requests to `gemini-3.1-flash-tts-preview` (`DEFAULT_NARRATOR_MODEL = "gemini-3.1-flash-tts-preview"`), matching `SPECS/TECH.md` and `SPECS/MISSION.md`.
   - `Narrator` is a deterministic execution tool (`src/telegram_documentaries/narrator.py`), not an autonomous reasoning agent.
2. **Nikolai Drozdov Voice Direction & Prebuilt Voice Config:**
   - Keep the English documentary script produced by `Scripter` and configure `Narrator` to synthesize speech in a warm, affectionate, enthusiastic, grandfatherly Russian-accented naturalist voice inspired by **Nikolai Drozdov** (host of the classic wildlife TV program *"In the World of Animals" / «В мире животных»*).
   - Pass `types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=DEFAULT_NARRATOR_VOICE))))` (with `DEFAULT_NARRATOR_VOICE = "Charon"`).
   - Prefix the TTS prompt (`NARRATOR_VOICE_PROMPT_PREFIX`) instructing the TTS model to read the script aloud in the warm, kindly, slightly raspy, grandfatherly Russian-accented voice and joyful cadence of Nikolai Drozdov hosting *"In the World of Animals"*, reading only the provided narration text aloud.
3. **Telegram-Compatible Audio Format (MP3/OGG) & Scoped Temporary File Lifecycle:**
   - Gemini TTS returns inline audio bytes (typically `audio/L16;rate=24000` raw 16-bit little-endian mono PCM at 24 kHz, or an encoded `audio/mp3` / `audio/ogg` / `audio/mpeg` stream).
   - Validate the Gemini inline audio part with a strict Pydantic v2 model `GeneratedAudio(audio_bytes: bytes, mime_type: str)`.
   - Convert raw PCM (`audio/l16` or `audio/pcm`) into a valid Telegram-compatible MP3 audio stream (or OGG/MP3 container) so Telegram clients can play it natively, while passing through already-encoded `audio/mpeg` / `audio/mp3` / `audio/ogg` payloads unchanged.
   - Persist the synthesized audio bytes to a temporary file on disk (`tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)`) during delivery (`Narrator.synthesize_to_file` or a context-managed helper) and guarantee in a `try ... finally` block that the temporary file is unlinked immediately after `TelegramClient.send_voice` completes or raises, leaving zero temporary files behind on success, failure, or `/restart`.
4. **Stage-Aware Retry & `SessionPhase.COMPLETED`:**
   - Add `COMPLETED = "completed"` to `SessionPhase` and `@log_stage("session") def complete_documentary(self, chat_id: int) -> SessionState` to `SessionStore`.
   - When `Narrator` + `send_voice` succeeds, call `sessions.complete_documentary(chat_id)` to transition the session from `GENERATING_MEDIA` to `COMPLETED`.
   - If `Narrator` or `send_voice` fails, log `event=narrator_failed stage=gateway chat_id=...` with traceback, send `NARRATOR_ERROR_FALLBACK_MESSAGE = "My field microphone crackled out — send any message to retry recording your voiceover, or /restart to begin anew!"`, and keep the session in `GENERATING_MEDIA` with `hybrid_portrait` and `script` stored so the user's next message retries **only** the Narrator step.
5. **Observability:**
   - Decorate `Narrator.synthesize_speech` with `@log_stage("narrator")`, `TelegramClient.send_voice` with `@log_stage("telegram_api")`, and `SessionStore.complete_documentary` with `@log_stage("session")`.
   - Emit `event=narrator_audio_generated stage=narrator voice=%r mime_type=%r bytes=%d` at `INFO` level.

## Boundary Contracts (Pydantic v2)
- `GeneratedAudio`:
  - `audio_bytes: bytes` (validator rejecting empty `b""`)
  - `mime_type: str = "audio/mpeg"` (validator requiring a non-empty `audio/*` MIME type, normalized to lowercase)
- `SendVoicePayload`:
  - `chat_id: StrictInt`
  - `audio_bytes: bytes` (validator rejecting empty `b""`)
  - `caption: str | None = None` (validator rejecting empty/whitespace-only caption when provided)
- `SendVoiceResponse`:
  - `ok: StrictBool`
  - `result: TelegramMessage`
- `SessionPhase`:
  - `AWAITING_PHOTO = "awaiting_photo"`
  - `INTERVIEWING = "interviewing"`
  - `GENERATING_MEDIA = "generating_media"`
  - `COMPLETED = "completed"`

## Out of Scope (YAGNI)
- Multi-speaker dialogues or background music mixing.
- Video synthesis or persistent cloud storage of audio files.

## Acceptance Criteria
- Answering the final interview question runs the Converter (`sendPhoto`), then the Scripter (`sendMessage`), and immediately runs `Narrator` (`gemini-3.1-flash-tts-preview`) with Nikolai Drozdov's warm naturalist voice direction, writes the Telegram-compatible audio to a temporary file, uploads it via `TelegramClient.send_voice`, deletes the temporary file, and transitions the session to `SessionPhase.COMPLETED`.
- `GeneratedAudio`, `SendVoicePayload`, and `SendVoiceResponse` enforce strict Pydantic v2 validation at the Gemini TTS and Telegram `sendVoice` boundaries.
- Raw PCM (`audio/L16` / `audio/pcm`) returned by Gemini TTS is converted into a valid Telegram-compatible MP3/OGG payload before upload.
- Temporary audio files are always deleted in a `finally` block whether synthesis/upload succeeds or fails.
- If `Narrator` or `send_voice` fails after `hybrid_portrait` and `script` were delivered, `NARRATOR_ERROR_FALLBACK_MESSAGE` is sent, `hybrid_portrait` and `script` remain stored in `GENERATING_MEDIA`, and a subsequent text message retries **only** the Narrator step without re-running Converter or Scripter.
- Once in `COMPLETED`, subsequent text messages reply with `INTERVIEW_COMPLETE_NUDGE`.
- `/start` and `/restart` reset the session from any phase (including `COMPLETED`) to `AWAITING_PHOTO`.
- `./scripts/test` and `./scripts/hooks` pass completely.
