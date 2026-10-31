# Phase 6 Implementation Plan: The Narrator (Gemini TTS Audio Synthesis)

All implementation follows **Red/Green TDD**: write failing tests first, run `./scripts/test` to confirm failure, implement the minimal code to pass, and verify with `./scripts/test` and `./scripts/hooks`. Do not commit during implementation.

---

## Task Group 1: Boundary Schemas (`GeneratedAudio`, `SendVoicePayload`, `SendVoiceResponse`), `SessionPhase.COMPLETED` & `TelegramClient.send_voice` (TDD)
1. **Red — extend `tests/test_models.py`, `tests/test_session.py`, and `tests/test_telegram_client.py`:**
   - In `tests/test_models.py`:
     - Validate `GeneratedAudio(audio_bytes=b"ID3...", mime_type="audio/mpeg")` accepts non-empty bytes and normalizes `audio/*` MIME types (including parameters like `"audio/L16;rate=24000"`), and rejects empty `audio_bytes` or non-`audio/*` MIME types with `ValidationError`.
     - Validate `SendVoicePayload` requires `StrictInt` `chat_id`, non-empty `audio_bytes`, and optional non-blank `caption`.
     - Validate `SendVoiceResponse` parses a valid `{"ok": True, "result": ...}` envelope and rejects malformed responses.
   - In `tests/test_session.py`:
     - Verify `SessionPhase.COMPLETED == "completed"`.
     - Verify `complete_documentary(chat_id)` transitions the chat to `SessionPhase.COMPLETED` while keeping session isolation across `chat_id`s.
     - Verify `start_interview` and `reset` transition a `COMPLETED` session back to `INTERVIEWING` / `AWAITING_PHOTO` and clear prior artifacts.
   - In `tests/test_telegram_client.py`:
     - Verify `TelegramClient.send_voice(chat_id=12345, audio_bytes=b"...", filename="narration.mp3", mime_type="audio/mpeg", caption=...)` posts multipart form data (`files={"voice": (filename, audio_bytes, mime_type)}`) to `/bot<token>/sendVoice` with `SEND_VOICE_TIMEOUT_SECONDS = 30`, validates the response via `SendVoiceResponse`, and raises `TelegramClientError` (without leaking the bot token) on empty bytes, invalid `chat_id`, HTTP errors, or malformed JSON.
2. **Green — update `src/telegram_documentaries/models.py`, `src/telegram_documentaries/session.py`, and `src/telegram_documentaries/telegram_client.py`:**
   - Add `GeneratedAudio`, `SendVoicePayload`, and `SendVoiceResponse` to `models.py`.
   - Add `COMPLETED = "completed"` to `SessionPhase` and `@log_stage("session") def complete_documentary(self, chat_id: int) -> SessionState` to `SessionStore`.
   - Add `SEND_VOICE_TIMEOUT_SECONDS = 30` and `@log_stage("telegram_api") def send_voice(...) -> TelegramMessage` to `TelegramClient`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 2: The Narrator TTS Tool (`src/telegram_documentaries/narrator.py`) & MP3 Packaging (TDD)
