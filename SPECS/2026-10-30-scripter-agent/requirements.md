# Phase 5 Requirements: The Scripter Agent (Documentary Narration Text)

## Context & Goal
Phase 4 implemented **The Converter** (`gemini-3.1-flash-image-preview`), which synthesizes a human-animal hybrid portrait upon completing the 5–7 question behavioral interview and delivers it to the user's Telegram chat via `sendPhoto`.

Phase 5 implements **The Scripter** (`gemini-3.1-flash-lite-preview`), a specialized text-generation agent that consumes the `BehavioralDossier` (`summary` + `suggested_animal`) produced by the Interviewer and transforms those traits into a dramatic, comedic, one-paragraph (~60–90 words) British wildlife documentary voiceover script. Immediately after the Converter delivers the hybrid portrait image, the Scripter generates the narration script, stores the raw script string in `SessionState.script` (ready for Phase 6 TTS synthesis), logs the script in console logs (`event=scripter_narration_generated`), and sends the narrative paragraph directly to the user's Telegram chat.

Roadmap reference: Phase 5 in `SPECS/ROADMAP.md`.

## User-Visible Behaviour
| Situation | Bot response | Session effect |
|---|---|---|
| Photo containing a human (`AWAITING_PHOTO` or re-upload) | Bouncer approves; Interviewer generates and sends **Question 1** | Portrait stored; `interview_history=[]`; `dossier=None`; `hybrid_portrait=None`; `script=None`; `current_question` set to Question 1; phase → `INTERVIEWING` |
| Text sent while `INTERVIEWING` (final answer, between turn 5 and turn 7 inclusive) | Records final `(current_question, user_answer)` and `BehavioralDossier`; runs `Converter.generate_hybrid_portrait` + `send_photo`; immediately runs `Scripter.generate_script(dossier)` and sends the narrative script via `send_message` | `dossier` stored; `current_question = None`; `hybrid_portrait` stored; `script` stored; phase → `GENERATING_MEDIA` |
| Gemini / Telegram error during Converter image synthesis or `sendPhoto` | Logs full traceback (`event=converter_failed stage=gateway`); sends `CONVERTER_ERROR_FALLBACK_MESSAGE` | Preserves `portrait` and `dossier` in `GENERATING_MEDIA` with `hybrid_portrait=None` and `script=None` so a follow-up message retries from the Converter stage |
| Gemini / Telegram error during Scripter generation or `sendMessage` (after `hybrid_portrait` succeeded) | Logs full traceback (`event=scripter_failed stage=gateway`); sends `SCRIPTER_ERROR_FALLBACK_MESSAGE` | Keeps `hybrid_portrait` stored (`hybrid_portrait is not None`, `script=None`) so a follow-up message skips the Converter and retries **only** the Scripter stage |
| Text sent while `GENERATING_MEDIA` when `hybrid_portrait is None` | Retries Converter synthesis + `send_photo`, then immediately runs Scripter + `send_message` | Stores `hybrid_portrait` and `script` as each step succeeds |
| Text sent while `GENERATING_MEDIA` when `hybrid_portrait is not None` and `script is None` | Skips Converter and retries **only** `Scripter.generate_script(dossier)` + `send_message` | On success, stores `script` and sends the narration text; on failure, logs `event=scripter_failed` and sends `SCRIPTER_ERROR_FALLBACK_MESSAGE` |
| Text sent while `GENERATING_MEDIA` when both `hybrid_portrait is not None` and `script is not None` | Polite in-character nudge (`INTERVIEW_COMPLETE_NUDGE`) noting field research is complete and offering `/restart` | Unchanged (`GENERATING_MEDIA`) |
| `/start` or `/restart` in any phase | `AWAITING_PHOTO_NUDGE` | Full reset (`portrait=None`, `interview_history=[]`, `current_question=None`, `dossier=None`, `hybrid_portrait=None`, `script=None`, phase → `AWAITING_PHOTO`) |

