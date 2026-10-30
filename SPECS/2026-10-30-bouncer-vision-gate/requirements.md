# Phase 2 Requirements: The Bouncer (Vision Gate) & Session State

## Context & Goal
Phase 1 delivered a Telegram long-polling gateway that replies `"hey mate!"` to every message. Phase 2 gives the bot eyes: **The Bouncer**, a Gemini vision gate that inspects uploaded photos, confirms a human is present, and either admits the user to the pipeline or rejects them with a cheeky in-character message. This phase also introduces the first real **per-`chat_id` session state machine**, which every later phase builds on.

Roadmap reference: Phase 2 in `SPECS/ROADMAP.md`.

## User-Visible Behaviour
| Situation | Bot response | Session effect |
|---|---|---|
| Photo containing a human | `hey mate!` (existing `HARDCODED_REPLY`, unchanged so the Activity 3 check still passes) | Portrait bytes stored; phase → `INTERVIEWING` |
| Photo without a human (pet, car, food, landscape, object) | Cheeky, in-character rejection that names what it actually saw, e.g. "That's a toaster, mate. Magnificent specimen — but I need a human." | Session reset; phase → `AWAITING_PHOTO` |
| Text sent while `AWAITING_PHOTO` | Polite in-character nudge asking for a portrait photo | Unchanged (`AWAITING_PHOTO`) |
| Text/photo while `INTERVIEWING` | Not yet specified beyond the rules below — a new photo is re-evaluated and, if human, replaces the stored portrait; text is left for Phase 3 | Re-evaluation may replace portrait |
| Update with no message, or non-photo/non-text message | Silently ignored (logged at debug/info) | Unchanged |
| Telegram download failure or Bouncer/Gemini failure | In-character fallback ("My binoculars fogged up — send that photo again?"), never a stack trace | Unchanged |

## Decisions (explicit, no legacy)
1. **No backward compatibility.** `process_update`'s signature changes from `(update, client)` to `(update, client, sessions, bouncer)`. Phase 1 tests are rewritten, not preserved with shims.
2. **Agent boundary uses `google-genai`, not ADK `Runner`.** `SPECS/TECH.md` permits "Google ADK & `google-genai` SDK". A full ADK Runner would introduce a second session store that duplicates the `chat_id` state machine we are required to own; per YAGNI the Bouncer is a thin, testable wrapper around `google.genai.Client.models.generate_content` with a Pydantic `response_schema`. This keeps one source of truth for session state and one model boundary.
3. **Portrait storage is in-memory bytes**, not temp files, so `/restart` cleanup semantics (Phase 7) are trivial: dropping the session drops the media. Max one portrait per session.
4. **Largest photo size wins.** Telegram sends a list of `photo` sizes; we pick the one with the greatest `width * height` for classification quality.
5. **Fail loud internally, degrade gracefully to the user.** Malformed Gemini output or a download error raises a typed exception inside the Bouncer/client, is logged loudly by `@log_stage`, and the gateway converts it into an in-character fallback message without corrupting session state (per `TECH.md` policy).
6. **Rejection copy comes from the model**, constrained by schema (`rejection_message`), so rejections name the real subject and never read like an HTTP error. A deterministic local fallback copy is used only if the model omits it.

## Boundary Contracts (new, Pydantic v2)
- `TelegramPhotoSize`: `file_id: str`, `file_unique_id: str`, `width: StrictInt`, `height: StrictInt`, `file_size: StrictInt | None = None`.
- `TelegramMessage` extended with `photo: list[TelegramPhotoSize] | None = None` (text-only messages keep parsing unchanged).
- `TelegramFile`: `file_id: str`, `file_unique_id: str`, `file_size: StrictInt | None = None`, `file_path: str | None = None`.
- `GetFileResponse`: `ok: StrictBool`, `result: TelegramFile`.
- `BouncerVerdict`: `is_human: StrictBool`, `subject: str` (what was actually seen), `rejection_message: str = ""`.
- `SessionPhase` enum: `AWAITING_PHOTO`, `INTERVIEWING`.
- `SessionState`: `chat_id: StrictInt`, `phase: SessionPhase = AWAITING_PHOTO`, `portrait: bytes | None = None`.

All model output from Gemini is parsed into `BouncerVerdict`; raw JSON/dicts never cross module boundaries.

## In Scope
- `google-genai` added to dependencies; Bouncer model id `gemini-3.1-flash-lite-preview` configurable and defaulted per `TECH.md`.
- `TelegramClient.get_file(file_id)` and `TelegramClient.download_file(file_path) -> bytes`.
- `SessionStore` (in-memory dict keyed by `chat_id`) with `get`, `reset`, and phase/portrait mutation.
- Photo routing in the gateway as described in the behaviour table.
- Structured logging via the existing `@log_stage` decorator (`stage=bouncer`, `stage=gateway`) with `chat_id` context.
- Repo hygiene: add tool cache directories (`.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`) to `.gitignore`.

## Out of Scope (YAGNI)
- Interview questions, dossier, image generation, scripting, TTS (Phases 3–6).
- `/restart` and `/start` full reset UX, out-of-order guards for later phases (Phase 7). Only the minimal `AWAITING_PHOTO` nudge is implemented here.
- Persistence, retries/backoff, rate limiting, i18n, and per-user concurrency (single-threaded loop).

## Acceptance Criteria
- A human photo yields `hey mate!` and leaves the session in `INTERVIEWING` with the portrait bytes retained.
- A non-human photo yields a cheeky rejection naming the subject and leaves the session in `AWAITING_PHOTO` with no portrait stored.
- Text while awaiting a photo yields the nudge and does not change the phase.
- Two different `chat_id`s never see each other's phase or portrait.
- A simulated Telegram download error and a simulated Gemini error each produce the in-character fallback, log a full traceback, and leave state unchanged.
- `./scripts/test` and `./scripts/hooks` pass (ruff, ruff-format, mypy strict, pytest, gitleaks).
