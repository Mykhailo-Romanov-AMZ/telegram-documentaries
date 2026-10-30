"""Tests for the in-memory per-chat session state store."""

from telegram_documentaries.session import SessionPhase, SessionState, SessionStore


def test_get_creates_default_state_awaiting_photo_without_portrait() -> None:
    """A previously unseen chat starts in AWAITING_PHOTO with no portrait."""
    store = SessionStore()

    state = store.get(chat_id=1001)

    assert isinstance(state, SessionState)
    assert state.chat_id == 1001
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


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
