# Phase 3 Implementation Plan: The Interviewer Agent & Stateful Orchestrator

All implementation follows **Red/Green TDD**: write failing tests first, run `./scripts/test` to confirm failure, implement the minimal code to pass, and verify with `./scripts/test` and `./scripts/hooks`. Do not commit during implementation.

---

## Task Group 1: Boundary Schemas (`InterviewTurn`, `BehavioralDossier`, `InterviewerTurnResult`) (TDD)
1. **Red — extend `tests/test_models.py`:**
   - Validate `InterviewTurn(question=..., answer=...)` strips/accepts valid strings and rejects empty or whitespace-only `question` or `answer` with `ValidationError`.
   - Validate `BehavioralDossier(summary=..., suggested_animal=...)` requires non-empty `summary` and `suggested_animal` and carries `Field(description=...)` metadata on both fields.
   - Validate `InterviewerTurnResult(is_complete=..., next_question=..., dossier=...)` parses both in-progress turns (`is_complete=False`, `next_question="...?"`) and completed turns (`is_complete=True`, `dossier=BehavioralDossier(...)`), and rejects non-bool `is_complete`.
2. **Green — extend `src/telegram_documentaries/models.py`:**
   - Define `InterviewTurn`, `BehavioralDossier`, and `InterviewerTurnResult` with strict Pydantic v2 types, `Field(description=...)`, and `@field_validator` checks.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 2: Extended Session State Machine (`SessionStore` & `SessionState`) (TDD)
1. **Red — extend `tests/test_session.py`:**
   - Verify `SessionPhase` includes `AWAITING_PHOTO`, `INTERVIEWING`, and `GENERATING_MEDIA`.
   - Verify default `SessionState` initializes `current_question=None`, `interview_history=[]`, and `dossier=None`.
   - Verify `start_interview(chat_id, portrait, first_question)` sets `portrait`, sets `phase=SessionPhase.INTERVIEWING`, sets `current_question=first_question`, and clears any prior `interview_history` and `dossier`.
   - Verify `record_answer(chat_id, answer, next_question)` appends `InterviewTurn(question=current_question, answer=answer)` to `interview_history`, updates `current_question=next_question`, and keeps `phase=SessionPhase.INTERVIEWING`. Calling `record_answer` when `current_question` is `None` raises `ValueError`.
   - Verify `complete_interview(chat_id, answer, dossier)` appends the final `InterviewTurn`, sets `current_question=None`, stores `dossier`, and transitions `phase` to `SessionPhase.GENERATING_MEDIA`. Calling `complete_interview` when `current_question` is `None` raises `ValueError`.
   - Verify `reset(chat_id)` clears `portrait`, `current_question`, `interview_history`, and `dossier`, returning `phase` to `SessionPhase.AWAITING_PHOTO`.
   - Verify two different `chat_id`s never share `interview_history`, `current_question`, or `dossier`.
2. **Green — update `src/telegram_documentaries/session.py`:**
   - Add `GENERATING_MEDIA` to `SessionPhase` and the new fields/methods to `SessionState` and `SessionStore`, decorated with `@log_stage("session")` on mutating methods.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 3: The Interviewer Agent (`src/telegram_documentaries/interviewer.py`) (TDD)
1. **Red — create `tests/test_interviewer.py`** (mock `google.genai.Client`; no network):
   - `start_interview(portrait_bytes)` calls `client.models.generate_content` with model `gemini-3.1-flash-lite-preview`, passing the portrait image part and structured output config, logs `event=interviewer_question stage=interviewer turn=1`, and returns the opening question string.
   - `advance_interview(history)`:
     - When `len(history) < 5` (e.g. 1..4 answers) and model returns `is_complete=False, next_question="Q2?"`, returns `InterviewerTurnResult(is_complete=False, next_question="Q2?")` and logs `event=interviewer_question stage=interviewer turn=...`.
     - When `len(history) < 5` and model prematurely returns `is_complete=True` with a valid `next_question`, logs a warning (`event=interviewer_early_completion_coerced`) and coerces `is_complete=False`. If `next_question` is empty/whitespace, raises `InterviewerError`.
     - When `5 <= len(history) < 7`, respects either `is_complete=False` (with non-empty `next_question`) or `is_complete=True` (with non-None `dossier`, logging `event=interviewer_dossier stage=interviewer turns=... suggested_animal=...`).
     - When `len(history) >= 7` (e.g. 7th answer) and model returns `is_complete=True` with a valid `dossier`, returns the completed result and logs `event=interviewer_dossier`.
     - When `len(history) >= 7` and model returns `is_complete=False` but includes a valid `dossier`, logs a warning (`event=interviewer_max_turns_coerced`) and coerces `is_complete=True`. If `dossier` is `None`, raises `InterviewerError`.
     - Empty response, safety-blocked response (with `block_reason`/`finish_reason` diagnostics), invalid JSON, missing `next_question` when incomplete, missing `dossier` when complete, or SDK transport errors all raise `InterviewerError` with chained cause.
2. **Green — create `src/telegram_documentaries/interviewer.py`:**
   - Implement `Interviewer`, `InterviewerError`, `MIN_INTERVIEW_QUESTIONS = 5`, `MAX_INTERVIEW_QUESTIONS = 7`, `DEFAULT_INTERVIEWER_MODEL = "gemini-3.1-flash-lite-preview"`, and `format_dossier_message(dossier: BehavioralDossier) -> str`.
   - Decorate `start_interview` and `advance_interview` with `@log_stage("interviewer")`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 4: Gateway Orchestration & Error Recovery (`poller.py` & `main.py`) (TDD)
1. **Red — update `tests/test_polling.py`:**
   - Human photo approved by Bouncer → calls `interviewer.start_interview(portrait)`, stores portrait + Question 1 via `sessions.start_interview`, and sends Question 1 to the chat.
   - If `interviewer.start_interview` raises `InterviewerError` after Bouncer approval → logs exception, resets session to `AWAITING_PHOTO`, and sends `ERROR_FALLBACK_MESSAGE`.
   - Text message during `INTERVIEWING` (in-progress turn) → calls `interviewer.advance_interview` with `history + [InterviewTurn(question=current_question, answer=text)]`, records answer & next question in `SessionStore`, and sends `next_question` to the chat.
   - Text message during `INTERVIEWING` (final turn producing dossier) → calls `sessions.complete_interview`, transitions to `GENERATING_MEDIA`, and sends `format_dossier_message(dossier)` (containing both `suggested_animal` and `summary`) to the chat.
   - Empty/whitespace-only text message during `INTERVIEWING` → re-prompts the user with the current question (or polite nudge) without advancing turn count.
   - If `interviewer.advance_interview` raises `InterviewerError` during `INTERVIEWING` → logs exception, sends `INTERVIEW_ERROR_FALLBACK_MESSAGE`, and preserves existing `interview_history` and `current_question` so the user can retry.
   - Text message during `GENERATING_MEDIA` → sends `INTERVIEW_COMPLETE_NUDGE` without altering state.
   - `/start` and `/restart` in `INTERVIEWING` or `GENERATING_MEDIA` → resets session to `AWAITING_PHOTO` (clearing portrait, history, current_question, dossier) and sends `AWAITING_PHOTO_NUDGE`.
2. **Green — update `src/telegram_documentaries/poller.py` and `src/telegram_documentaries/main.py`:**
   - Thread `interviewer: Interviewer` through `process_update`, `poll_once`, `run_polling_loop`, and `main()`.
3. **Verify:** Run `./scripts/test` and `./scripts/hooks`.
