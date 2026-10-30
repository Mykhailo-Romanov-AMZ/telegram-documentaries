# Phase 2 Implementation Plan: The Bouncer (Vision Gate) & Session State

All work is Red/Green TDD: write the failing test, run `./scripts/test` to see it fail, implement the minimum, then re-run `./scripts/test` and `./scripts/hooks`. No commits during implementation.

---

## Task Group 1: Dependencies, Hygiene & Model Schemas
1. Add `google-genai>=2.0` to `pyproject.toml` dependencies and install it (`pip install -e '.[dev]'` or `pip install google-genai`).
2. Add `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/` to `.gitignore`.
3. **Red — `tests/test_models.py`:**
   - Parse a text-only `TelegramUpdate` unchanged (regression guard).
   - Parse an update whose message carries a `photo` list of three sizes; assert typed `TelegramPhotoSize` values and that `photo` defaults to `None` when absent.
   - Reject malformed photo entries (e.g. `width` as a string) with `ValidationError`.
   - Parse a `getFile` envelope into `GetFileResponse`/`TelegramFile`, including `file_path=None`.
   - Parse `BouncerVerdict` (human/non-human) and reject a verdict missing `is_human`.
4. **Green — extend `src/telegram_documentaries/models.py`** with `TelegramPhotoSize`, `TelegramFile`, `GetFileResponse`, `BouncerVerdict`, and the `photo` field on `TelegramMessage`.
5. Verify: `./scripts/test`.

---

## Task Group 2: Telegram File Download (TDD)
1. **Red — extend `tests/test_telegram_client.py`:**
   - `get_file(file_id)` calls `getFile` with the right path/params and returns a validated `TelegramFile`.
   - `get_file` propagates a non-200 response as an exception (no silent `None`).
   - `download_file(file_path)` GETs `/file/bot<token>/<file_path>` and returns raw bytes unchanged.
   - `download_file` raises when the response body is empty and when Telegram returns non-200.
2. **Green — extend `src/telegram_documentaries/telegram_client.py`** with `get_file` and `download_file`, both decorated with `@log_stage("telegram_api")`, with explicit timeouts.
3. Verify: `./scripts/test`.

---

## Task Group 3: Session Store (TDD)
1. **Red — `tests/test_session.py`:**
   - `get(chat_id)` creates a default `SessionState` in `AWAITING_PHOTO` with no portrait.
   - `get` is stable: repeated calls return the same object/state.
   - `set_portrait(chat_id, bytes)` stores bytes and moves phase to `INTERVIEWING`.
   - `reset(chat_id)` drops phase back to `AWAITING_PHOTO` and clears the portrait.
   - Two `chat_id`s are fully isolated (state set on one is invisible to the other).
2. **Green — create `src/telegram_documentaries/session.py`** with `SessionPhase`, `SessionState`, and `SessionStore` (`get`, `reset`, `set_portrait`), decorated with `@log_stage("session")` where state actually mutates.
3. Verify: `./scripts/test`.

---

## Task Group 4: The Bouncer Agent (TDD)
1. **Red — `tests/test_bouncer.py`** (mock `google.genai.Client`; no network):
   - `evaluate(image_bytes)` sends the image bytes and returns a parsed `BouncerVerdict` when the model returns valid schema JSON.
   - Non-human verdict is returned unchanged (cheeky copy preserved verbatim).
   - Model output that is not valid JSON / not schema-shaped raises `BouncerError` (no silent default).
   - Empty `rejection_message` for a non-human verdict is replaced by the deterministic local fallback copy (constant), so downstream code always has text.
   - The configured model id defaults to `gemini-3.1-flash-lite-preview` and is passed through to the SDK.
   - Transport/SDK exceptions are wrapped in `BouncerError` with the original exception chained.
2. **Green — create `src/telegram_documentaries/bouncer.py`:**
   - `BouncerVerdict` imported from `models.py`; `BouncerError(Exception)`.
   - `Bouncer` class: constructor takes `api_key`, optional `model_name`, optional injected `genai.Client`; `evaluate` decorated with `@log_stage("bouncer")`.
   - Uses `types.GenerateContentConfig(response_mime_type="application/json", response_schema=BouncerVerdict, temperature=...)` plus an instruction describing the deadpan-security-guard persona and the "name what you actually see" rejection rule.
