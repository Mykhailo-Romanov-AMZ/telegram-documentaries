# Project Mission: The Telegram Documentaries

## Vision
**The Telegram Documentaries** is an interactive Telegram bot powered by Google's Agent Development Kit (ADK) and Gemini multimodal models. A user uploads a portrait photo, answers a brief 5–7 question behavioral interview, and receives a customized hybrid wildlife portrait along with a dramatic, British-style nature documentary narration delivered as both text and a synthesized voice note.

## End-to-End User Experience
1. **Initiation & Upload:** The user starts a chat (`/start`) and uploads a portrait photo.
2. **Vision Gate (The Bouncer):** The bot inspects the photo. If no human subject is detected (e.g., pets, cars, food, landscapes), the bot replies with a cheeky rejection message, resets the session, and asks for a human photo.
3. **Behavioral Interview (The Interviewer / Orchestrator):** Once a human photo is accepted, the Interviewer asks 5–7 sequential questions, one at a time, probing the user's daily habits, habitats, and quirks. From the answers, it compiles a structured behavioral dossier and selects a fitting spirit animal.
4. **Hybrid Portrait Synthesis (The Converter):** Passing the original user photo and behavioral dossier directly to Gemini's native image generation model, the bot synthesizes a hybrid animal-human portrait and sends it to the Telegram chat.
5. **Documentary Script (The Scripter):** Using the behavioral dossier, the Scripter composes a single-paragraph (~60–90 words) comedy-wildlife-documentary narration in an observational, dramatic British naturalist tone.
6. **Voice Narration (The Narrator):** The script is forwarded directly to the Gemini TTS endpoint, rendered into a Telegram-compatible audio file (OGG/MP3), and delivered to the user's chat.

## In-Scope Capabilities
- **Telegram Long Polling Gateway:** Receives messages, commands, and photos from Telegram without requiring webhooks or public HTTPS endpoints.
- **5-Stage Multimodal Pipeline:**
  1. **The Bouncer** (`gemini-3.1-flash-lite-preview`) — human detection vision gate.
  2. **The Interviewer** (`gemini-3.1-flash-lite-preview`) — stateful orchestrator conducting a 5–7 turn Q&A and producing a behavioral dossier + spirit animal.
  3. **The Converter** (`gemini-3.1-flash-image-preview`) — multimodal fusion of portrait photo and dossier into a hybrid animal image.
  4. **The Scripter** (`gemini-3.1-flash-lite-preview`) — one-paragraph (~60–90 words) British nature documentary script writer.
  5. **The Narrator** (`gemini-3.1-flash-tts-preview`) — execution tool converting the script to OGG/MP3 voice/audio for Telegram.
- **Session Lifecycle & Reset (`/start` and `/restart`):** In-memory state isolated by `chat_id`. Sending `/start` or `/restart` at any point immediately purges the user's in-memory session state and deletes any temporary local media files without restarting the bot process.
- **Input & Stage Resilience:** Graceful handling of out-of-order inputs (e.g., sending text when awaiting a photo, or sending a photo mid-interview) and upstream API timeouts without crashing the bot or leaking errors into another user's session.

## Out of Scope (YAGNI)
- **No Persistent Database:** Session state lives strictly in memory keyed by `chat_id`; no SQL/NoSQL persistence or historical user profiles across process restarts.
- **No Webhooks or Public Web Servers:** Communication uses Telegram Long Polling exclusively.
- **No Video Generation:** Media output is strictly a static hybrid image and an audio narration clip.
- **No Multi-User Group Chat Orchestration:** Each session corresponds to a single `chat_id` state machine.
- **No Autonomous Agent for TTS:** Text-to-Speech is a deterministic execution step called by the orchestrator, not an autonomous reasoning agent.

## Success Criteria
- **Happy Path Completion:** Uploading a valid human portrait and answering 5–7 interview prompts reliably yields a hybrid animal image, a 60–90 word documentary paragraph, and a playable audio narration in Telegram.
- **Bouncer Rejection:** Uploading a non-human image produces a witty rejection and cleanly resets the session back to awaiting a human portrait.
- **Clean Reset:** `/start` and `/restart` wipe state and temporary artifacts at any stage of the pipeline.
- **Stage Guards:** Unexpected message types at any state-machine phase prompt a helpful guidance message rather than raising unhandled exceptions.

## Non-Negotiables
- **Session Isolation:** Never leak photos, interview answers, dossiers, or state across different `chat_id` sessions.
- **Secret Hygiene:** `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` must reside exclusively in `.env` (which is gitignored) and must never be hardcoded or committed to version control.
