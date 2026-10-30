"""Tests for the Telegram long-polling loop, Bouncer vision gate, and Interviewer orchestration."""

import logging
from unittest.mock import MagicMock

import pytest
import requests

from telegram_documentaries.bouncer import DEFAULT_REJECTION_MESSAGE, Bouncer, BouncerError
from telegram_documentaries.interviewer import (
    Interviewer,
    InterviewerError,
    format_dossier_message,
)
from telegram_documentaries.models import (
    BehavioralDossier,
    BouncerVerdict,
    InterviewerTurnResult,
    InterviewTurn,
    TelegramChat,
    TelegramFile,
    TelegramMessage,
    TelegramPhotoSize,
    TelegramUpdate,
)
from telegram_documentaries.poller import (
    AWAITING_PHOTO_NUDGE,
    ERROR_FALLBACK_MESSAGE,
    INTERVIEW_COMPLETE_NUDGE,
    INTERVIEW_ERROR_FALLBACK_MESSAGE,
    poll_once,
    process_update,
    run_polling_loop,
)
from telegram_documentaries.session import SessionPhase, SessionStore
from telegram_documentaries.telegram_client import TelegramClient, TelegramClientError

PORTRAIT_BYTES = b"\xff\xd8large-portrait-bytes"
FIRST_QUESTION = "When you emerge from your burrow each morning, what is your first ritual?"


def _photo_update(chat_id: int, update_id: int = 100) -> TelegramUpdate:
    """Build a photo update with three sizes so 'largest wins' can be asserted."""
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=1,
            chat=TelegramChat(id=chat_id),
            photo=[
                TelegramPhotoSize(
                    file_id="small",
                    file_unique_id="u-small",
                    width=90,
                    height=90,
                    file_size=1000,
                ),
                TelegramPhotoSize(
                    file_id="large",
                    file_unique_id="u-large",
                    width=800,
                    height=800,
                    file_size=9000,
                ),
                TelegramPhotoSize(
                    file_id="medium",
                    file_unique_id="u-medium",
                    width=320,
                    height=320,
                    file_size=4000,
                ),
            ],
        ),
    )


def _text_update(chat_id: int, text: str = "hello", update_id: int = 101) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=2,
            chat=TelegramChat(id=chat_id),
            text=text,
        ),
    )


def _human_bouncer() -> MagicMock:
    bouncer = MagicMock(spec=Bouncer)
    bouncer.evaluate.return_value = BouncerVerdict(
        is_human=True,
        subject="a person",
    )
    return bouncer


def _ready_interviewer(first_question: str = FIRST_QUESTION) -> MagicMock:
    interviewer = MagicMock(spec=Interviewer)
    interviewer.start_interview.return_value = first_question
    return interviewer


def _client_with_portrait() -> MagicMock:
    client = MagicMock(spec=TelegramClient)
    client.get_file.return_value = TelegramFile(
        file_id="large",
        file_unique_id="u-large",
        file_path="photos/large.jpg",
    )
    client.download_file.return_value = PORTRAIT_BYTES
    return client


def test_human_photo_downloads_largest_size_starts_interview_and_sends_question_1() -> None:
    """A human photo picks the largest size, stores portrait + Q1, and sends Question 1."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    process_update(
        update=_photo_update(chat_id=98765),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    client.get_file.assert_called_once_with("large")
    client.download_file.assert_called_once_with("photos/large.jpg")
    bouncer.evaluate.assert_called_once_with(PORTRAIT_BYTES)
    interviewer.start_interview.assert_called_once_with(PORTRAIT_BYTES)
    client.send_message.assert_called_once_with(chat_id=98765, text=FIRST_QUESTION)

    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == PORTRAIT_BYTES
    assert state.current_question == FIRST_QUESTION
    assert state.interview_history == []


def test_interviewer_error_on_question_1_resets_session_and_sends_error_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If start_interview fails after Bouncer approval, resets session and sends fallback."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = MagicMock(spec=Interviewer)
    interviewer.start_interview.side_effect = InterviewerError("question 1 failed")
    sessions = SessionStore()

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.current_question is None


def test_non_human_photo_sends_cheeky_rejection_and_resets_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-human photo sends the model's rejection copy and clears session state."""
    client = _client_with_portrait()
    bouncer = MagicMock(spec=Bouncer)
    cheeky = "That's a toaster, mate. Magnificent specimen — but I need a human."
    bouncer.evaluate.return_value = BouncerVerdict(
        is_human=False,
        subject="a toaster",
        rejection_message=cheeky,
    )
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    with caplog.at_level(logging.WARNING):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    interviewer.start_interview.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=98765, text=cheeky)
    assert "event=gateway_rejection_fallback" not in caplog.text
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


