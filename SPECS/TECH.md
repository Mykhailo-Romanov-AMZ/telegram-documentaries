# Technical Contract: The Telegram Documentaries

## Technology Stack
- **Language:** Python 3.11+
- **Agent Framework:** Google Agent Development Kit (ADK) & `google-genai` SDK
- **Telegram Integration:** Telegram Bot API via **Long Polling** (no webhooks)
- **Data Validation:** Pydantic v2 for strict schema validation at all module and external API boundaries
- **Configuration:** `python-dotenv` loading secrets from `.env` (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`)
- **Quality & Testing:** `pytest`, `ruff` (linting and formatting), `mypy` (strict static type checking)

## Model Assignments
| Pipeline Component | Role | Model Identifier | Type |
|---|---|---|---|
| **The Bouncer** | Human detection vision gate | `gemini-3.1-flash-lite-preview` | Specialist Agent |
| **The Interviewer** | Stateful 5–7 turn Q&A & dossier builder | `gemini-3.1-flash-lite-preview` | Orchestrator & Agent |
| **The Converter** | Multimodal portrait + dossier hybrid image synthesis | `gemini-3.1-flash-image-preview` | Specialist Agent |
| **The Scripter** | Single-paragraph (~60–90 words) British documentary script | `gemini-3.1-flash-lite-preview` | Specialist Agent |
| **The Narrator** | Text-to-Speech audio rendering (OGG/MP3) | `gemini-3.1-flash-tts-preview` | Execution Tool |

## Architecture: Hub-and-Spoke State Machine
- **Orchestrator Pattern:** The **Interviewer** doubles as the central orchestrator in a hub-and-spoke architecture, managing conversation state and delegating work to the specialist agents (Bouncer, Converter, Scripter) and the TTS execution tool (Narrator).
- **Explicit Session Phases:** Each user conversation is tracked via an explicit state machine enum:
  - `AWAITING_PHOTO`: Initial state after `/start`, `/restart`, or Bouncer rejection; expects a user portrait photo.
  - `INTERVIEWING`: Human photo verified; tracks question count (5–7 turns), conversation history, and accumulates the behavioral dossier.
  - `GENERATING_MEDIA`: Interview complete; sequentially invokes Converter, Scripter, and Narrator TTS.
  - `COMPLETED`: Documentary delivered; prompts user to `/restart` for a new documentary.
- **Direct Multimodal Fusion:** The Converter passes the original photo bytes/file and the structured interview dossier directly to `gemini-3.1-flash-image-preview` and streams the resulting image back to Telegram without an intermediate text-only image description hop.

## Boundary Contracts & Validation Policy
- **Untrusted External Inputs:** All incoming Telegram updates, LLM structured outputs, image payloads, and TTS responses are treated as untrusted external data.
- **Pydantic at the Edge:** Parse and validate all external payloads into typed Pydantic models at system boundaries. Never pass raw dictionaries or unvalidated JSON across internal module boundaries.
- **Schemas Over Regexes:** Use structured output schemas (Pydantic models) for agent responses (e.g., Bouncer verdict + cheeky reason, Interviewer completion status + dossier + chosen animal) rather than brittle string parsing or regular expressions.

## Session State & Storage Policy
- **In-Memory Store:** Maintain a single shared in-memory state driver keyed by `chat_id: int` mapping to a versioned Pydantic `SessionState` model.
- **Temporary Media Cleanup:** Any temporary files (downloaded user portraits or synthesized audio/image files) must be scoped to the user's session and deleted immediately upon `/start`, `/restart`, Bouncer rejection, or pipeline completion.

## Logging & Error Handling Policy
- **Structured Logging via Decorators:** Use comprehensive structured logging across pipeline stages and API calls, preferring decorators over cluttering core business logic with repetitive log statements.
- **No Silent Failures:** Never use bare `except:`, `except Exception: pass`, or un-logged fallbacks.
- **Graceful User Degradation:**
  - On non-critical or background tasks, fail loudly and log full context.
  - On a user's active Telegram conversation path, catch upstream API or validation errors, log them loudly with stack traces and `chat_id` context, and reply to the user with a polite, in-character fallback message so the bot process remains healthy and the user can retry or `/restart`.

## Testing & Verification Policy (Red/Green TDD)
- **Test-First Workflow:** Every feature and bug fix follows strict Red/Green TDD: write failing behavioral tests first, implement minimal code to pass, then refactor.
- **Developer Scripts (`scripts/`):**
  - `scripts/test`: Runs the full automated test suite (`pytest`).
  - `scripts/hooks`: Runs static analysis, formatting, and type checks (`ruff check`, `ruff format --check`, `mypy`).
- **Mocked External Boundaries in Unit Tests:** Unit and integration tests must mock Telegram API calls and Gemini SDK endpoints deterministically so the test suite runs fast, offline, and without consuming API quota.

## Repository Hygiene & Documentation Policy
- **Secrets Protection:** `.env` must remain in `.gitignore`. `.env.example` documents required environment variables with safe placeholders.
- **Synchronized Documentation:** `README.md`, `SPECS/TECH.md`, `SPECS/MISSION.md`, and `SPECS/ROADMAP.md` must stay synchronized with implemented commands, scripts, and architecture after every verified feature.
