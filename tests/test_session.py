"""Tests for the in-memory per-chat session state store."""

import pytest

from telegram_documentaries.models import BehavioralDossier, InterviewTurn
from telegram_documentaries.session import SessionPhase, SessionState, SessionStore


def test_get_creates_default_state_awaiting_photo_without_portrait() -> None:
    """A previously unseen chat starts in AWAITING_PHOTO with no portrait or interview data."""
    store = SessionStore()

    state = store.get(chat_id=1001)

    assert isinstance(state, SessionState)
    assert state.chat_id == 1001
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.current_question is None
    assert state.interview_history == []
    assert state.dossier is None
    assert state.hybrid_portrait is None
    assert state.script is None


def test_get_is_stable_across_repeated_calls() -> None:
    """Repeated get calls return the same live state object for a chat."""
    store = SessionStore()

    first = store.get(chat_id=1001)
    second = store.get(chat_id=1001)

    assert first is second


def test_set_portrait_stores_bytes_and_moves_to_interviewing() -> None:
    """set_portrait retains the portrait bytes and admits the chat to INTERVIEWING."""
    store = SessionStore()
    portrait = b"\xff\xd8portrait-bytes"

    state = store.set_portrait(chat_id=1001, portrait=portrait)

    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == portrait
    assert store.get(chat_id=1001).portrait == portrait


def test_reset_clears_portrait_and_returns_to_awaiting_photo() -> None:
    """reset drops the portrait and moves the chat back to AWAITING_PHOTO."""
    store = SessionStore()
    store.set_portrait(chat_id=1001, portrait=b"portrait")

    state = store.reset(chat_id=1001)

    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert store.get(chat_id=1001).phase == SessionPhase.AWAITING_PHOTO


def test_reset_on_unknown_chat_creates_clean_state() -> None:
    """Resetting a chat with no prior state still yields a clean AWAITING_PHOTO state."""
    store = SessionStore()

    state = store.reset(chat_id=9999)

    assert state.chat_id == 9999
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_two_chat_ids_are_fully_isolated() -> None:
    """State and portraits never leak between different chat IDs."""
    store = SessionStore()

    store.set_portrait(chat_id=1001, portrait=b"portrait-a")
    other = store.get(chat_id=2002)

    assert other.phase == SessionPhase.AWAITING_PHOTO
    assert other.portrait is None

    store.set_portrait(chat_id=2002, portrait=b"portrait-b")
    store.reset(chat_id=1001)

    assert store.get(chat_id=1001).portrait is None
    assert store.get(chat_id=2002).portrait == b"portrait-b"
    assert store.get(chat_id=2002).phase == SessionPhase.INTERVIEWING


def test_session_phase_includes_generating_media() -> None:
    """SessionPhase defines AWAITING_PHOTO, INTERVIEWING, GENERATING_MEDIA, and COMPLETED."""
    assert SessionPhase.AWAITING_PHOTO.value == "awaiting_photo"
    assert SessionPhase.INTERVIEWING.value == "interviewing"
    assert SessionPhase.GENERATING_MEDIA.value == "generating_media"
    assert SessionPhase.COMPLETED.value == "completed"


def test_start_interview_sets_portrait_first_question_and_clears_prior_interview_state() -> None:
    """start_interview stores portrait + Q1, enters INTERVIEWING, and clears prior history."""
    store = SessionStore()
    dossier = BehavioralDossier(
        summary="Prior dossier summary.",
        suggested_animal="Capybara",
    )
    store.start_interview(chat_id=1001, portrait=b"old-portrait", first_question="Old Q1?")
    store.complete_interview(chat_id=1001, answer="Old A1", dossier=dossier)

    state = store.start_interview(
        chat_id=1001,
        portrait=b"new-portrait",
        first_question="What is your preferred morning forage?",
    )

    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == b"new-portrait"
    assert state.current_question == "What is your preferred morning forage?"
    assert state.interview_history == []
    assert state.dossier is None


def test_record_answer_appends_turn_and_updates_current_question() -> None:
    """record_answer appends InterviewTurn(question, answer) and updates current_question."""
    store = SessionStore()
    store.start_interview(chat_id=1001, portrait=b"portrait", first_question="Q1?")

    state = store.record_answer(chat_id=1001, answer="A1", next_question="Q2?")

    assert state.phase == SessionPhase.INTERVIEWING
    assert state.current_question == "Q2?"
    assert state.interview_history == [InterviewTurn(question="Q1?", answer="A1")]

    store.record_answer(chat_id=1001, answer="A2", next_question="Q3?")
    assert state.current_question == "Q3?"
    assert state.interview_history == [
        InterviewTurn(question="Q1?", answer="A1"),
        InterviewTurn(question="Q2?", answer="A2"),
    ]


