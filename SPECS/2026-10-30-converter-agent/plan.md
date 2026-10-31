# Phase 4 Implementation Plan: The Converter Agent (Hybrid Animal Portrait)

All implementation follows **Red/Green TDD**: write failing tests first, run `./scripts/test` to confirm failure, implement the minimal code to pass, and verify with `./scripts/test` and `./scripts/hooks`. Do not commit during implementation.

---

## Task Group 1: Boundary Schemas (`GeneratedImage`, `SendPhotoPayload`, `SendPhotoResponse`) & Session State (`hybrid_portrait`) (TDD)
1. **Red — extend `tests/test_models.py` and `tests/test_session.py`:**
   - In `tests/test_models.py`:
     - Validate `GeneratedImage(image_bytes=b"...", mime_type="image/png")` accepts non-empty bytes and valid `image/*` MIME types, and rejects empty `image_bytes` (`b""`) or non-image `mime_type` (e.g. `""`, `"text/plain"`) with `ValidationError`.
     - Validate `SendPhotoPayload(chat_id=123, photo_bytes=b"...", caption=...)` enforces `StrictInt` on `chat_id`, rejects empty `photo_bytes`, and validates optional `caption` (stripping or rejecting whitespace-only captions if provided).
     - Validate `SendPhotoResponse(ok=True, result=TelegramMessage(...))` parses valid Telegram `sendPhoto` responses and rejects non-bool `ok` or malformed `result`.
   - In `tests/test_session.py`:
     - Verify default `SessionState` initializes `hybrid_portrait=None`.
     - Verify `set_hybrid_portrait(chat_id, hybrid_portrait)` stores non-empty `hybrid_portrait` bytes and raises `ValueError` on empty bytes (`b""`).
     - Verify `start_interview` and `reset` both clear `hybrid_portrait=None`.
     - Verify two different `chat_id`s never share `hybrid_portrait`.
2. **Green — update `src/telegram_documentaries/models.py` and `src/telegram_documentaries/session.py`:**
   - Add `GeneratedImage`, `SendPhotoPayload`, and `SendPhotoResponse` to `models.py`.
   - Add `hybrid_portrait: bytes | None = None` to `SessionState` and `@log_stage("session") def set_hybrid_portrait(self, chat_id: int, hybrid_portrait: bytes) -> SessionState` to `SessionStore`, clearing `hybrid_portrait` in `start_interview` and `reset`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 2: Telegram `sendPhoto` Client Method (`src/telegram_documentaries/telegram_client.py`) (TDD)
1. **Red — extend `tests/test_telegram_client.py`:**
   - Verify `client.send_photo(chat_id=12345, photo_bytes=b"\x89PNG...")` posts to `https://api.telegram.org/bot<TOKEN>/sendPhoto` with `data={"chat_id": 12345}` (and optional `caption` when provided), multipart `files={"photo": ("hybrid_portrait.png", photo_bytes, "image/png")}`, and `timeout=SEND_PHOTO_TIMEOUT_SECONDS`, returning a validated `TelegramMessage`.
   - Verify `send_photo` raises `ValidationError` when `photo_bytes` is empty (`b""`) or `chat_id` is not a `StrictInt`.
   - Verify `send_photo` raises `TelegramClientError` (without leaking the bot token) on HTTP errors, network timeouts, invalid JSON, or responses failing `SendPhotoResponse` schema validation.
2. **Green — update `src/telegram_documentaries/telegram_client.py`:**
   - Add `SEND_PHOTO_TIMEOUT_SECONDS = 30` and `@log_stage("telegram_api") def send_photo(self, chat_id: int, photo_bytes: bytes, caption: str | None = None) -> TelegramMessage`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 3: The Converter Agent (`src/telegram_documentaries/converter.py`) (TDD)
