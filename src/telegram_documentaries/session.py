"""In-memory per-chat session state machine."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, StrictInt

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import BehavioralDossier, InterviewTurn


class SessionPhase(StrEnum):
    """Explicit phases a user's documentary session can be in."""

    AWAITING_PHOTO = "awaiting_photo"
    INTERVIEWING = "interviewing"
    GENERATING_MEDIA = "generating_media"
    COMPLETED = "completed"


class SessionState(BaseModel):
    """Versioned state for a single Telegram chat, keyed by chat_id."""

    chat_id: StrictInt
    phase: SessionPhase = SessionPhase.AWAITING_PHOTO
    portrait: bytes | None = None
    current_question: str | None = None
    interview_history: list[InterviewTurn] = Field(default_factory=list)
    dossier: BehavioralDossier | None = None
    hybrid_portrait: bytes | None = None
    script: str | None = None
    ephemeral_files: list[Path] = Field(default_factory=list)


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
    def register_ephemeral_file(self, chat_id: int, path: Path) -> SessionState:
        """Track a temporary local media file path associated with a chat."""
        state = self.get(chat_id)
        if path not in state.ephemeral_files:
            state.ephemeral_files.append(path)
        return state

    @log_stage("session")
    def cleanup_ephemeral_files(self, chat_id: int) -> SessionState:
        """Delete any tracked temporary media files on disk for a chat and clear the list."""
        state = self.get(chat_id)
        for path in list(state.ephemeral_files):
            path.unlink(missing_ok=True)
        state.ephemeral_files = []
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
        self.cleanup_ephemeral_files(chat_id)
        state = self.get(chat_id)
        state.portrait = portrait
        state.phase = SessionPhase.INTERVIEWING
        state.current_question = cleaned_question
        state.interview_history = []
        state.dossier = None
        state.hybrid_portrait = None
        state.script = None
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
    def set_script(self, chat_id: int, script: str) -> SessionState:
        """Store the generated documentary narration script for a chat."""
        cleaned = script.strip()
        if not cleaned:
            raise ValueError("script must not be empty.")
        state = self.get(chat_id)
        state.script = cleaned
        return state

    @log_stage("session")
    def complete_documentary(self, chat_id: int) -> SessionState:
        """Transition a chat session to COMPLETED after voice narration delivery."""
        self.cleanup_ephemeral_files(chat_id)
        state = self.get(chat_id)
        state.phase = SessionPhase.COMPLETED
        return state

    @log_stage("session")
    def reset(self, chat_id: int) -> SessionState:
        """Execute the 3-step session reset routine for a chat without restarting the process."""
        state = self.get(chat_id)
        # 1. Cancel active session
        state.phase = SessionPhase.AWAITING_PHOTO
        state.current_question = None
        # 2. Purge ephemeral assets on disk and in memory
        self.cleanup_ephemeral_files(chat_id)
        state.portrait = None
        state.hybrid_portrait = None
        # 3. Re-initialize agent memory
        state.interview_history = []
        state.dossier = None
        state.script = None
        logger.info("event=session_reset stage=session chat_id=%s", chat_id)
        return state
