# Phase 6 Validation Checklist: The Narrator (Gemini TTS Audio Synthesis)

## Automated Verification
- [x] `./scripts/test` — full `pytest` suite passes with zero failures, skips, or xfails (246 passed).
- [x] `./scripts/hooks` — `ruff check`, `ruff format --check`, `mypy` (strict across `src` and `tests`), `pytest`, and `pre-commit run --all-files` (including `gitleaks`) all pass.
- [x] No `# type: ignore`, `# noqa`, or `Any` escape hatches added without justification.

## Boundary & Contract Verification
- [x] `GeneratedAudio`, `SendVoicePayload`, and `SendVoiceResponse` are strict Pydantic v2 models enforcing non-empty audio bytes, valid `audio/*` MIME types, and `StrictInt` `chat_id`.
- [x] `SessionPhase.COMPLETED` and `@log_stage("session") SessionStore.complete_documentary` transition a completed chat session to `COMPLETED` while keeping sessions isolated per `chat_id`.
- [x] `Narrator` targets `gemini-3.1-flash-tts-preview` with `response_modalities=["AUDIO"]`, `SpeechConfig`/`VoiceConfig`, and a Nikolai Drozdov (*"In the World of Animals"*) warm Russian-accented naturalist voice prompt, converts raw PCM (`audio/L16`) to Telegram-compatible MP3/OGG, logs `event=narrator_audio_generated stage=narrator`, and cleans up temporary audio files in a `finally` block.

## Behavioural Verification (Unit & Integration Tests)
- [x] Answering the final interview question runs the Converter (`sendPhoto`), then the Scripter (`sendMessage`), and immediately runs the Narrator (`sendVoice`), removes the temporary audio file, and transitions the session to `SessionPhase.COMPLETED`.
- [x] If the Converter and Scripter succeed and the Narrator or `sendVoice` fails, `hybrid_portrait` and `script` stay stored in `GENERATING_MEDIA`, any temporary audio file is cleaned up, `NARRATOR_ERROR_FALLBACK_MESSAGE` is sent, and a follow-up text message retries **only** the Narrator step without re-running the Converter or Scripter.
- [x] Sending text in `SessionPhase.COMPLETED` replies with `INTERVIEW_COMPLETE_NUDGE`.
- [x] `/start` and `/restart` wipe `portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`, and `script` from any phase (including `COMPLETED`) and return the chat to `AWAITING_PHOTO`.

## Live Verification (User on Phone)
- [x] Send a human portrait, complete the 5–7 question interview, receive the hybrid animal portrait and documentary script, and confirm the bot immediately delivers the playable voiced documentary narration in Nikolai Drozdov's warm naturalist style in Telegram.

## Documentation Synchronisation
- [x] `SPECS/ROADMAP.md` Phase 6 marked **Completed** with delivery summary.
- [x] `SPECS/TECH.md`, `SPECS/MISSION.md`, and `README.md` updated to document `Narrator`, `GeneratedAudio`, `SendVoicePayload`, `SendVoiceResponse`, `SessionPhase.COMPLETED`, and the Phase 6 voice synthesis flow.
