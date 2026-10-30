"""In-memory per-chat session state machine."""

from enum import StrEnum

from pydantic import BaseModel, Field, StrictInt

from telegram_documentaries.logging_utils import log_stage
from telegram_documentaries.models import BehavioralDossier, InterviewTurn


class SessionPhase(StrEnum):
    """Explicit phases a user's documentary session can be in."""

    AWAITING_PHOTO = "awaiting_photo"
    INTERVIEWING = "interviewing"
    GENERATING_MEDIA = "generating_media"


class SessionState(BaseModel):
    """Versioned state for a single Telegram chat, keyed by chat_id."""

    chat_id: StrictInt
    phase: SessionPhase = SessionPhase.AWAITING_PHOTO
    portrait: bytes | None = None
    current_question: str | None = None
    interview_history: list[InterviewTurn] = Field(default_factory=list)
    dossier: BehavioralDossier | None = None
    hybrid_portrait: bytes | None = None


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
    def start_interview(
        self,
        chat_id: int,
        portrait: bytes,
        first_question: str,
    ) -> SessionState:
        """Store a verified portrait, set Question 1, and reset prior interview artifacts."""
        cleaned_question = first_question.strip()
        if not cleaned_question:
            raise ValueError("first_question must not be empty.")
        state = self.get(chat_id)
        state.portrait = portrait
        state.phase = SessionPhase.INTERVIEWING
        state.current_question = cleaned_question
        state.interview_history = []
        state.dossier = None
        state.hybrid_portrait = None
        return state

    @log_stage("session")
    def record_answer(
        self,
        chat_id: int,
        answer: str,
        next_question: str,
    ) -> SessionState:
        """Append the answered turn to interview_history and advance current_question."""
        state = self.get(chat_id)
        if state.current_question is None:
            raise ValueError("Cannot record answer when current_question is None.")
        cleaned_next = next_question.strip()
        if not cleaned_next:
            raise ValueError("next_question must not be empty.")
        turn = InterviewTurn(question=state.current_question, answer=answer)
        state.interview_history.append(turn)
        state.current_question = cleaned_next
        state.phase = SessionPhase.INTERVIEWING
        return state

    @log_stage("session")
    def complete_interview(
        self,
        chat_id: int,
        answer: str,
        dossier: BehavioralDossier,
    ) -> SessionState:
        """Record the final answer, store the compiled dossier, and move to GENERATING_MEDIA."""
        state = self.get(chat_id)
        if state.current_question is None:
            raise ValueError("Cannot complete interview when current_question is None.")
        turn = InterviewTurn(question=state.current_question, answer=answer)
        state.interview_history.append(turn)
        state.current_question = None
        state.dossier = dossier
        state.phase = SessionPhase.GENERATING_MEDIA
        return state

    @log_stage("session")
    def set_hybrid_portrait(self, chat_id: int, hybrid_portrait: bytes) -> SessionState:
        """Store the synthesized hybrid animal portrait bytes for a chat."""
        if not hybrid_portrait:
            raise ValueError("hybrid_portrait must not be empty.")
        state = self.get(chat_id)
        state.hybrid_portrait = hybrid_portrait
        return state

    @log_stage("session")
    def reset(self, chat_id: int) -> SessionState:
        """Clear portrait and interview artifacts and return the chat to AWAITING_PHOTO."""
        state = self.get(chat_id)
        state.phase = SessionPhase.AWAITING_PHOTO
        state.portrait = None
        state.current_question = None
        state.interview_history = []
        state.dossier = None
        state.hybrid_portrait = None
        return state