1. **Red — create `tests/test_converter.py`** (mock `google.genai.Client`; no network):
   - Verify `Converter` defaults to `DEFAULT_CONVERTER_MODEL = "gemini-3.1-flash-image-preview"`.
   - Verify `generate_hybrid_portrait(portrait_bytes, dossier)` calls `client.models.generate_content` in a single multimodal invocation passing:
     - `model="gemini-3.1-flash-image-preview"`
     - `contents` containing both the prompt text (referencing `dossier.suggested_animal`, `dossier.summary`, and instructions to fuse the user's facial likeness, composition, and posture with the animal archetype) and `types.Part.from_bytes(data=portrait_bytes, mime_type="image/jpeg")`
     - `config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"], system_instruction=CONVERTER_INSTRUCTION)`
   - Verify `generate_hybrid_portrait` extracts the generated image part (`part.inline_data.data` and `part.inline_data.mime_type`) from `response.candidates[0].content.parts`, validates it via `GeneratedImage`, logs `event=converter_portrait_generated stage=converter suggested_animal=%r mime_type=%r bytes=%d`, and returns the `GeneratedImage.image_bytes`.
   - Verify `generate_hybrid_portrait` handles responses that contain both a text part and an inline image part by extracting the inline image part.
   - Verify `generate_hybrid_portrait` raises `ConverterError` when:
     - `portrait_bytes` is empty (`b""`).
     - `response.candidates` is empty or missing content parts.
     - Safety block / finish reason blocks generation (including `block_reason` / `finish_reason` diagnostics in the error message).
     - The response contains only text parts and no `inline_data` image bytes (or `inline_data.data` is empty).
     - The `google-genai` SDK raises an exception (chained via `raise ConverterError(...) from exc`).
2. **Green — create `src/telegram_documentaries/converter.py`:**
   - Implement `DEFAULT_CONVERTER_MODEL = "gemini-3.1-flash-image-preview"`, `CONVERTER_INSTRUCTION`, `ConverterError`, and `Converter` with `@log_stage("converter") def generate_hybrid_portrait(self, portrait_bytes: bytes, dossier: BehavioralDossier) -> bytes`.
3. **Verify:** Run `./scripts/test`.

---

## Task Group 4: Gateway Orchestration & Retry Resilience (`poller.py` & `main.py`) (TDD)
1. **Red — update `tests/test_polling.py`:**
   - When the final interview answer completes the interview (`turn_result.is_complete=True` with `dossier`):
     - `sessions.complete_interview` transitions the chat to `GENERATING_MEDIA` and stores `dossier`.
     - `converter.generate_hybrid_portrait(portrait=portrait, dossier=dossier)` is called immediately.
     - `sessions.set_hybrid_portrait(chat_id=chat_id, hybrid_portrait=hybrid_bytes)` stores the synthesized image.
     - `client.send_photo(chat_id=chat_id, photo_bytes=hybrid_bytes)` delivers the hybrid portrait to Telegram (and the Phase 3 interim dossier text message is NOT sent).
   - When `converter.generate_hybrid_portrait` raises `ConverterError` (or `client.send_photo` raises `TelegramClientError`) upon completing the interview:
     - Logs `event=converter_failed stage=gateway chat_id=...` with traceback.
     - Sends `CONVERTER_ERROR_FALLBACK_MESSAGE` to the user.
     - Preserves `phase == SessionPhase.GENERATING_MEDIA`, `portrait`, and `dossier` with `hybrid_portrait is None`.
   - When a user in `GENERATING_MEDIA` sends a non-command text message:
     - If `state.hybrid_portrait is None` (previous Converter attempt failed) and `state.portrait` and `state.dossier` are present: retries `converter.generate_hybrid_portrait` + `client.send_photo` and stores `hybrid_portrait` on success.
     - If `state.hybrid_portrait is not None` (hybrid portrait already delivered): sends `INTERVIEW_COMPLETE_NUDGE`.
   - `/start` and `/restart` in `GENERATING_MEDIA` reset `portrait`, `current_question`, `interview_history`, `dossier`, and `hybrid_portrait` and return the chat to `AWAITING_PHOTO`.
2. **Green — update `src/telegram_documentaries/poller.py` and `src/telegram_documentaries/main.py`:**
   - Thread `converter: Converter` through `process_update`, `poll_once`, `run_polling_loop`, and `main()`.
   - Add `@log_stage("converter") def _generate_hybrid_portrait(...)` and `_deliver_hybrid_portrait(...)` helper in `poller.py`.
3. **Verify:** Run `./scripts/test` and `./scripts/hooks`.
