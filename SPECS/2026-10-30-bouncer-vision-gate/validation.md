# Phase 2 Validation Checklist: The Bouncer (Vision Gate) & Session State

## Automated Verification
- [x] `./scripts/test` — full pytest suite passes (81 passed; Phase 1 tests rewritten to the new signature, no skips/xfails added to dodge failures).
- [x] `./scripts/hooks` — `ruff check`, `ruff format --check`, `mypy` (strict, `src` + `tests`), `pytest`, and `pre-commit run --all-files` (incl. `gitleaks`) all pass.
- [x] No new `# type: ignore`, `noqa`, or `Any` escape hatches introduced without a documented reason.

## Contract Verification
- [x] `TelegramPhotoSize`, `TelegramFile`, `GetFileResponse`, `BouncerVerdict` exist as Pydantic v2 models and are the only shapes crossing module boundaries (no raw dicts).
- [x] `TelegramMessage.photo` is optional and text-only messages still parse.
- [x] `SessionState.chat_id` is a strict int; `SessionPhase` is an enum with `AWAITING_PHOTO` and `INTERVIEWING`.
- [x] `BouncerError` is raised (never silently swallowed) for malformed or failed Gemini responses.

## Behaviour Verification (logic-level, mocked)
- [x] Human photo → `hey mate!` sent, phase `INTERVIEWING`, portrait bytes stored.
- [x] Non-human photo → cheeky `rejection_message` sent, phase reset to `AWAITING_PHOTO`, portrait cleared.
- [x] Text while `AWAITING_PHOTO` → `AWAITING_PHOTO_NUDGE` sent, phase unchanged.
- [x] Empty/absent `message` → no send, no state change.
- [x] `chat_id` A state never leaks into `chat_id` B.
- [x] Download failure and Gemini failure each → `ERROR_FALLBACK_MESSAGE`, loud traceback log, state unchanged.

## Live Verification (user, on phone — manual smoke test pending user phone run)
- [ ] **Negative test (user phone test):** send a photo of a coffee mug / landscape → bot replies with a humorous, specific rejection and does not start an interview.
- [ ] **Positive test (user phone test):** send a clear portrait of a person → bot replies `hey mate!` and admits the session.
- [ ] **Terminal log check (during user phone test):** terminal logs show `stage=bouncer` and `stage=gateway` entries with `chat_id` for both tests.

## Documentation Synchronisation
- [x] `SPECS/ROADMAP.md` Phase 2 marked **Completed** with a delivery summary.
- [x] `SPECS/TECH.md` updated if any model id, module layout, or session-state detail changed from the contract.
- [x] `README.md` documents the new behaviour (photo gate, `google-genai` dependency) and any new env requirement.
- [x] `SPECS/2026-10-30-bouncer-vision-gate/` files reflect anything that deviated during implementation, with user approval.
