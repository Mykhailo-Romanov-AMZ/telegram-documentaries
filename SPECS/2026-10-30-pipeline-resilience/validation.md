# Phase 7 Validation Checklist: Pipeline Resilience, Session Resets & Edge Guards

## Automated Verification
- [x] `./scripts/test` — full `pytest` suite passes with zero failures, skips, or xfails (256 passed).
- [x] `./scripts/hooks` — `ruff check`, `ruff format --check`, `mypy` (strict across `src` and `tests`), `pytest`, and `pre-commit run --all-files` (including `gitleaks`) all pass.
- [x] No `# type: ignore`, `# noqa`, or `Any` escape hatches added without justification.

## Boundary & Contract Verification
- [x] `TelegramMessage` includes `caption: str | None = None`.
- [x] `SessionState` tracks `ephemeral_files: list[Path]` and `SessionStore.reset(chat_id)` performs all three required reset actions:
  1. Cancels active session (`phase = SessionPhase.AWAITING_PHOTO`, `current_question = None`).
  2. Purges ephemeral assets on disk (`unlink(missing_ok=True)`) and in memory (`portrait = None`, `hybrid_portrait = None`).
  3. Re-initializes agent memory (`interview_history = []`, `dossier = None`, `script = None`) and logs `event=session_reset stage=session`.

## Behavioural Verification (Unit & Integration Tests)
- [x] Sending `/start` or `/restart` (as text or photo caption) in any phase purges ephemeral disk files and in-memory state and replies with `AWAITING_PHOTO_NUDGE`.
- [x] Sending a photo during `INTERVIEWING` replies with `INTERVIEW_PHOTO_GUARD_MESSAGE` prompting the user to finish answering the current question with text or type `/restart`, preserving the active interview state without calling `Bouncer`.
- [x] Sending a photo during `COMPLETED` replies with `INTERVIEW_COMPLETE_NUDGE`.
- [x] Sending text during `AWAITING_PHOTO` replies with `AWAITING_PHOTO_NUDGE`.
- [x] Sending an unsupported media message (sticker/voice/document) returns a phase-appropriate guidance message instead of silently dropping the update.
- [x] API timeouts and errors across Gemini and Telegram boundaries (including fallback message sends) are caught, logged with `chat_id` and traceback, and never crash the polling loop.
- [x] Two consecutive end-to-end documentary runs for the same `chat_id` complete cleanly in a single process.

## Live Verification (User on Phone)
- [x] Run through the entire pipeline on a phone from portrait upload to received voice documentary, then initiate a second run (`/restart`) without restarting the Python process and confirm both runs succeed cleanly.

## Documentation Synchronisation
- [x] `SPECS/ROADMAP.md` Phase 7 marked **Completed** with delivery summary and explicit 3-step `/restart` requirements.
- [x] `SPECS/TECH.md`, `SPECS/MISSION.md`, and `README.md` updated to reflect Phase 7 reset hygiene and stage guards.