3. Verify: `./scripts/test`.

---

## Task Group 5: Gateway Routing (TDD)
1. **Red — rewrite `tests/test_polling.py`** for the new signature `process_update(update, client, sessions, bouncer)`:
   - Human photo: picks the largest size, calls `get_file` then `download_file`, calls `bouncer.evaluate`, sends `HARDCODED_REPLY`, and leaves the session in `INTERVIEWING` with the portrait stored.
   - Non-human photo: sends the verdict's `rejection_message` and resets the session to `AWAITING_PHOTO` with no portrait.
   - Text in `AWAITING_PHOTO`: sends `AWAITING_PHOTO_NUDGE`; phase and portrait unchanged.
   - `message is None`: no sends, no state change.
   - Download error: sends `ERROR_FALLBACK_MESSAGE`, logs, leaves state unchanged.
   - Bouncer error: sends `ERROR_FALLBACK_MESSAGE`, logs, leaves state unchanged.
   - Photo in `INTERVIEWING` that is human: replaces portrait and re-sends `hey mate!` (no crash).
   - Existing `poll_once` offset/error-isolation tests keep passing with a stubbed `process_update` signature.
2. **Green — update `src/telegram_documentaries/poller.py`:**
   - Constants: `HARDCODED_REPLY = "hey mate!"` (unchanged), `AWAITING_PHOTO_NUDGE`, `ERROR_FALLBACK_MESSAGE`.
   - Helper `_largest_photo(photo) -> TelegramPhotoSize`.
   - `process_update(...)` implementing the routing table in `requirements.md`, catching `TelegramClient`/`Bouncer` errors around the download+evaluate block only, logging via `logger.exception`, and sending the fallback.
   - `run_polling_loop`/`poll_once` accept and thread through `sessions` and `bouncer`.
3. **Green — update `src/telegram_documentaries/main.py`** to construct `SessionStore()` and `Bouncer(api_key=settings.gemini_api_key)` and pass them into `run_polling_loop`.
4. Verify: `./scripts/test` then `./scripts/hooks`.

---

## Task Group 6: Full Verification & Live Smoke Test
1. Run `./scripts/test` (all green) and `./scripts/hooks` (ruff, ruff-format, mypy strict, pytest, gitleaks all pass).
2. Launch the bot locally and confirm the startup log line `Telegram polling loop started...` appears.
3. Report the two manual checks for the user to run on their phone:
   - Negative: send a photo of an object/landscape → cheeky rejection naming the subject, no interview.
   - Positive: send a portrait of a person → `hey mate!`, session admitted.

---

## Task Group 7: Bug Fix — Primary-Subject Car Rejection, Schema Ordering & Verdict Logging (TDD)
1. **Red — update `tests/test_models.py`, `tests/test_bouncer.py`, `tests/test_polling.py`:**
   - Assert `BouncerVerdict.model_fields` order puts `subject` first (`list(BouncerVerdict.model_fields.keys()) == ["subject", "is_human", "rejection_message"]`), that each field carries a non-empty `description`, and that an empty/whitespace `subject` raises `ValidationError`.
   - Assert `BOUNCER_INSTRUCTION` explicitly requires a human to be the primary/central subject and rejects cars/vehicles even when a driver, reflection, or background bystander is visible.
   - Assert `Bouncer.evaluate` logs `event=bouncer_verdict stage=bouncer is_human=... subject=...` at `INFO` level on both human and non-human verdicts.
   - Assert `/start` and `/restart` text messages in `INTERVIEWING` reset the session back to `AWAITING_PHOTO` (clearing the portrait) and reply with `AWAITING_PHOTO_NUDGE`.
2. **Green — update `models.py`, `bouncer.py`, `poller.py`:**
   - Reorder `BouncerVerdict` fields (`subject`, `is_human`, `rejection_message`), add `Field(description=...)`, and validate non-empty `subject`.
   - Tighten `BOUNCER_INSTRUCTION` and `BOUNCER_PROMPT` and emit the `event=bouncer_verdict` info log in `Bouncer.evaluate`.
   - Handle `/start` and `/restart` in `poller.py`.
3. **Verify:** Run `./scripts/test` and `./scripts/hooks`.
