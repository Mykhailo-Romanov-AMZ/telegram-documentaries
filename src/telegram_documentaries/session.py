"""In-memory per-chat session state machine."""

from enum import StrEnum

from pydantic import BaseModel, StrictInt

from telegram_documentaries.logging_utils import log_stage


class SessionPhase(StrEnum):
    """Explicit phases a user's documentary session can be in."""

    AWAITING_PHOTO = "awaiting_photo"
    INTERVIEWING = "interviewing"


class SessionState(BaseModel):
    """Versioned state for a single Telegram chat, keyed by chat_id."""

    chat_id: StrictInt
    phase: SessionPhase = SessionPhase.AWAITING_PHOTO
    portrait: bytes | None = None


class SessionStore:
    """In-memory session store isolating state per chat_id."""

    def __init__(self) -> None:
        self._sessions: dict[int, SessionState] = {}

    def get(self, chat_id: int) -> SessionState:
        """Return the live state for a chat, creating a default one if needed."""
        state = self._sessions.get(chat_id)
        if state is None:
            state = SessionState(chat_id=chat_id)
            self._sessions[chat_id] = state
        return state

    @log_stage("session")
    def set_portrait(self, chat_id: int, portrait: bytes) -> SessionState:
        """Store a verified portrait and admit the chat to INTERVIEWING."""
        state = self.get(chat_id)
        state.portrait = portrait
        state.phase = SessionPhase.INTERVIEWING
        return state

    @log_stage("session")
    def reset(self, chat_id: int) -> SessionState:
        """Clear any portrait and return the chat to AWAITING_PHOTO."""
        state = self.get(chat_id)
        state.phase = SessionPhase.AWAITING_PHOTO
        state.portrait = None
        return state