def test_record_answer_raises_when_current_question_is_none() -> None:
    """Calling record_answer when current_question is None raises ValueError."""
    store = SessionStore()

    with pytest.raises(ValueError, match="current_question"):
        store.record_answer(chat_id=1001, answer="Orphan answer", next_question="Q2?")


def test_complete_interview_appends_final_turn_stores_dossier_and_enters_generating_media() -> None:
    """complete_interview records final answer, clears current_question, and stores dossier."""
    store = SessionStore()
    store.start_interview(chat_id=1001, portrait=b"portrait", first_question="Q1?")
    store.record_answer(chat_id=1001, answer="A1", next_question="Q2?")
    dossier = BehavioralDossier(
        summary="Nocturnal snacker with a fondness for warm burrows.",
        suggested_animal="Eurasian Badger",
    )

    state = store.complete_interview(chat_id=1001, answer="A2", dossier=dossier)

    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.current_question is None
    assert state.dossier == dossier
    assert state.interview_history == [
        InterviewTurn(question="Q1?", answer="A1"),
        InterviewTurn(question="Q2?", answer="A2"),
    ]


def test_complete_interview_raises_when_current_question_is_none() -> None:
    """Calling complete_interview when current_question is None raises ValueError."""
    store = SessionStore()
    dossier = BehavioralDossier(
        summary="Summary text.",
        suggested_animal="Barn Owl",
    )

    with pytest.raises(ValueError, match="current_question"):
        store.complete_interview(chat_id=1001, answer="Orphan answer", dossier=dossier)


def test_reset_clears_interview_history_current_question_and_dossier() -> None:
    """reset clears portrait, current_question, interview_history, and dossier."""
    store = SessionStore()
    dossier = BehavioralDossier(
        summary="Summary text.",
        suggested_animal="Barn Owl",
    )
    store.start_interview(chat_id=1001, portrait=b"portrait", first_question="Q1?")
    store.record_answer(chat_id=1001, answer="A1", next_question="Q2?")
    store.complete_interview(chat_id=1001, answer="A2", dossier=dossier)

    state = store.reset(chat_id=1001)

    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.current_question is None
    assert state.interview_history == []
    assert state.dossier is None


def test_two_chat_ids_never_share_interview_history_current_question_or_dossier() -> None:
    """Two different chat_ids have completely independent interview state and dossiers."""
    store = SessionStore()
    dossier_a = BehavioralDossier(
        summary="Chat A behavioral summary.",
        suggested_animal="Red Panda",
    )

    store.start_interview(chat_id=1001, portrait=b"portrait-a", first_question="Chat A Q1?")
    store.start_interview(chat_id=2002, portrait=b"portrait-b", first_question="Chat B Q1?")
    store.record_answer(chat_id=1001, answer="Chat A A1", next_question="Chat A Q2?")

    state_a = store.get(chat_id=1001)
    state_b = store.get(chat_id=2002)

    assert state_a.current_question == "Chat A Q2?"
    assert state_b.current_question == "Chat B Q1?"
    assert len(state_a.interview_history) == 1
    assert state_b.interview_history == []

    store.complete_interview(chat_id=1001, answer="Chat A A2", dossier=dossier_a)

    assert state_a.phase == SessionPhase.GENERATING_MEDIA
    assert state_a.dossier == dossier_a
    assert state_b.phase == SessionPhase.INTERVIEWING
    assert state_b.dossier is None
    assert state_b.current_question == "Chat B Q1?"


def test_set_hybrid_portrait_stores_bytes_and_rejects_empty_bytes() -> None:
    """set_hybrid_portrait stores non-empty hybrid_portrait bytes and rejects empty bytes."""
    store = SessionStore()
    hybrid_bytes = b"\x89PNG-hybrid-portrait"

    state = store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=hybrid_bytes)

    assert state.hybrid_portrait == hybrid_bytes
    assert store.get(chat_id=1001).hybrid_portrait == hybrid_bytes

    with pytest.raises(ValueError, match="hybrid_portrait"):
        store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"")


def test_start_interview_and_reset_clear_hybrid_portrait() -> None:
    """Both start_interview and reset clear any previously stored hybrid_portrait."""
    store = SessionStore()
    store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"\x89PNG-hybrid")
    assert store.get(chat_id=1001).hybrid_portrait == b"\x89PNG-hybrid"

    state_after_start = store.start_interview(
        chat_id=1001,
        portrait=b"new-portrait",
        first_question="Q1?",
    )
    assert state_after_start.hybrid_portrait is None

    store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"\x89PNG-hybrid-2")
    state_after_reset = store.reset(chat_id=1001)
    assert state_after_reset.hybrid_portrait is None


def test_two_chat_ids_never_share_hybrid_portrait() -> None:
    """hybrid_portrait is strictly isolated per chat_id."""
    store = SessionStore()
    store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"\x89PNG-chat-a")

    assert store.get(chat_id=1001).hybrid_portrait == b"\x89PNG-chat-a"
    assert store.get(chat_id=2002).hybrid_portrait is None


