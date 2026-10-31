# Phase 5 Validation Checklist: The Scripter Agent (Documentary Narration Text)

## Automated Verification
- [x] `./scripts/test` — full `pytest` suite passes with zero failures, skips, or xfails.
- [x] `./scripts/hooks` — `ruff check`, `ruff format --check`, `mypy` (strict across `src` and `tests`), `pytest`, and `pre-commit run --all-files` (including `gitleaks`) all pass.
- [x] No `# type: ignore`, `# noqa`, or `Any` escape hatches added without justification.

## Boundary & Contract Verification
- [x] `DocumentaryScript` is a strict Pydantic v2 model with `Field(description=...)` metadata and a validator ensuring a non-empty single-paragraph string.
- [x] `SessionState` tracks `script: str | None` isolated per `chat_id`, set via `@log_stage("session") set_script` and cleared on `start_interview` and `reset`.
- [x] `Scripter` uses `gemini-3.1-flash-lite-preview` with structured output (`response_schema=DocumentaryScript`), logs `event=scripter_narration_generated stage=scripter` with the raw script string, and raises `ScripterError` on empty, blocked, or invalid responses.

## Behavioural Verification (Unit & Integration Tests)
- [x] Answering the final interview question runs the Converter (`sendPhoto`) and immediately runs the Scripter (`Scripter.generate_script`), stores `SessionState.script`, and sends the single-paragraph narration to Telegram via `sendMessage`.
- [x] If the Converter succeeds and the Scripter or its `sendMessage` fails, `hybrid_portrait` stays stored while `script` is `None`, `SCRIPTER_ERROR_FALLBACK_MESSAGE` is sent, and a follow-up text message retries **only** the Scripter without re-running the Converter.
- [x] Sending text in `GENERATING_MEDIA` after both `hybrid_portrait` and `script` have been delivered replies with `INTERVIEW_COMPLETE_NUDGE`.
- [x] `/start` and `/restart` wipe `portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`, and `script` from any phase.

## Live Verification (User on Phone)
- [x] Send a human portrait, complete the 5–7 question interview, receive the hybrid animal portrait, and confirm the bot immediately delivers the single-paragraph British wildlife documentary script in the chat and logs the captured script in the console.

## Documentation Synchronisation
- [x] `SPECS/ROADMAP.md` Phase 5 marked **Completed** with delivery summary.
- [x] `SPECS/TECH.md` and `README.md` updated to document `Scripter`, `DocumentaryScript`, `SessionState.script`, and the Phase 5 narration flow.
