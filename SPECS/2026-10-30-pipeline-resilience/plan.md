# Phase 7 Implementation Plan: Pipeline Resilience, Session Resets & Edge Guards

All implementation follows **Red/Green TDD**: write failing tests first, run `./scripts/test` to confirm failure, implement the minimal code to pass, and verify with `./scripts/test` and `./scripts/hooks`. Do not commit during implementation.

---

## Task Group 1: Ephemeral Asset Tracking & 3-Step Reset Routine in `SessionStore` and `TelegramMessage.caption` (TDD)
1. **Red — extend `tests/test_models.py` and `tests/test_session.py`:**
   - In `tests/test_models.py`:
     - Verify `TelegramMessage` accepts optional `caption: str | None = None` (default `None`).
   - In `tests/test_session.py`:
     - Verify `SessionState` initializes `ephemeral_files: list[Path] = []`.
     - Verify `SessionStore.register_ephemeral_file(chat_id, path)` records a `Path` for that `chat_id` without leaking to other `chat_id`s.
     - Verify `SessionStore.cleanup_ephemeral_files(chat_id)` deletes existing files on disk (`tmp_path`), tolerates already-deleted files (`missing_ok=True`), and empties `state.ephemeral_files`.
     - Verify `SessionStore.reset(chat_id)` performs all three reset actions:
       1. Cancels active session (`phase == SessionPhase.AWAITING_PHOTO`, `current_question is None`).
       2. Purges ephemeral assets (unlinks all `ephemeral_files` on disk and sets `portrait = None`, `hybrid_portrait = None`, `ephemeral_files = []`).
       3. Re-initializes agent memory (`interview_history == []`, `dossier is None`, `script is None`) and emits `event=session_reset stage=session chat_id=...`.
     - Verify `start_interview` and `complete_documentary` also clean up any lingering `ephemeral_files` for that `chat_id`.
2. **Green — update `src/telegram_documentaries/models.py` and `src/telegram_documentaries/session.py`:**
   - Add `caption: str | None = None` to `TelegramMessage`.
   - Add `ephemeral_files: list[Path] = Field(default_factory=list)` to `SessionState`.
   - Implement `register_ephemeral_file`, `cleanup_ephemeral_files`, and update `reset`, `start_interview`, and `complete_documentary` in `SessionStore`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 2: Phase Guards, Caption Reset Commands, Retry Hardening & Back-to-Back Run Verification (`poller.py`) (TDD)
1. **Red — update `tests/test_polling.py`:**
   - **Photo during `INTERVIEWING` guard (replaces old replacement-photo tests):**
     - Verify that sending a photo while in `SessionPhase.INTERVIEWING` does NOT call `client.get_file`, `bouncer.evaluate`, or `interviewer.start_interview`, preserves `portrait`, `current_question`, and `interview_history`, and replies with `INTERVIEW_PHOTO_GUARD_MESSAGE`.
   - **Photo during `COMPLETED` and `GENERATING_MEDIA` guards:**
     - Verify that sending a photo while in `SessionPhase.COMPLETED` replies with `INTERVIEW_COMPLETE_NUDGE` without calling `Bouncer`.
     - Verify that sending a photo while in `SessionPhase.GENERATING_MEDIA` triggers the pending media retry (`_handle_generating_media_text`) without overwriting `portrait` or calling `Bouncer`.
   - **Reset commands in photo captions & ephemeral file purge:**
     - Verify that `/restart` or `/start` sent as a text message OR as a photo `caption` in any phase unlinks tracked `ephemeral_files` on disk, resets session state to `AWAITING_PHOTO`, and replies with `AWAITING_PHOTO_NUDGE`.
   - **Narrator temp file registration with `SessionStore`:**
     - Verify that during `_synthesize_and_send_voice`, the temporary audio file path is registered via `sessions.register_ephemeral_file(chat_id, audio_path)` and cleaned up via `sessions.cleanup_ephemeral_files(chat_id)` on completion or reset.
   - **Unsupported payload guard (messages with neither `photo` nor `text`):**
     - Verify that a sticker/voice/document message in `AWAITING_PHOTO` replies with `AWAITING_PHOTO_NUDGE`; in `INTERVIEWING` replies with `INTERVIEW_PHOTO_GUARD_MESSAGE`; in `COMPLETED` replies with `INTERVIEW_COMPLETE_NUDGE`; in `GENERATING_MEDIA` triggers pending stage retry.
   - **Fallback `send_message` resilience:**
     - Verify that if the fallback `client.send_message` itself raises `TelegramClientError` inside `_handle_photo`, `_handle_interview_text`, or `_deliver_hybrid_portrait`, the error is logged with `event=*_fallback_failed` and does not escape `process_update`.
   - **Two consecutive end-to-end documentary runs in one process:**
     - Verify a full Run 1 (human photo → 5 interview answers → hybrid portrait → script → voice note → `COMPLETED`) followed by `/restart` and a full Run 2 for the same `chat_id` succeeds cleanly without any state leakage from Run 1.
2. **Green — update `src/telegram_documentaries/poller.py`:**
   - Add `INTERVIEW_PHOTO_GUARD_MESSAGE`.
   - Check `_is_reset_command` on both `message.text` and `message.caption` at the top of `process_update` before photo/text branching.
   - Guard `_handle_photo` by checking `sessions.get(chat_id).phase` so photos are only sent to `Bouncer` when `phase == SessionPhase.AWAITING_PHOTO`.
   - Route unsupported messages (neither `photo` nor `text`) by phase to send helpful guidance prompts.
   - Register and clean up the temporary audio file path with `sessions` in `_synthesize_and_send_voice`.
   - Wrap fallback `client.send_message` calls in `_handle_photo`, `_handle_interview_text`, and `_deliver_hybrid_portrait` with `try/except TelegramClientError`.
3. **Verify:** Run `./scripts/test` and `./scripts/hooks`.
