# Phase 3 Validation Checklist: The Interviewer Agent & Stateful Orchestrator

## Automated Verification
- [x] `./scripts/test` — full `pytest` suite passes with zero failures, skips, or xfails (`144 passed`).
- [x] `./scripts/hooks` — `ruff check`, `ruff format --check`, `mypy` (strict across `src` and `tests`), `pytest`, and `pre-commit run --all-files` (including `gitleaks`) all pass.
- [x] No `# type: ignore`, `# noqa`, or `Any` escape hatches added without justification.

## Boundary & Contract Verification
- [x] `InterviewTurn`, `BehavioralDossier`, and `InterviewerTurnResult` are strict Pydantic v2 models with field descriptions and non-empty string validators.
- [x] `SessionPhase` includes `AWAITING_PHOTO`, `INTERVIEWING`, and `GENERATING_MEDIA`.
- [x] `SessionState` tracks `portrait`, `current_question`, `interview_history: list[InterviewTurn]`, and `dossier: BehavioralDossier | None` isolated per `chat_id`.
- [x] `Interviewer` uses `gemini-3.1-flash-lite-preview` with structured output (`response_schema`) and raises `InterviewerError` on empty, blocked, or invalid responses.

## Behavioural Verification (Unit & Integration Tests)
- [x] Human photo approval immediately starts the interview and sends Question 1.
- [x] Sequential text replies during `INTERVIEWING` ask one question at a time and accumulate `interview_history`.
- [x] Bounds enforced: cannot complete with `< 5` answers; must complete at `7` answers.
- [x] Final interview answer stores `BehavioralDossier`, transitions session to `GENERATING_MEDIA`, and sends the formatted dossier + suggested animal to Telegram.
- [x] Error during Question 1 resets session to `AWAITING_PHOTO` and sends `ERROR_FALLBACK_MESSAGE`; error during an interview turn sends `INTERVIEW_ERROR_FALLBACK_MESSAGE` and preserves existing `interview_history` and `current_question`.
- [x] `/start` and `/restart` wipe `portrait`, `current_question`, `interview_history`, and `dossier` from any phase.

## Live Verification (User on Phone)
- [ ] Upload a human portrait → bot replies with Question 1 from the Interviewer *(pending live user phone test)*.
- [ ] Reply turn-by-turn to 5–7 questions → bot asks one question at a time and then outputs the behavioral dossier summary + suggested spirit animal *(pending live user phone test)*.

## Documentation Synchronisation
- [x] `SPECS/ROADMAP.md` Phase 3 marked **Completed** with delivery summary.
- [x] `SPECS/TECH.md` and `README.md` updated to document `Interviewer`, `BehavioralDossier`, and the 5–7 turn state machine flow.