## Decisions (explicit, no legacy)
1. **Model Selection:** Route Scripter requests to `gemini-3.1-flash-lite-preview` (`DEFAULT_SCRIPTER_MODEL = "gemini-3.1-flash-lite-preview"`), matching `SPECS/TECH.md` and `SPECS/MISSION.md`.
2. **Persona & Prompt Directive:** Enforce the persona of a revered, highly dramatic British nature documentary narrator observing a wild beast (`"Write like a highly dramatic, British nature documentary narrator observing a wild beast"`). Frame the subject's quirks, bedtime habits, and snacking patterns from `BehavioralDossier` as funny animal behaviours in a single paragraph of roughly 60 to 90 words with no markdown formatting, ready for voice synthesis.
3. **Structured Output Schema (`DocumentaryScript`):** Use `google-genai` structured output (`response_mime_type="application/json"`, `response_schema=DocumentaryScript`). Validate in `DocumentaryScript` that `narration` is non-empty, strips leading/trailing whitespace, collapses any internal line breaks into a single cohesive paragraph, and contains no markdown formatting markers (or strips/rejects empty text), without hard-failing if the model produces slightly fewer than 60 or slightly more than 90 words.
4. **Skip Converter on Scripter Retry:** Store `hybrid_portrait` as soon as `send_photo` succeeds. If `Scripter.generate_script` or `client.send_message` fails afterward, `hybrid_portrait` remains stored while `script` is `None`. When the user sends a follow-up message in `GENERATING_MEDIA`, the gateway checks `if state.hybrid_portrait is None` first, and if `state.hybrid_portrait is not None and state.script is None`, retries only the Scripter step (`SCRIPTER_ERROR_FALLBACK_MESSAGE = "My typewriter ribbon snapped mid-narration — send any message to retry your documentary script, or /restart to begin anew!"`).
5. **Observability:** Decorate `Scripter.generate_script` with `@log_stage("scripter")` and `SessionStore.set_script` with `@log_stage("session")`. Emit `event=scripter_narration_generated stage=scripter suggested_animal=%r words=%d script=%r` at `INFO` level so console logs clearly show the captured raw script string ready for audio synthesis.

## Boundary Contracts (Pydantic v2)
- `DocumentaryScript`:
  - `narration: str = Field(description=...)` (validator ensuring non-empty single-paragraph text with internal newlines collapsed to spaces)
- `SessionState`:
  - `chat_id: StrictInt`
  - `phase: SessionPhase = SessionPhase.AWAITING_PHOTO`
  - `portrait: bytes | None = None`
  - `current_question: str | None = None`
  - `interview_history: list[InterviewTurn] = Field(default_factory=list)`
  - `dossier: BehavioralDossier | None = None`
  - `hybrid_portrait: bytes | None = None`
  - `script: str | None = None`

## Out of Scope (YAGNI)
- TTS voice note synthesis (`Narrator`, Phase 6).
- Persistent database storage across bot restarts.

## Acceptance Criteria
- Answering the final interview question (turn 5–7) runs the Converter (`sendPhoto`) and immediately runs `Scripter.generate_script(dossier)` (`gemini-3.1-flash-lite-preview`), stores the raw script string in `SessionState.script`, logs `event=scripter_narration_generated` with the captured script, and sends the script paragraph to Telegram via `sendMessage`.
- `DocumentaryScript` enforces a non-empty single-paragraph narration via Pydantic v2 validation.
- Empty, safety-blocked, or malformed Gemini responses in `Scripter` raise `ScripterError` with diagnostic context.
- If the Converter succeeds but the Scripter or its `sendMessage` fails, `hybrid_portrait` remains stored while `script` is `None`, `SCRIPTER_ERROR_FALLBACK_MESSAGE` is sent, and a subsequent text message retries **only** the Scripter without re-running the Converter.
- `/start` and `/restart` clear `portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`, and `script` and return the chat to `AWAITING_PHOTO`.
- Separate `chat_id` sessions remain 100% isolated.
- `./scripts/test` and `./scripts/hooks` pass completely.
