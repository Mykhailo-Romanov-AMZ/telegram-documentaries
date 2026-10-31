# Phase 3 Requirements: The Interviewer Agent & Stateful Orchestrator

## Context & Goal
Phase 2 gave the bot a vision gate (**The Bouncer**) that verifies a human portrait and transitions the user's session to `INTERVIEWING`. Phase 3 implements **The Interviewer** (`gemini-3.1-flash-lite-preview`), which doubles as the stateful conversation orchestrator. Once a human portrait is approved, the Interviewer conducts a sequential 5–7 question behavioral interview (one question at a time), records the user's answers per `chat_id`, compiles a structured `BehavioralDossier` (behavioral summary + suggested spirit animal), transitions the session to `GENERATING_MEDIA`, and delivers the dossier summary to the chat.

Roadmap reference: Phase 3 in `SPECS/ROADMAP.md`.

## User-Visible Behaviour
| Situation | Bot response | Session effect |
|---|---|---|
| Photo containing a human (`AWAITING_PHOTO` or re-upload) | Bouncer approves; Interviewer generates and sends **Question 1** (replaces Phase 1/2 `"hey mate!"` placeholder) | Portrait stored; history cleared; `current_question` set to Question 1; phase → `INTERVIEWING` |
| Photo without a human | Cheeky Bouncer rejection naming the subject | Session reset; phase → `AWAITING_PHOTO` |
| Text sent while `AWAITING_PHOTO` | `AWAITING_PHOTO_NUDGE` asking for a portrait photo | Unchanged (`AWAITING_PHOTO`) |
| Text sent while `INTERVIEWING` (answers 1..4, or 5..6 when continuing) | Records `(current_question, user_answer)` in `interview_history`; sends the single next question | `interview_history` grows by 1; `current_question` updated; phase stays `INTERVIEWING` |
| Text sent while `INTERVIEWING` (final answer, between turn 5 and turn 7 inclusive) | Records final `(current_question, user_answer)`; synthesizes `BehavioralDossier`; sends formatted dossier + suggested animal to chat | `dossier` stored; `current_question = None`; phase → `GENERATING_MEDIA` |
| Text sent while `GENERATING_MEDIA` | Polite in-character nudge (`INTERVIEW_COMPLETE_NUDGE`) noting field research is complete and offering `/restart` | Unchanged (`GENERATING_MEDIA`) |
| `/start` or `/restart` in any phase | `AWAITING_PHOTO_NUDGE` | Full reset (`portrait=None`, `interview_history=[]`, `current_question=None`, `dossier=None`, phase → `AWAITING_PHOTO`) |
| Gemini / Telegram error while generating Question 1 | `ERROR_FALLBACK_MESSAGE`, loud traceback log | Session reset to `AWAITING_PHOTO` so chat is never stuck in `INTERVIEWING` without a question |
| Gemini / Telegram error during an interview turn | `INTERVIEW_ERROR_FALLBACK_MESSAGE`, loud traceback log | Existing `interview_history` and `current_question` preserved so the user can retry their answer |

## Decisions (explicit, no legacy)
1. **Question 1 replaces `"hey mate!"` on photo approval.** Phase 1/2 tests that asserted `"hey mate!"` after a human photo are updated to assert Question 1 from the Interviewer.
2. **Bounded 5–7 sequential questions (`MIN_INTERVIEW_QUESTIONS = 5`, `MAX_INTERVIEW_QUESTIONS = 7`).**
   - Every turn asks **one** question at a time and yields until the user replies.
   - Code enforces the bounds on `len(interview_history)` (count of answered Q&A pairs including the current reply):
     - If `len(history) < 5`: the interview **must** continue (`is_complete=False`, non-empty `next_question`). If the model prematurely sets `is_complete=True` while also providing `next_question`, log a structured warning and coerce `is_complete=False`; if `next_question` is missing, raise `InterviewerError`.
     - If `5 <= len(history) < 7`: the model may either ask one more follow-up question (`is_complete=False` with `next_question`) or finish (`is_complete=True` with `dossier`).
     - If `len(history) >= 7`: the interview **must** complete (`is_complete=True`, non-None `dossier`). If the model sets `is_complete=False` at turn 7 while providing a `dossier`, log a structured warning and coerce `is_complete=True`; if `dossier` is `None`, raise `InterviewerError`.
3. **Agent boundary uses `google-genai` structured output (`response_schema`).** Consistent with Phase 2 and `SPECS/TECH.md`, `Interviewer` wraps `google.genai.Client.models.generate_content` with `gemini-3.1-flash-lite-preview` and Pydantic v2 schemas.
4. **Persona & tone:** Investigative, playful, slightly eccentric wildlife documentary researcher probing daily habits, sleep cycles, territory marking, social rituals, and favorite forage.
5. **Observability:** Log `event=interviewer_question` (with `turn` number) and `event=interviewer_dossier` (with `turns` count and `suggested_animal`) at `INFO` level so terminal logs clearly show interview progress.

## Boundary Contracts (Pydantic v2)
- `InterviewTurn`:
  - `question: str` (non-empty validator)
  - `answer: str` (non-empty validator)
- `BehavioralDossier`:
  - `summary: str` (`Field(description=...)`, non-empty validator — cohesive descriptive paragraph summarizing behavioral quirks, habitats, and daily rituals)
  - `suggested_animal: str` (`Field(description=...)`, non-empty validator — specific wildlife species the subject should be transformed into)
- `InterviewerTurnResult`:
  - `is_complete: StrictBool` (`Field(description=...)`)
  - `next_question: str = ""` (`Field(description=...)`)
  - `dossier: BehavioralDossier | None = None` (`Field(description=...)`)
- `SessionPhase` enum: `AWAITING_PHOTO`, `INTERVIEWING`, `GENERATING_MEDIA`.
- `SessionState`:
  - `chat_id: StrictInt`
  - `phase: SessionPhase = SessionPhase.AWAITING_PHOTO`
  - `portrait: bytes | None = None`
  - `current_question: str | None = None`
  - `interview_history: list[InterviewTurn] = Field(default_factory=list)`
  - `dossier: BehavioralDossier | None = None`

## Out of Scope (YAGNI)
- Hybrid image synthesis (`Converter`, Phase 4), documentary script (`Scripter`, Phase 5), and TTS voice synthesis (`Narrator`, Phase 6).
- Persistent database storage across bot restarts.

## Acceptance Criteria
- Uploading a human portrait transitions the chat to `INTERVIEWING`, stores the portrait and `current_question`, and sends Question 1 to the user.
- Replying with text during `INTERVIEWING` appends `InterviewTurn(question, answer)` to `interview_history` and asks the next single question one turn at a time.
- The interview never completes in fewer than 5 answers and always completes by the 7th answer at the latest.
- Upon completion, a validated `BehavioralDossier` (`summary` + `suggested_animal`) is stored in `SessionState`, the phase transitions to `GENERATING_MEDIA`, and the formatted dossier + suggested animal is sent to Telegram.
- `/start` and `/restart` wipe `portrait`, `current_question`, `interview_history`, and `dossier` and return the chat to `AWAITING_PHOTO`.
- Separate `chat_id` sessions remain 100% isolated.
- `./scripts/test` and `./scripts/hooks` pass completely.
