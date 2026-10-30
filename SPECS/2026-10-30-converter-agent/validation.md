# Phase 4 Validation Checklist: The Converter Agent (Hybrid Animal Portrait)

## Automated Verification
- [x] `./scripts/test` — full `pytest` suite passes with zero failures, skips, or xfails (174 passed).
- [x] `./scripts/hooks` — `ruff check`, `ruff format --check`, `mypy` (strict across `src` and `tests`), `pytest`, and `pre-commit run --all-files` (including `gitleaks`) all pass.
- [x] No `# type: ignore`, `# noqa`, or `Any` escape hatches added without justification.

## Boundary & Contract Verification
- [x] `GeneratedImage`, `SendPhotoPayload`, and `SendPhotoResponse` are strict Pydantic v2 models with field validators rejecting empty image bytes and invalid types.
- [x] `SessionState` tracks `hybrid_portrait: bytes | None` isolated per `chat_id`, cleared on `start_interview` and `reset`.
- [x] `TelegramClient.send_photo` validates inputs via `SendPhotoPayload`, uploads image bytes via multipart `sendPhoto`, and validates the response via `SendPhotoResponse`.
- [x] `Converter` uses `gemini-3.1-flash-image-preview` in a single multimodal call (passing both the user's raw portrait bytes and the `BehavioralDossier` prompt without an intermediate text step), validates output bytes via `GeneratedImage`, and raises `ConverterError` on empty, blocked, or text-only responses.

## Behavioural Verification (Unit & Integration Tests)
- [x] Answering the final interview question transitions the session to `GENERATING_MEDIA`, calls `Converter.generate_hybrid_portrait(portrait, dossier)`, stores `hybrid_portrait`, and sends the hybrid portrait photo to Telegram via `sendPhoto`.
- [x] Converter or `sendPhoto` failure logs `event=converter_failed`, sends `CONVERTER_ERROR_FALLBACK_MESSAGE`, and preserves `portrait` and `dossier` in `GENERATING_MEDIA` so any subsequent text message retries image synthesis.
- [x] Sending text in `GENERATING_MEDIA` after the hybrid portrait has already been delivered replies with `INTERVIEW_COMPLETE_NUDGE`.
- [x] `/start` and `/restart` wipe `portrait`, `current_question`, `interview_history`, `dossier`, and `hybrid_portrait` from any phase.

## Live Verification (User on Phone)
- [ ] Upload a human portrait and complete the 5–7 question interview in Telegram → confirm the bot automatically transitions from the final question to returning an animal hybrid portrait image in the chat.

## Documentation Synchronisation
- [x] `SPECS/ROADMAP.md` Phase 4 marked **Completed** with delivery summary.
- [x] `SPECS/TECH.md` and `README.md` updated to document `Converter`, `TelegramClient.send_photo`, `GeneratedImage`, and the Phase 4 multimodal image synthesis flow.