@pytest.mark.parametrize("blank_script", ["", "   ", "\t\n"])
def test_set_script_stores_stripped_script_and_rejects_empty_strings(blank_script: str) -> None:
    """set_script stores a stripped non-empty narration string and rejects blank strings."""
    store = SessionStore()
    narration = "  Here in the midnight kitchen, the Eurasian Badger stalks a biscuit.  "

    state = store.set_script(chat_id=1001, script=narration)

    assert state.script == "Here in the midnight kitchen, the Eurasian Badger stalks a biscuit."
    assert store.get(chat_id=1001).script == (
        "Here in the midnight kitchen, the Eurasian Badger stalks a biscuit."
    )

    with pytest.raises(ValueError, match="script"):
        store.set_script(chat_id=1001, script=blank_script)


def test_start_interview_and_reset_clear_script() -> None:
    """Both start_interview and reset clear any previously stored script."""
    store = SessionStore()
    store.set_script(chat_id=1001, script="Here in the suburban undergrowth...")
    assert store.get(chat_id=1001).script == "Here in the suburban undergrowth..."

    state_after_start = store.start_interview(
        chat_id=1001,
        portrait=b"new-portrait",
        first_question="Q1?",
    )
    assert state_after_start.script is None

    store.set_script(chat_id=1001, script="Another narration paragraph.")
    state_after_reset = store.reset(chat_id=1001)
    assert state_after_reset.script is None


def test_two_chat_ids_never_share_script() -> None:
    """script is strictly isolated per chat_id."""
    store = SessionStore()
    store.set_script(chat_id=1001, script="Chat A documentary narration.")

    assert store.get(chat_id=1001).script == "Chat A documentary narration."
    assert store.get(chat_id=2002).script is None


def test_complete_documentary_transitions_chat_to_completed_with_chat_isolation() -> None:
    """complete_documentary sets phase to COMPLETED while keeping other chats isolated."""
    store = SessionStore()
    dossier = BehavioralDossier(
        summary="Nocturnal snacker with a fondness for warm burrows.",
        suggested_animal="Eurasian Badger",
    )
    store.start_interview(chat_id=1001, portrait=b"portrait-a", first_question="Q1?")
    store.complete_interview(chat_id=1001, answer="A1", dossier=dossier)
    store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"\x89PNG-hybrid")
    store.set_script(chat_id=1001, script="Here in the midnight kitchen...")
    store.start_interview(chat_id=2002, portrait=b"portrait-b", first_question="Chat B Q1?")

    state = store.complete_documentary(chat_id=1001)

    assert state.phase == SessionPhase.COMPLETED
    assert store.get(chat_id=1001).phase == SessionPhase.COMPLETED
    assert store.get(chat_id=1001).hybrid_portrait == b"\x89PNG-hybrid"
    assert store.get(chat_id=1001).script == "Here in the midnight kitchen..."
    assert store.get(chat_id=2002).phase == SessionPhase.INTERVIEWING


def test_start_interview_and_reset_transition_completed_session_and_clear_artifacts() -> None:
    """Both start_interview and reset transition a COMPLETED session and clear prior artifacts."""
    store = SessionStore()
    dossier = BehavioralDossier(
        summary="Prior dossier.",
        suggested_animal="Eurasian Badger",
    )
    store.start_interview(chat_id=1001, portrait=b"old-portrait", first_question="Old Q1?")
    store.complete_interview(chat_id=1001, answer="Old A1", dossier=dossier)
    store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"\x89PNG-old")
    store.set_script(chat_id=1001, script="Old script.")
    store.complete_documentary(chat_id=1001)
    assert store.get(chat_id=1001).phase == SessionPhase.COMPLETED

    restarted = store.start_interview(
        chat_id=1001,
        portrait=b"new-portrait",
        first_question="New Q1?",
    )
    assert restarted.phase == SessionPhase.INTERVIEWING
    assert restarted.portrait == b"new-portrait"
    assert restarted.current_question == "New Q1?"
    assert restarted.interview_history == []
    assert restarted.dossier is None
    assert restarted.hybrid_portrait is None
    assert restarted.script is None

    store.complete_interview(chat_id=1001, answer="New A1", dossier=dossier)
    store.set_hybrid_portrait(chat_id=1001, hybrid_portrait=b"\x89PNG-new")
    store.set_script(chat_id=1001, script="New script.")
    store.complete_documentary(chat_id=1001)
    assert store.get(chat_id=1001).phase == SessionPhase.COMPLETED

    after_reset = store.reset(chat_id=1001)
    assert after_reset.phase == SessionPhase.AWAITING_PHOTO
    assert after_reset.portrait is None
    assert after_reset.current_question is None
    assert after_reset.interview_history == []
    assert after_reset.dossier is None
    assert after_reset.hybrid_portrait is None
    assert after_reset.script is None