@pytest.mark.parametrize("empty_rejection", ["", "   "])
def test_non_human_photo_with_empty_rejection_uses_local_fallback(
    caplog: pytest.LogCaptureFixture,
    empty_rejection: str,
) -> None:
    """An empty model rejection logs a warning and uses the local fallback."""
    client = _client_with_portrait()
    bouncer = MagicMock(spec=Bouncer)
    bouncer.evaluate.return_value = BouncerVerdict(
        is_human=False,
        subject="a rock",
        rejection_message=empty_rejection,
    )
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    with caplog.at_level(logging.WARNING):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=DEFAULT_REJECTION_MESSAGE)
    assert "event=gateway_rejection_fallback" in caplog.text
    assert "chat_id=98765" in caplog.text
    assert "subject='a rock'" in caplog.text
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_text_while_awaiting_photo_sends_nudge_and_leaves_phase_unchanged() -> None:
    """Text in AWAITING_PHOTO yields the in-character nudge without changing state."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    process_update(
        update=_text_update(chat_id=111, text="hello there"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    client.send_message.assert_called_once_with(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    interviewer.advance_interview.assert_not_called()
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_text_while_interviewing_advances_turn_records_answer_and_sends_next_question() -> None:
    """Text during INTERVIEWING calls advance_interview, records turn, and sends next_question."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=False,
        next_question="And where do you stash your favourite midnight snacks?",
        dossier=None,
    )
    sessions = SessionStore()
    sessions.start_interview(
        chat_id=111,
        portrait=PORTRAIT_BYTES,
        first_question=FIRST_QUESTION,
    )

    process_update(
        update=_text_update(chat_id=111, text="I brew a strong pot of tea."),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    interviewer.advance_interview.assert_called_once_with(
        [InterviewTurn(question=FIRST_QUESTION, answer="I brew a strong pot of tea.")]
    )
    client.send_message.assert_called_once_with(
        chat_id=111,
        text="And where do you stash your favourite midnight snacks?",
    )
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.current_question == "And where do you stash your favourite midnight snacks?"
    assert state.interview_history == [
        InterviewTurn(question=FIRST_QUESTION, answer="I brew a strong pot of tea.")
    ]


def test_text_while_interviewing_final_turn_completes_interview_and_sends_dossier() -> None:
    """When advance_interview completes, stores dossier, enters GENERATING_MEDIA, and sends it."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower who guards the biscuit tin.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    for idx in range(1, 5):
        sessions.record_answer(chat_id=111, answer=f"A{idx}", next_question=f"Q{idx + 1}?")

    process_update(
        update=_text_update(chat_id=111, text="Final answer 5"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.current_question is None
    assert state.dossier == dossier
    assert len(state.interview_history) == 5

    expected_text = format_dossier_message(dossier)
    assert "Eurasian Badger" in expected_text
    assert dossier.summary in expected_text
    client.send_message.assert_called_once_with(chat_id=111, text=expected_text)


@pytest.mark.parametrize("blank_text", ["", "   ", "\t\n"])
def test_empty_or_whitespace_text_during_interviewing_reprompts_without_advancing(
    blank_text: str,
) -> None:
    """Whitespace-only text during INTERVIEWING re-sends current_question without advancing."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    sessions = SessionStore()
    sessions.start_interview(
        chat_id=111,
        portrait=PORTRAIT_BYTES,
        first_question=FIRST_QUESTION,
    )

    process_update(
        update=_text_update(chat_id=111, text=blank_text),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    interviewer.advance_interview.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=111, text=FIRST_QUESTION)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.current_question == FIRST_QUESTION
    assert state.interview_history == []


def test_interviewer_error_during_turn_sends_fallback_and_preserves_history_and_question(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If advance_interview raises InterviewerError, sends fallback and preserves state."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    interviewer.advance_interview.side_effect = InterviewerError("gemini timeout mid-interview")
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.record_answer(chat_id=111, answer="A1", next_question="Q2?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="A2 attempt"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_called_once_with(
        chat_id=111,
        text=INTERVIEW_ERROR_FALLBACK_MESSAGE,
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.current_question == "Q2?"
    assert state.interview_history == [InterviewTurn(question="Q1?", answer="A1")]


def test_text_during_generating_media_sends_complete_nudge_and_leaves_state_unchanged() -> None:
    """Text in GENERATING_MEDIA sends INTERVIEW_COMPLETE_NUDGE without altering session state."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    dossier = BehavioralDossier(
        summary="Completed dossier.",
        suggested_animal="Red Panda",
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)

    process_update(
        update=_text_update(chat_id=111, text="Any more questions?"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    interviewer.advance_interview.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=111, text=INTERVIEW_COMPLETE_NUDGE)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.dossier == dossier


def test_update_without_message_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    """An update with no message sends nothing, changes no state, and is logged."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    with caplog.at_level(logging.DEBUG, logger="telegram_documentaries"):
        process_update(
            update=TelegramUpdate(update_id=102, message=None),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_not_called()
    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "update_id=102" in messages
    assert "reason=no_message" in messages


def test_message_without_photo_or_text_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-photo, non-text message is silently ignored apart from debug logging."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    with caplog.at_level(logging.DEBUG, logger="telegram_documentaries"):
        process_update(
            update=TelegramUpdate(
                update_id=103,
                message=TelegramMessage(message_id=3, chat=TelegramChat(id=222)),
            ),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_not_called()
    bouncer.evaluate.assert_not_called()
    assert "chat_id=222" in caplog.text
    assert "reason=no_photo_or_text" in caplog.text


def test_download_error_sends_fallback_logs_traceback_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Telegram download failure sends the fallback and does not corrupt session state."""
    client = MagicMock(spec=TelegramClient)
    client.get_file.return_value = TelegramFile(
        file_id="large",
        file_unique_id="u-large",
        file_path="photos/large.jpg",
    )
    client.download_file.side_effect = TelegramClientError("download exploded")
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    sessions = SessionStore()
    sessions.start_interview(
        chat_id=98765,
        portrait=b"existing-portrait",
        first_question="Existing Q1?",
    )

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    bouncer.evaluate.assert_not_called()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == b"existing-portrait"
    assert state.current_question == "Existing Q1?"


def test_bouncer_error_sends_fallback_logs_traceback_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Gemini/Bouncer failure sends the fallback and leaves the session untouched."""
    client = _client_with_portrait()
    bouncer = MagicMock(spec=Bouncer)
    bouncer.evaluate.side_effect = BouncerError("gemini exploded")
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_human_photo_during_interviewing_replaces_portrait_and_restarts_interview() -> None:
    """A new human photo mid-interview replaces the portrait, clears history, and asks Q1."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer(first_question="Fresh Question 1?")
    sessions = SessionStore()
    sessions.start_interview(chat_id=98765, portrait=b"old-portrait", first_question="Old Q1?")
    sessions.record_answer(chat_id=98765, answer="Old A1", next_question="Old Q2?")

    process_update(
        update=_photo_update(chat_id=98765, update_id=104),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    client.send_message.assert_called_once_with(chat_id=98765, text="Fresh Question 1?")
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == PORTRAIT_BYTES
    assert state.current_question == "Fresh Question 1?"
    assert state.interview_history == []


def test_human_photo_logs_bouncer_and_interviewer_stages_with_chat_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gateway photo processing emits stage=bouncer and stage=interviewer logs carrying chat_id."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    with caplog.at_level(logging.INFO):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    bouncer_records = [r for r in caplog.records if "stage=bouncer" in r.getMessage()]
    interviewer_records = [r for r in caplog.records if "stage=interviewer" in r.getMessage()]
    assert any("chat_id=98765" in record.getMessage() for record in bouncer_records)
    assert any("chat_id=98765" in record.getMessage() for record in interviewer_records)


def test_non_human_photo_during_interviewing_resets_session() -> None:
    """A rejected replacement photo mid-interview clears the stored portrait and history."""
    client = _client_with_portrait()
    bouncer = MagicMock(spec=Bouncer)
    cheeky = "That's a cat, mate. Impressive whiskers — but I need a human."
    bouncer.evaluate.return_value = BouncerVerdict(
        is_human=False,
        subject="a cat",
        rejection_message=cheeky,
    )
    interviewer = _ready_interviewer()
    sessions = SessionStore()
    sessions.start_interview(chat_id=98765, portrait=b"old-portrait", first_question="Q1?")

    process_update(
        update=_photo_update(chat_id=98765, update_id=107),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    client.send_message.assert_called_once_with(chat_id=98765, text=cheeky)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.current_question is None
    assert state.interview_history == []


@pytest.mark.parametrize("file_path", [None, ""])
def test_missing_or_empty_file_path_sends_fallback_without_downloading_or_evaluating(
    caplog: pytest.LogCaptureFixture,
    file_path: str | None,
) -> None:
    """A getFile response without a usable file_path degrades gracefully and preserves state."""
    client = MagicMock(spec=TelegramClient)
    client.get_file.return_value = TelegramFile(
        file_id="large",
        file_unique_id="u-large",
        file_path=file_path,
    )
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    sessions = SessionStore()
    sessions.start_interview(chat_id=98765, portrait=b"existing-portrait", first_question="Q1?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )

    client.download_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == b"existing-portrait"


def test_two_chat_ids_do_not_leak_phase_or_portrait() -> None:
    """Admitting one chat leaves another chat awaiting a photo with no portrait."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    sessions = SessionStore()

    process_update(
        update=_photo_update(chat_id=1001, update_id=105),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )
    process_update(
        update=_text_update(chat_id=2002, update_id=106),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    assert sessions.get(chat_id=1001).phase == SessionPhase.INTERVIEWING
    assert sessions.get(chat_id=1001).portrait == PORTRAIT_BYTES
    assert sessions.get(chat_id=2002).phase == SessionPhase.AWAITING_PHOTO
    assert sessions.get(chat_id=2002).portrait is None
    client.send_message.assert_any_call(chat_id=2002, text=AWAITING_PHOTO_NUDGE)


@pytest.mark.parametrize(
    "command_text",
    [
        "/start",
        "/restart",
        "  /start  ",
        "/RESTART",
        "/start@NatureDocBot",
        "/restart@NatureDocBot",
    ],
)
def test_start_or_restart_command_resets_session_from_interviewing_or_generating_media(
    command_text: str,
) -> None:
    """/start and /restart in INTERVIEWING or GENERATING_MEDIA reset the session."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=b"existing-portrait", first_question="Q1?")
    sessions.record_answer(chat_id=111, answer="A1", next_question="Q2?")

    process_update(
        update=_text_update(chat_id=111, text=command_text),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )

    client.send_message.assert_called_once_with(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.current_question is None
    assert state.interview_history == []
    assert state.dossier is None

    # Also test reset from GENERATING_MEDIA while another chat's session remains untouched
    dossier = BehavioralDossier(summary="Summary.", suggested_animal="Otter")
    sessions.start_interview(chat_id=111, portrait=b"p1", first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    sessions.start_interview(chat_id=222, portrait=b"other-portrait", first_question="Other Q1?")

    process_update(
        update=_text_update(chat_id=111, text="/restart", update_id=120),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )
    assert sessions.get(chat_id=111).phase == SessionPhase.AWAITING_PHOTO
    assert sessions.get(chat_id=111).dossier is None
    assert sessions.get(chat_id=222).phase == SessionPhase.INTERVIEWING
    assert sessions.get(chat_id=222).portrait == b"other-portrait"


def test_poll_once_fetches_updates_replies_and_advances_offset() -> None:
    """poll_once processes all returned updates and returns max_update_id + 1."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.return_value = [
        _text_update(chat_id=111, text="First message", update_id=200),
        _text_update(chat_id=222, text="Second message", update_id=201),
    ]
    sessions = SessionStore()
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()

    next_offset = poll_once(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        offset=200,
        timeout=30,
    )

    assert next_offset == 202
    client.get_updates.assert_called_once_with(offset=200, timeout=30)
    assert client.send_message.call_count == 2
    client.send_message.assert_any_call(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    client.send_message.assert_any_call(chat_id=222, text=AWAITING_PHOTO_NUDGE)


def test_poll_once_preserves_offset_when_no_updates_returned() -> None:
    """poll_once returns the existing offset unchanged when getUpdates returns an empty list."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.return_value = []
    sessions = SessionStore()
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()

    next_offset = poll_once(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        offset=55,
        timeout=30,
    )

    assert next_offset == 55
    client.send_message.assert_not_called()


def test_poll_once_handles_get_updates_error_gracefully_and_preserves_offset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """poll_once logs and catches network errors from get_updates without crashing."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.side_effect = requests.ConnectionError("Telegram unreachable")
    sessions = SessionStore()
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            offset=42,
            timeout=30,
        )

    assert next_offset == 42
    error_messages = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "Telegram unreachable" in error_messages or "stage=polling" in error_messages


def test_poll_once_handles_send_message_error_gracefully_and_advances_offset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """poll_once logs per-update errors and still advances offset so later updates proceed."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.return_value = [
        _text_update(chat_id=333, text="Trigger send error", update_id=300),
        _text_update(chat_id=444, text="Second update in batch", update_id=301),
    ]
    client.send_message.side_effect = [
        requests.Timeout("sendMessage timed out"),
        MagicMock(spec=TelegramMessage),
    ]
    sessions = SessionStore()
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            offset=300,
            timeout=30,
        )

    assert next_offset == 302
    assert client.send_message.call_count == 2
    error_messages = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "update_id=300" in error_messages


def test_run_polling_loop_logs_startup_and_advances_offsets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run_polling_loop logs startup readiness and advances offset across iterations."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.side_effect = [
        [_text_update(chat_id=555, text="hi", update_id=10)],
        [],
    ]
    sessions = SessionStore()
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()

    with caplog.at_level(logging.INFO):
        run_polling_loop(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            timeout=5,
            max_iterations=2,
        )

    info_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "Telegram polling loop started" in info_messages
    assert client.get_updates.call_args_list[0].kwargs == {"offset": None, "timeout": 5}
    assert client.get_updates.call_args_list[1].kwargs == {"offset": 11, "timeout": 5}