1. **Red — create `tests/test_narrator.py`** (mock `google.genai.Client`; no network):
   - Verify `Narrator` defaults to `DEFAULT_NARRATOR_MODEL = "gemini-3.1-flash-tts-preview"` and `DEFAULT_NARRATOR_VOICE = "Charon"`, and allows overriding `model_name` and `voice_name`.
   - Verify `synthesize_speech(script)` calls `client.models.generate_content` with:
     - `model="gemini-3.1-flash-tts-preview"`
     - `contents` containing the script and `NARRATOR_VOICE_PROMPT_PREFIX` instructing the model to speak in the warm, affectionate, grandfatherly Russian-accented naturalist voice of Nikolai Drozdov (*"In the World of Animals" / «В мире животных»*).
     - `config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name))))`.
   - Verify `synthesize_speech(script)`:
     - Converts raw PCM (`audio/L16;rate=24000` or `audio/pcm`) inline data into valid MP3 audio bytes (`audio/mpeg`) and logs `event=narrator_audio_generated stage=narrator voice=%r mime_type=%r bytes=%d`.
     - Passes through already-encoded `audio/mp3` / `audio/mpeg` / `audio/ogg` inline audio bytes cleanly.
   - Verify temporary file lifecycle (`narrator.synthesize_to_temp_file(script)` context manager):
     - Writes the synthesized Telegram-compatible audio bytes to a temporary `.mp3` / `.ogg` file on disk, yields the `Path` so the caller can read/upload it, and deletes the temporary file from disk when exiting the `with` block — both on normal completion and when an exception is raised inside the `with` block.
   - Verify `synthesize_speech` raises `NarratorError` when:
     - `script` is empty or whitespace-only.
     - Gemini returns no candidates, no inline audio part, or empty audio bytes (including safety `block_reason` / `finish_reason` diagnostics when present).
     - The `google-genai` SDK raises an exception (chained via `raise NarratorError(...) from exc`).
2. **Green — update `pyproject.toml` (if `lameenc` is needed for pure-Python PCM-to-MP3 encoding) and create `src/telegram_documentaries/narrator.py`:**
   - Implement `DEFAULT_NARRATOR_MODEL = "gemini-3.1-flash-tts-preview"`, `DEFAULT_NARRATOR_VOICE = "Charon"`, `NARRATOR_VOICE_PROMPT_PREFIX`, `NarratorError`, PCM-to-MP3 encoding, and `Narrator` (`synthesize_speech` and `synthesize_to_temp_file` context manager).
3. **Verify:** Run `./scripts/test`.

---

## Task Group 3: Gateway Orchestration, Temporary File Cleanup & Stage-Aware Retry (`poller.py` & `main.py`) (TDD)
1. **Red — update `tests/test_polling.py`:**
   - When the final interview answer completes the interview (`turn_result.is_complete=True` with `dossier`):
     - `converter.generate_hybrid_portrait` + `client.send_photo` + `sessions.set_hybrid_portrait` run first.
     - `scripter.generate_script` + `client.send_message` + `sessions.set_script` run second.
     - Immediately after, `narrator` synthesizes the script to a temporary audio file, `client.send_voice` uploads the audio bytes read from that temporary file, the temporary file is removed from disk, and `sessions.complete_documentary(chat_id)` transitions the session to `SessionPhase.COMPLETED`.
   - When `converter` fails or `scripter` fails:
     - `narrator` is NOT called, and the session remains in `GENERATING_MEDIA`.
   - When `converter` and `scripter` succeed (`hybrid_portrait` and `script` stored) but `narrator` raises `NarratorError` or `client.send_voice` raises `TelegramClientError`:
     - Logs `event=narrator_failed stage=gateway chat_id=...` with traceback.
     - Cleans up any temporary audio file.
     - Sends `NARRATOR_ERROR_FALLBACK_MESSAGE` to the user.
     - Keeps the session in `GENERATING_MEDIA` with `hybrid_portrait` and `script` preserved.
     - A subsequent non-command text message in `GENERATING_MEDIA` skips both Converter and Scripter and retries **only** the Narrator TTS + `send_voice` step, transitioning to `COMPLETED` on success.
   - When the session is in `SessionPhase.COMPLETED`:
     - Any non-command text message replies with `INTERVIEW_COMPLETE_NUDGE` without calling Converter, Scripter, or Narrator.
   - `/start` and `/restart` in `GENERATING_MEDIA` or `COMPLETED` reset `portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`, and `script` and return the chat to `AWAITING_PHOTO`.
2. **Green — update `src/telegram_documentaries/poller.py` and `src/telegram_documentaries/main.py`:**
   - Thread `narrator: Narrator` through `process_update`, `poll_once`, `run_polling_loop`, and `main()`.
   - Add `NARRATOR_ERROR_FALLBACK_MESSAGE`, `_deliver_voice_narration(...)`, and `SessionPhase.COMPLETED` routing in `poller.py`.
3. **Verify:** Run `./scripts/test` and `./scripts/hooks`.
