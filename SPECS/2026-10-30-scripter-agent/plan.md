# Phase 5 Implementation Plan: The Scripter Agent (Documentary Narration Text)

All implementation follows **Red/Green TDD**: write failing tests first, run `./scripts/test` to confirm failure, implement the minimal code to pass, and verify with `./scripts/test` and `./scripts/hooks`. Do not commit during implementation.

---

## Task Group 1: Boundary Schema (`DocumentaryScript`) & Session State (`script`) (TDD)
1. **Red — extend `tests/test_models.py` and `tests/test_session.py`:**
   - In `tests/test_models.py`:
     - Validate `DocumentaryScript(narration="Here in the fluorescent-lit kitchen...")` accepts valid prose, carries `Field(description=...)` metadata on `narration`, collapses internal newlines into a single paragraph, and rejects empty or whitespace-only `narration` with `ValidationError`.
   - In `tests/test_session.py`:
     - Verify default `SessionState` initializes `script=None`.
     - Verify `set_script(chat_id, script)` stores a stripped non-empty `script` string and raises `ValueError` on empty or whitespace-only strings.
     - Verify `start_interview` and `reset` both clear `script=None`.
     - Verify two different `chat_id`s never share `script`.
2. **Green — update `src/telegram_documentaries/models.py` and `src/telegram_documentaries/session.py`:**
   - Add `DocumentaryScript` to `models.py`.
   - Add `script: str | None = None` to `SessionState` and `@log_stage("session") def set_script(self, chat_id: int, script: str) -> SessionState` to `SessionStore`, clearing `script` in `start_interview` and `reset`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 2: The Scripter Agent (`src/telegram_documentaries/scripter.py`) (TDD)
1. **Red — create `tests/test_scripter.py`** (mock `google.genai.Client`; no network):
   - Verify `Scripter` defaults to `DEFAULT_SCRIPTER_MODEL = "gemini-3.1-flash-lite-preview"`.
   - Verify `generate_script(dossier)` calls `client.models.generate_content` with:
     - `model="gemini-3.1-flash-lite-preview"`
     - `contents` containing a prompt with `dossier.suggested_animal` and `dossier.summary`
     - `config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=DocumentaryScript, temperature=0.7, system_instruction=SCRIPTER_INSTRUCTION)` where `SCRIPTER_INSTRUCTION` instructs the model to write like a highly dramatic British nature documentary narrator observing a wild beast, in a single paragraph (~60–90 words) with no markdown formatting.
   - Verify `generate_script(dossier)` parses the JSON response via `DocumentaryScript.model_validate_json`, logs `event=scripter_narration_generated stage=scripter suggested_animal=%r words=%d script=%r`, and returns the validated single-paragraph `narration` string.
   - Verify `generate_script(dossier)` raises `ScripterError` when:
     - `response.text` is `None` or whitespace-only (including safety-block `block_reason` / `finish_reason` diagnostics when present).
     - `response.text` is invalid JSON or fails `DocumentaryScript` validation.
     - The `google-genai` SDK raises an exception (chained via `raise ScripterError(...) from exc`).
2. **Green — create `src/telegram_documentaries/scripter.py`:**
   - Implement `DEFAULT_SCRIPTER_MODEL = "gemini-3.1-flash-lite-preview"`, `SCRIPTER_INSTRUCTION`, `ScripterError`, and `Scripter` with `@log_stage("scripter") def generate_script(self, dossier: BehavioralDossier) -> str`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 3: Gateway Orchestration & Stage-Aware Retry (`poller.py` & `main.py`) (TDD)
1. **Red — update `tests/test_polling.py`:**
   - When the final interview answer completes the interview (`turn_result.is_complete=True` with `dossier`):
     - `sessions.complete_interview` transitions the chat to `GENERATING_MEDIA` and stores `dossier`.
     - `converter.generate_hybrid_portrait(portrait, dossier)` + `client.send_photo` + `sessions.set_hybrid_portrait` run first.
     - Immediately after, `scripter.generate_script(dossier)` runs, `client.send_message(chat_id=chat_id, text=script)` sends the narration paragraph to Telegram, and `sessions.set_script(chat_id=chat_id, script=script)` stores the script in `SessionState.script`.
   - When `converter.generate_hybrid_portrait` or `client.send_photo` fails:
     - Logs `event=converter_failed`, sends `CONVERTER_ERROR_FALLBACK_MESSAGE`, does NOT call `scripter.generate_script`, and leaves `hybrid_portrait=None` and `script=None`.
     - A subsequent non-command text message in `GENERATING_MEDIA` retries the Converter first and then immediately runs the Scripter upon Converter success.
   - When the Converter succeeds (`hybrid_portrait` stored and sent) but `scripter.generate_script` raises `ScripterError` (or `client.send_message` raises `TelegramClientError` while sending the script):
     - Logs `event=scripter_failed stage=gateway chat_id=...` with traceback.
     - Sends `SCRIPTER_ERROR_FALLBACK_MESSAGE` to the user.
     - Preserves `hybrid_portrait` (`state.hybrid_portrait is not None`) while `state.script is None`.
     - A subsequent non-command text message in `GENERATING_MEDIA` skips the Converter (does not call `converter.generate_hybrid_portrait` or `client.send_photo` again) and retries **only** `scripter.generate_script` + `client.send_message` + `sessions.set_script`.
   - When both `state.hybrid_portrait is not None` and `state.script is not None` in `GENERATING_MEDIA`:
     - Any non-command text message replies with `INTERVIEW_COMPLETE_NUDGE`.
   - `/start` and `/restart` in `GENERATING_MEDIA` reset `portrait`, `current_question`, `interview_history`, `dossier`, `hybrid_portrait`, and `script` and return the chat to `AWAITING_PHOTO`.
2. **Green — update `src/telegram_documentaries/poller.py` and `src/telegram_documentaries/main.py`:**
   - Thread `scripter: Scripter` through `process_update`, `poll_once`, `run_polling_loop`, and `main()`.
   - Add `SCRIPTER_ERROR_FALLBACK_MESSAGE`, `@log_stage("scripter") def _generate_script(...)`, and `_deliver_script(...)` in `poller.py`.
3. **Verify:** Run `./scripts/test` and `./scripts/hooks`.
