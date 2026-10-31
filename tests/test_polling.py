"""Tests for the Telegram long-polling loop, Bouncer vision gate, and Interviewer orchestration."""

import logging
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from telegram_documentaries.bouncer import DEFAULT_REJECTION_MESSAGE, Bouncer, BouncerError
from telegram_documentaries.converter import Converter, ConverterError
from telegram_documentaries.interviewer import (
    Interviewer,
    InterviewerError,
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
from telegram_documentaries.narrator import Narrator, NarratorError
from telegram_documentaries.poller import (
    AWAITING_PHOTO_NUDGE,
    CONVERTER_ERROR_FALLBACK_MESSAGE,
    ERROR_FALLBACK_MESSAGE,
    INTERVIEW_COMPLETE_NUDGE,
    INTERVIEW_ERROR_FALLBACK_MESSAGE,
    INTERVIEW_PHOTO_GUARD_MESSAGE,
    NARRATOR_ERROR_FALLBACK_MESSAGE,
    SCRIPTER_ERROR_FALLBACK_MESSAGE,
    poll_once,
    process_update,
    run_polling_loop,
)
from telegram_documentaries.scripter import Scripter, ScripterError
from telegram_documentaries.session import SessionPhase, SessionStore
from telegram_documentaries.telegram_client import TelegramClient, TelegramClientError

PORTRAIT_BYTES = b"\xff\xd8large-portrait-bytes"
HYBRID_PORTRAIT_BYTES = b"\x89PNG\r\n\x1a\nhybrid-animal-portrait"
VOICE_AUDIO_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00drozdov-voice-narration"
FIRST_QUESTION = "When you emerge from your burrow each morning, what is your first ritual?"
SAMPLE_SCRIPT = (
    "Here in the midnight kitchen, the solitary Eurasian Badger emerges from its duvet burrow "
    "to guard the biscuit tin with theatrical devotion."
)


def _photo_update(
    chat_id: int,
    update_id: int = 100,
    caption: str | None = None,
) -> TelegramUpdate:
    """Build a photo update with three sizes so 'largest wins' can be asserted."""
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=1,
            chat=TelegramChat(id=chat_id),
            caption=caption,
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


def _ready_converter(hybrid_bytes: bytes = HYBRID_PORTRAIT_BYTES) -> MagicMock:
    converter = MagicMock(spec=Converter)
    converter.generate_hybrid_portrait.return_value = hybrid_bytes
    return converter


def _ready_scripter(script_text: str = SAMPLE_SCRIPT) -> MagicMock:
    scripter = MagicMock(spec=Scripter)
    scripter.generate_script.return_value = script_text
    return scripter


def _ready_narrator(
    audio_bytes: bytes = VOICE_AUDIO_BYTES,
    created_paths: list[Path] | None = None,
) -> MagicMock:
    narrator = MagicMock(spec=Narrator)
    narrator.synthesize_speech.return_value = audio_bytes

    @contextmanager
    def _temp_file(script: str) -> Iterator[Path]:
        audio = narrator.synthesize_speech(script)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(audio)
            if created_paths is not None:
                created_paths.append(temp_path)
            yield temp_path
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    narrator.synthesize_to_temp_file.side_effect = _temp_file
    return narrator


def _completed_media_state(
    *,
    with_hybrid_portrait: bool,
) -> tuple[SessionStore, BehavioralDossier]:
    """Build a GENERATING_MEDIA session with a completed dossier and optional portrait."""
    dossier = BehavioralDossier(
        summary="Completed dossier awaiting retry.",
        suggested_animal="Red Panda",
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    if with_hybrid_portrait:
        sessions.set_hybrid_portrait(chat_id=111, hybrid_portrait=HYBRID_PORTRAIT_BYTES)
    return sessions, dossier


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
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
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
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
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
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
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
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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


def test_final_turn_sends_hybrid_portrait_then_delivers_script_and_voice() -> None:
    """When advance_interview completes, deliver portrait, script, and voice, then COMPLETED."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    created_paths: list[Path] = []
    narrator = _ready_narrator(created_paths=created_paths)
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
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.COMPLETED
    assert state.current_question is None
    assert state.dossier == dossier
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT
    assert len(state.interview_history) == 5

    converter.generate_hybrid_portrait.assert_called_once_with(
        PORTRAIT_BYTES,
        dossier,
    )
    client.send_photo.assert_called_once_with(
        chat_id=111,
        photo_bytes=HYBRID_PORTRAIT_BYTES,
    )
    scripter.generate_script.assert_called_once_with(dossier)
    client.send_message.assert_called_once_with(
        chat_id=111,
        text=SAMPLE_SCRIPT,
    )
    narrator.synthesize_to_temp_file.assert_called_once_with(SAMPLE_SCRIPT)
    client.send_voice.assert_called_once_with(
        chat_id=111,
        audio_bytes=VOICE_AUDIO_BYTES,
    )
    assert len(created_paths) == 1
    assert not created_paths[0].exists()


def test_final_turn_sends_hybrid_photo_then_script_then_voice_in_order() -> None:
    """Hybrid sendPhoto precedes sendMessage (script), which precedes sendVoice (narration)."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
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
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )

    delivery_calls = [
        mock_call[0]
        for mock_call in client.mock_calls
        if mock_call[0] in {"send_photo", "send_message", "send_voice"}
    ]
    assert delivery_calls == ["send_photo", "send_message", "send_voice"]
    client.send_photo.assert_called_once_with(
        chat_id=111,
        photo_bytes=HYBRID_PORTRAIT_BYTES,
    )
    client.send_message.assert_called_once_with(chat_id=111, text=SAMPLE_SCRIPT)
    client.send_voice.assert_called_once_with(chat_id=111, audio_bytes=VOICE_AUDIO_BYTES)


def test_converter_error_on_final_turn_logs_sends_fallback_and_preserves_dossier_for_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If Converter fails after final answer, logs event=converter_failed and skips Scripter."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = MagicMock(spec=Converter)
    converter.generate_hybrid_portrait.side_effect = ConverterError("darkroom exploded")
    scripter = _ready_scripter()
    narrator = _ready_narrator()
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

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    client.send_photo.assert_not_called()
    scripter.generate_script.assert_not_called()
    narrator.synthesize_to_temp_file.assert_not_called()
    client.send_voice.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=111,
        text=CONVERTER_ERROR_FALLBACK_MESSAGE,
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=converter_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.portrait == PORTRAIT_BYTES
    assert state.dossier == dossier
    assert state.hybrid_portrait is None
    assert state.script is None


def test_send_photo_error_on_final_turn_logs_sends_fallback_and_leaves_hybrid_portrait_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If send_photo fails, logs event=converter_failed, skips Scripter, and keeps state None."""
    client = MagicMock(spec=TelegramClient)
    client.send_photo.side_effect = TelegramClientError("sendPhoto failed")
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    scripter.generate_script.assert_not_called()
    narrator.synthesize_to_temp_file.assert_not_called()
    client.send_voice.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=111,
        text=CONVERTER_ERROR_FALLBACK_MESSAGE,
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=converter_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.portrait == PORTRAIT_BYTES
    assert state.dossier == dossier
    assert state.hybrid_portrait is None
    assert state.script is None


def test_scripter_error_after_converter_success_preserves_hybrid_portrait_and_sends_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If Scripter fails after Converter succeeds, log event=scripter_failed and keep portrait."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = MagicMock(spec=Scripter)
    scripter.generate_script.side_effect = ScripterError("typewriter jammed")
    narrator = _ready_narrator()
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    client.send_photo.assert_called_once_with(chat_id=111, photo_bytes=HYBRID_PORTRAIT_BYTES)
    scripter.generate_script.assert_called_once_with(dossier)
    narrator.synthesize_to_temp_file.assert_not_called()
    client.send_voice.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=111,
        text=SCRIPTER_ERROR_FALLBACK_MESSAGE,
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=scripter_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script is None


def test_script_send_message_error_logs_scripter_failed_and_leaves_script_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If sending the narration script fails, log event=scripter_failed and leave script None."""
    client = MagicMock(spec=TelegramClient)
    client.send_message.side_effect = [
        TelegramClientError("sendMessage failed for script"),
        MagicMock(spec=TelegramMessage),
    ]
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    narrator.synthesize_to_temp_file.assert_not_called()
    client.send_voice.assert_not_called()
    assert client.send_message.call_count == 2
    client.send_message.assert_any_call(chat_id=111, text=SAMPLE_SCRIPT)
    client.send_message.assert_any_call(chat_id=111, text=SCRIPTER_ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=scripter_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )

    state = sessions.get(chat_id=111)
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script is None


def test_script_send_failure_does_not_store_script() -> None:
    """A failed narration send must not call sessions.set_script and leaves script None."""
    client = MagicMock(spec=TelegramClient)
    client.send_message.side_effect = [
        TelegramClientError("sendMessage failed for script"),
        MagicMock(spec=TelegramMessage),
    ]
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with patch.object(sessions, "set_script") as set_script:
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=_ready_narrator(),
        )

    set_script.assert_not_called()
    state = sessions.get(chat_id=111)
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script is None


def test_generating_media_retries_converter_then_scripter_then_narrator_when_portrait_missing() -> (
    None
):
    """Text in GENERATING_MEDIA with no hybrid portrait retries Converter, Scripter, Narrator."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    created_paths: list[Path] = []
    narrator = _ready_narrator(created_paths=created_paths)
    dossier = BehavioralDossier(
        summary="Completed dossier awaiting retry.",
        suggested_animal="Red Panda",
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    assert sessions.get(chat_id=111).hybrid_portrait is None
    assert sessions.get(chat_id=111).script is None

    process_update(
        update=_text_update(chat_id=111, text="Please retry my portrait!"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )

    interviewer.advance_interview.assert_not_called()
    converter.generate_hybrid_portrait.assert_called_once_with(PORTRAIT_BYTES, dossier)
    client.send_photo.assert_called_once_with(chat_id=111, photo_bytes=HYBRID_PORTRAIT_BYTES)
    scripter.generate_script.assert_called_once_with(dossier)
    client.send_message.assert_called_once_with(chat_id=111, text=SAMPLE_SCRIPT)
    narrator.synthesize_to_temp_file.assert_called_once_with(SAMPLE_SCRIPT)
    client.send_voice.assert_called_once_with(chat_id=111, audio_bytes=VOICE_AUDIO_BYTES)
    assert len(created_paths) == 1
    assert not created_paths[0].exists()
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.COMPLETED
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_generating_media_retries_scripter_then_narrator_when_script_missing() -> None:
    """Text in GENERATING_MEDIA with portrait and no script skips Converter and runs Scripter."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    created_paths: list[Path] = []
    narrator = _ready_narrator(created_paths=created_paths)
    dossier = BehavioralDossier(
        summary="Completed dossier with portrait already delivered.",
        suggested_animal="Red Panda",
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    sessions.set_hybrid_portrait(chat_id=111, hybrid_portrait=HYBRID_PORTRAIT_BYTES)
    assert sessions.get(chat_id=111).script is None

    process_update(
        update=_text_update(chat_id=111, text="Retry my script please!"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )

    converter.generate_hybrid_portrait.assert_not_called()
    client.send_photo.assert_not_called()
    scripter.generate_script.assert_called_once_with(dossier)
    client.send_message.assert_called_once_with(chat_id=111, text=SAMPLE_SCRIPT)
    narrator.synthesize_to_temp_file.assert_called_once_with(SAMPLE_SCRIPT)
    client.send_voice.assert_called_once_with(chat_id=111, audio_bytes=VOICE_AUDIO_BYTES)
    assert len(created_paths) == 1
    assert not created_paths[0].exists()
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.COMPLETED
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_generating_media_second_scripter_failure_sends_fallback_and_keeps_portrait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second Scripter failure on the retry-only path sends fallback and keeps portrait."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = MagicMock(spec=Scripter)
    scripter.generate_script.side_effect = ScripterError("typewriter jammed again")
    narrator = _ready_narrator()
    sessions, dossier = _completed_media_state(with_hybrid_portrait=True)
    assert sessions.get(chat_id=111).script is None

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Retry my script please!"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    converter.generate_hybrid_portrait.assert_not_called()
    client.send_photo.assert_not_called()
    scripter.generate_script.assert_called_once_with(dossier)
    narrator.synthesize_to_temp_file.assert_not_called()
    client.send_voice.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=111,
        text=SCRIPTER_ERROR_FALLBACK_MESSAGE,
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=scripter_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script is None


def test_scripter_fallback_send_failure_is_logged_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing Scripter fallback send logs event=scripter_fallback_failed without escaping."""
    client = MagicMock(spec=TelegramClient)
    client.send_message.side_effect = TelegramClientError("fallback send failed")
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = MagicMock(spec=Scripter)
    scripter.generate_script.side_effect = ScripterError("typewriter jammed")
    sessions, _dossier = _completed_media_state(with_hybrid_portrait=True)

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Retry my script please!"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=_ready_narrator(),
        )

    client.send_message.assert_called_once_with(
        chat_id=111,
        text=SCRIPTER_ERROR_FALLBACK_MESSAGE,
    )
    assert "event=scripter_fallback_failed stage=gateway chat_id=111" in caplog.text
    state = sessions.get(chat_id=111)
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script is None


def test_narrator_error_after_script_success_logs_sends_fallback_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If Narrator fails after Converter and Scripter succeed, preserves GENERATING_MEDIA state."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = MagicMock(spec=Narrator)
    narrator.synthesize_to_temp_file.side_effect = NarratorError("microphone crackled")
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    client.send_photo.assert_called_once_with(chat_id=111, photo_bytes=HYBRID_PORTRAIT_BYTES)
    scripter.generate_script.assert_called_once_with(dossier)
    client.send_voice.assert_not_called()
    assert client.send_message.call_count == 2
    client.send_message.assert_any_call(chat_id=111, text=SAMPLE_SCRIPT)
    client.send_message.assert_any_call(chat_id=111, text=NARRATOR_ERROR_FALLBACK_MESSAGE)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=narrator_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_send_voice_error_cleans_up_temp_file_logs_narrator_failed_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If send_voice fails, temp file is unlinked, fallback sent, and state is preserved."""
    client = MagicMock(spec=TelegramClient)
    client.send_voice.side_effect = TelegramClientError("sendVoice failed")
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    created_paths: list[Path] = []
    narrator = _ready_narrator(created_paths=created_paths)
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    assert len(created_paths) == 1
    assert not created_paths[0].exists()
    client.send_message.assert_any_call(chat_id=111, text=NARRATOR_ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=narrator_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_generating_media_retries_only_narrator_when_portrait_and_script_exist() -> None:
    """Text in GENERATING_MEDIA with both portrait and script retries only Narrator + send_voice."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    created_paths: list[Path] = []
    narrator = _ready_narrator(created_paths=created_paths)
    dossier = BehavioralDossier(
        summary="Completed dossier.",
        suggested_animal="Red Panda",
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    sessions.set_hybrid_portrait(chat_id=111, hybrid_portrait=HYBRID_PORTRAIT_BYTES)
    sessions.set_script(chat_id=111, script=SAMPLE_SCRIPT)

    process_update(
        update=_text_update(chat_id=111, text="Retry my voiceover please!"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )

    interviewer.advance_interview.assert_not_called()
    converter.generate_hybrid_portrait.assert_not_called()
    client.send_photo.assert_not_called()
    scripter.generate_script.assert_not_called()
    client.send_message.assert_not_called()
    narrator.synthesize_to_temp_file.assert_called_once_with(SAMPLE_SCRIPT)
    client.send_voice.assert_called_once_with(chat_id=111, audio_bytes=VOICE_AUDIO_BYTES)
    assert len(created_paths) == 1
    assert not created_paths[0].exists()

    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.COMPLETED
    assert state.dossier == dossier
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_generating_media_second_narrator_failure_sends_fallback_and_preserves_artifacts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second Narrator failure on the TTS-only retry path sends fallback and preserves state."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = MagicMock(spec=Narrator)
    narrator.synthesize_to_temp_file.side_effect = NarratorError("microphone crackled again")
    sessions, dossier = _completed_media_state(with_hybrid_portrait=True)
    sessions.set_script(chat_id=111, script=SAMPLE_SCRIPT)

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Retry my voiceover again!"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    converter.generate_hybrid_portrait.assert_not_called()
    client.send_photo.assert_not_called()
    scripter.generate_script.assert_not_called()
    client.send_voice.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=111,
        text=NARRATOR_ERROR_FALLBACK_MESSAGE,
    )
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "event=narrator_failed stage=gateway chat_id=111" in r.getMessage()
        and r.exc_info is not None
        for r in error_records
    )
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.dossier == dossier
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_narrator_fallback_send_failure_is_logged_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing Narrator fallback send logs event=narrator_fallback_failed without escaping."""
    client = MagicMock(spec=TelegramClient)
    client.send_message.side_effect = TelegramClientError("narrator fallback send failed")
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = MagicMock(spec=Narrator)
    narrator.synthesize_to_temp_file.side_effect = NarratorError("microphone crackled")
    sessions, _dossier = _completed_media_state(with_hybrid_portrait=True)
    sessions.set_script(chat_id=111, script=SAMPLE_SCRIPT)

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Retry my voiceover please!"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    client.send_message.assert_called_once_with(
        chat_id=111,
        text=NARRATOR_ERROR_FALLBACK_MESSAGE,
    )
    assert "event=narrator_fallback_failed stage=gateway chat_id=111" in caplog.text
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.GENERATING_MEDIA
    assert state.hybrid_portrait == HYBRID_PORTRAIT_BYTES
    assert state.script == SAMPLE_SCRIPT


def test_final_turn_logs_converter_scripter_and_narrator_stages_with_chat_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Media generation emits stage=converter, stage=scripter, and stage=narrator with chat_id."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    dossier = BehavioralDossier(
        summary="A tea-obsessed nocturnal burrower.",
        suggested_animal="Eurasian Badger",
    )
    interviewer.advance_interview.return_value = InterviewerTurnResult(
        is_complete=True,
        next_question="",
        dossier=dossier,
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")

    with caplog.at_level(logging.INFO):
        process_update(
            update=_text_update(chat_id=111, text="Final answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    converter_records = [r for r in caplog.records if "stage=converter" in r.getMessage()]
    scripter_records = [r for r in caplog.records if "stage=scripter" in r.getMessage()]
    narrator_records = [r for r in caplog.records if "stage=narrator" in r.getMessage()]
    assert any("chat_id=111" in r.getMessage() for r in converter_records)
    assert any("chat_id=111" in r.getMessage() for r in scripter_records)
    assert any("chat_id=111" in r.getMessage() for r in narrator_records)


def test_completed_phase_sends_complete_nudge_without_calling_agents() -> None:
    """Any non-command text in SessionPhase.COMPLETED replies with INTERVIEW_COMPLETE_NUDGE."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    dossier = BehavioralDossier(
        summary="Completed dossier.",
        suggested_animal="Red Panda",
    )
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    sessions.set_hybrid_portrait(chat_id=111, hybrid_portrait=HYBRID_PORTRAIT_BYTES)
    sessions.set_script(chat_id=111, script=SAMPLE_SCRIPT)
    sessions.complete_documentary(chat_id=111)

    process_update(
        update=_text_update(chat_id=111, text="Any more questions?"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )

    interviewer.advance_interview.assert_not_called()
    converter.generate_hybrid_portrait.assert_not_called()
    client.send_photo.assert_not_called()
    scripter.generate_script.assert_not_called()
    narrator.synthesize_to_temp_file.assert_not_called()
    client.send_voice.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=111, text=INTERVIEW_COMPLETE_NUDGE)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.COMPLETED


@pytest.mark.parametrize("blank_text", ["", "   ", "\t\n"])
def test_empty_or_whitespace_text_during_interviewing_reprompts_without_advancing(
    blank_text: str,
) -> None:
    """Whitespace-only text during INTERVIEWING re-sends current_question without advancing."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
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
        converter=converter,
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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
    converter = _ready_converter()
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
            converter=converter,
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
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
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
        )

    client.send_message.assert_not_called()
    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "update_id=102" in messages
    assert "reason=no_message" in messages


def test_message_without_photo_or_text_sends_phase_guidance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unsupported message (sticker/voice/document) sends phase-appropriate guidance."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = _ready_interviewer()
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    sessions = SessionStore()

    # 1. AWAITING_PHOTO -> AWAITING_PHOTO_NUDGE
    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        process_update(
            update=TelegramUpdate(
                update_id=103,
                message=TelegramMessage(message_id=3, chat=TelegramChat(id=222)),
            ),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    client.send_message.assert_called_once_with(chat_id=222, text=AWAITING_PHOTO_NUDGE)
    assert "event=unsupported_payload stage=gateway chat_id=222" in caplog.text

    # 2. INTERVIEWING -> INTERVIEW_PHOTO_GUARD_MESSAGE
    client.reset_mock()
    sessions.start_interview(chat_id=222, portrait=PORTRAIT_BYTES, first_question="Q1?")
    process_update(
        update=TelegramUpdate(
            update_id=104,
            message=TelegramMessage(message_id=4, chat=TelegramChat(id=222)),
        ),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    client.send_message.assert_called_once_with(chat_id=222, text=INTERVIEW_PHOTO_GUARD_MESSAGE)
    assert sessions.get(chat_id=222).phase == SessionPhase.INTERVIEWING

    # 3. GENERATING_MEDIA -> retries pending stage
    client.reset_mock()
    dossier = BehavioralDossier(summary="Dossier.", suggested_animal="Red Panda")
    sessions.complete_interview(chat_id=222, answer="A1", dossier=dossier)
    process_update(
        update=TelegramUpdate(
            update_id=105,
            message=TelegramMessage(message_id=5, chat=TelegramChat(id=222)),
        ),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    assert sessions.get(chat_id=222).phase == SessionPhase.COMPLETED

    # 4. COMPLETED -> INTERVIEW_COMPLETE_NUDGE
    client.reset_mock()
    process_update(
        update=TelegramUpdate(
            update_id=106,
            message=TelegramMessage(message_id=6, chat=TelegramChat(id=222)),
        ),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    client.send_message.assert_called_once_with(chat_id=222, text=INTERVIEW_COMPLETE_NUDGE)


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

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    bouncer.evaluate.assert_not_called()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


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
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_photo_during_interviewing_sends_guard_message_and_preserves_interview_state() -> None:
    """Photo sent during INTERVIEWING replies with INTERVIEW_PHOTO_GUARD_MESSAGE without Bouncer."""
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
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
    )

    client.get_file.assert_not_called()
    client.download_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    interviewer.start_interview.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=98765,
        text=INTERVIEW_PHOTO_GUARD_MESSAGE,
    )
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == b"old-portrait"
    assert state.current_question == "Old Q2?"
    assert state.interview_history == [InterviewTurn(question="Old Q1?", answer="Old A1")]


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
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
        )

    bouncer_records = [r for r in caplog.records if "stage=bouncer" in r.getMessage()]
    interviewer_records = [r for r in caplog.records if "stage=interviewer" in r.getMessage()]
    assert any("chat_id=98765" in record.getMessage() for record in bouncer_records)
    assert any("chat_id=98765" in record.getMessage() for record in interviewer_records)


def test_photo_during_completed_or_generating_media_guards_state_without_calling_bouncer() -> None:
    """Photos in COMPLETED send complete nudge; photos in GENERATING_MEDIA retry pending stage."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    dossier = BehavioralDossier(summary="Summary.", suggested_animal="Red Panda")
    sessions = SessionStore()
    sessions.start_interview(chat_id=98765, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=98765, answer="A1", dossier=dossier)

    # Photo during GENERATING_MEDIA retries pending media stage without calling Bouncer
    process_update(
        update=_photo_update(chat_id=98765, update_id=107),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    assert sessions.get(chat_id=98765).phase == SessionPhase.COMPLETED
    assert sessions.get(chat_id=98765).portrait == PORTRAIT_BYTES

    # Photo during COMPLETED replies with INTERVIEW_COMPLETE_NUDGE
    client.reset_mock()
    process_update(
        update=_photo_update(chat_id=98765, update_id=108),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    client.send_message.assert_called_once_with(
        chat_id=98765,
        text=INTERVIEW_COMPLETE_NUDGE,
    )
    assert sessions.get(chat_id=98765).phase == SessionPhase.COMPLETED


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

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=_ready_converter(),
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
        )

    client.download_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


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
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
    )
    process_update(
        update=_text_update(chat_id=2002, update_id=106),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
    )

    client.send_message.assert_called_once_with(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.current_question is None
    assert state.interview_history == []
    assert state.dossier is None

    # Also test reset from GENERATING_MEDIA or COMPLETED while another chat remains untouched
    dossier = BehavioralDossier(summary="Summary.", suggested_animal="Otter")
    sessions.start_interview(chat_id=111, portrait=b"p1", first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    sessions.set_hybrid_portrait(chat_id=111, hybrid_portrait=HYBRID_PORTRAIT_BYTES)
    sessions.set_script(chat_id=111, script=SAMPLE_SCRIPT)
    sessions.complete_documentary(chat_id=111)
    assert sessions.get(chat_id=111).phase == SessionPhase.COMPLETED
    sessions.start_interview(chat_id=222, portrait=b"other-portrait", first_question="Other Q1?")

    process_update(
        update=_text_update(chat_id=111, text="/restart", update_id=120),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
    )
    assert sessions.get(chat_id=111).phase == SessionPhase.AWAITING_PHOTO
    assert sessions.get(chat_id=111).dossier is None
    assert sessions.get(chat_id=111).hybrid_portrait is None
    assert sessions.get(chat_id=111).script is None
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
    converter = _ready_converter()

    next_offset = poll_once(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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
    converter = _ready_converter()

    next_offset = poll_once(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
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
    converter = _ready_converter()

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
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
    converter = _ready_converter()

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
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
    converter = _ready_converter()

    with caplog.at_level(logging.INFO):
        run_polling_loop(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=_ready_scripter(),
            narrator=_ready_narrator(),
            timeout=5,
            max_iterations=2,
        )

    info_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "Telegram polling loop started" in info_messages
    assert client.get_updates.call_args_list[0].kwargs == {"offset": None, "timeout": 5}
    assert client.get_updates.call_args_list[1].kwargs == {"offset": 11, "timeout": 5}


@pytest.mark.parametrize("caption_cmd", ["/start", "/restart", "  /restart@NatureDocBot  "])
def test_photo_with_reset_caption_purges_ephemeral_files_and_resets_session(
    tmp_path: Path,
    caption_cmd: str,
) -> None:
    """A photo carrying /start or /restart in caption purges disk files and resets state."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    sessions = SessionStore()
    ephemeral_file = tmp_path / "temp_voice.mp3"
    ephemeral_file.write_bytes(b"ID3-voice")
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.register_ephemeral_file(chat_id=111, path=ephemeral_file)

    process_update(
        update=_photo_update(chat_id=111, caption=caption_cmd),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=_ready_converter(),
        scripter=_ready_scripter(),
        narrator=_ready_narrator(),
    )

    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    client.send_message.assert_called_once_with(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    assert not ephemeral_file.exists()
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    assert state.ephemeral_files == []


def test_narrator_voice_synthesis_registers_and_cleans_up_ephemeral_file_in_session_store() -> None:
    """_synthesize_and_send_voice registers the temp audio path with SessionStore and cleans up."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    interviewer = MagicMock(spec=Interviewer)
    converter = _ready_converter()
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    dossier = BehavioralDossier(summary="Dossier.", suggested_animal="Red Panda")
    sessions = SessionStore()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    sessions.set_hybrid_portrait(chat_id=111, hybrid_portrait=HYBRID_PORTRAIT_BYTES)
    sessions.set_script(chat_id=111, script=SAMPLE_SCRIPT)

    with (
        patch.object(
            sessions,
            "register_ephemeral_file",
            wraps=sessions.register_ephemeral_file,
        ) as register_spy,
        patch.object(
            sessions,
            "cleanup_ephemeral_files",
            wraps=sessions.cleanup_ephemeral_files,
        ) as cleanup_spy,
    ):
        process_update(
            update=_text_update(chat_id=111, text="Retry my voiceover!"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    assert register_spy.call_count == 1
    assert register_spy.call_args.args[0] == 111
    assert isinstance(register_spy.call_args.args[1], Path)
    assert cleanup_spy.call_count >= 1
    assert sessions.get(chat_id=111).ephemeral_files == []
    assert sessions.get(chat_id=111).phase == SessionPhase.COMPLETED


def test_fallback_send_message_failures_are_caught_and_logged_across_photo_interview_and_converter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fallback send_message errors in photo, interview, and converter paths log without raising."""
    # 1. Photo gate failure + fallback send failure
    client = MagicMock(spec=TelegramClient)
    client.get_file.side_effect = TelegramClientError("getFile boom")
    client.send_message.side_effect = TelegramClientError("fallback send boom")
    bouncer = _human_bouncer()
    interviewer = _ready_interviewer()
    converter = MagicMock(spec=Converter)
    converter.generate_hybrid_portrait.side_effect = ConverterError("converter boom")
    scripter = _ready_scripter()
    narrator = _ready_narrator()
    sessions = SessionStore()

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=111, update_id=201),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
    assert "event=photo_gate_fallback_failed stage=gateway chat_id=111" in caplog.text

    # 2. Question 1 start_interview failure + fallback send failure
    caplog.clear()
    client = _client_with_portrait()
    client.send_message.side_effect = TelegramClientError("q1 fallback boom")
    interviewer_q1_fail = MagicMock(spec=Interviewer)
    interviewer_q1_fail.start_interview.side_effect = InterviewerError("q1 boom")
    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=111, update_id=202),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer_q1_fail,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
    assert "event=interview_start_fallback_failed stage=gateway chat_id=111" in caplog.text

    # 3. Mid-interview turn failure + fallback send failure
    caplog.clear()
    sessions.start_interview(chat_id=111, portrait=PORTRAIT_BYTES, first_question="Q1?")
    interviewer_turn_fail = MagicMock(spec=Interviewer)
    interviewer_turn_fail.advance_interview.side_effect = InterviewerError("turn boom")
    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="A1", update_id=203),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer_turn_fail,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
    assert "event=interview_turn_fallback_failed stage=gateway chat_id=111" in caplog.text

    # 4. Converter failure + fallback send failure
    caplog.clear()
    dossier = BehavioralDossier(summary="Dossier.", suggested_animal="Red Panda")
    sessions.complete_interview(chat_id=111, answer="A1", dossier=dossier)
    with caplog.at_level(logging.ERROR):
        process_update(
            update=_text_update(chat_id=111, text="Retry portrait", update_id=204),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
    assert "event=converter_fallback_failed stage=gateway chat_id=111" in caplog.text


def test_two_consecutive_full_documentary_runs_in_same_process_succeed_cleanly() -> None:
    """Two full end-to-end documentary runs for the same chat_id succeed back-to-back."""
    chat_id = 777
    sessions = SessionStore()
    bouncer = _human_bouncer()
    created_paths: list[Path] = []
    narrator = _ready_narrator(created_paths=created_paths)

    portrait_run_1 = b"\xff\xd8portrait-run-1"
    portrait_run_2 = b"\xff\xd8portrait-run-2"
    hybrid_run_1 = b"\x89PNG-hybrid-1"
    hybrid_run_2 = b"\x89PNG-hybrid-2"
    script_run_1 = "Run one narration observing the nocturnal Eurasian Badger in the kitchen."
    script_run_2 = "Run two narration observing the energetic Red Panda on the balcony."
    dossier_run_1 = BehavioralDossier(
        summary="Run 1 nocturnal kitchen badger habits.",
        suggested_animal="Eurasian Badger",
    )
    dossier_run_2 = BehavioralDossier(
        summary="Run 2 sunny balcony red panda habits.",
        suggested_animal="Red Panda",
    )

    client = MagicMock(spec=TelegramClient)
    client.get_file.return_value = TelegramFile(
        file_id="large",
        file_unique_id="u-large",
        file_path="photos/large.jpg",
    )
    client.download_file.side_effect = [portrait_run_1, portrait_run_2]

    interviewer = MagicMock(spec=Interviewer)
    interviewer.start_interview.side_effect = ["Run1 Q1?", "Run2 Q1?"]
    interviewer.advance_interview.side_effect = [
        InterviewerTurnResult(is_complete=False, next_question="Run1 Q2?", dossier=None),
        InterviewerTurnResult(is_complete=False, next_question="Run1 Q3?", dossier=None),
        InterviewerTurnResult(is_complete=False, next_question="Run1 Q4?", dossier=None),
        InterviewerTurnResult(is_complete=False, next_question="Run1 Q5?", dossier=None),
        InterviewerTurnResult(is_complete=True, next_question="", dossier=dossier_run_1),
        InterviewerTurnResult(is_complete=False, next_question="Run2 Q2?", dossier=None),
        InterviewerTurnResult(is_complete=False, next_question="Run2 Q3?", dossier=None),
        InterviewerTurnResult(is_complete=False, next_question="Run2 Q4?", dossier=None),
        InterviewerTurnResult(is_complete=False, next_question="Run2 Q5?", dossier=None),
        InterviewerTurnResult(is_complete=True, next_question="", dossier=dossier_run_2),
    ]

    converter = MagicMock(spec=Converter)
    converter.generate_hybrid_portrait.side_effect = [hybrid_run_1, hybrid_run_2]

    scripter = MagicMock(spec=Scripter)
    scripter.generate_script.side_effect = [script_run_1, script_run_2]

    # --- Run 1 ---
    process_update(
        update=_photo_update(chat_id=chat_id, update_id=1),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    assert sessions.get(chat_id).phase == SessionPhase.INTERVIEWING
    for turn in range(1, 6):
        process_update(
            update=_text_update(chat_id=chat_id, text=f"Run1 Answer {turn}", update_id=1 + turn),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    state_after_run_1 = sessions.get(chat_id)
    assert state_after_run_1.phase == SessionPhase.COMPLETED
    assert state_after_run_1.dossier == dossier_run_1
    assert state_after_run_1.hybrid_portrait == hybrid_run_1
    assert state_after_run_1.script == script_run_1
    assert state_after_run_1.ephemeral_files == []

    # --- /restart between runs ---
    process_update(
        update=_text_update(chat_id=chat_id, text="/restart", update_id=10),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    state_after_restart = sessions.get(chat_id)
    assert state_after_restart.phase == SessionPhase.AWAITING_PHOTO
    assert state_after_restart.portrait is None
    assert state_after_restart.current_question is None
    assert state_after_restart.interview_history == []
    assert state_after_restart.dossier is None
    assert state_after_restart.hybrid_portrait is None
    assert state_after_restart.script is None
    assert state_after_restart.ephemeral_files == []

    # --- Run 2 ---
    process_update(
        update=_photo_update(chat_id=chat_id, update_id=11),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
        converter=converter,
        scripter=scripter,
        narrator=narrator,
    )
    assert sessions.get(chat_id).phase == SessionPhase.INTERVIEWING
    assert sessions.get(chat_id).portrait == portrait_run_2
    assert sessions.get(chat_id).interview_history == []

    for turn in range(1, 6):
        process_update(
            update=_text_update(chat_id=chat_id, text=f"Run2 Answer {turn}", update_id=11 + turn),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )

    state_after_run_2 = sessions.get(chat_id)
    assert state_after_run_2.phase == SessionPhase.COMPLETED
    assert state_after_run_2.dossier == dossier_run_2
    assert state_after_run_2.hybrid_portrait == hybrid_run_2
    assert state_after_run_2.script == script_run_2
    assert len(state_after_run_2.interview_history) == 5
    assert all(
        turn.question.startswith("Run2") and turn.answer.startswith("Run2")
        for turn in state_after_run_2.interview_history
    )
    assert len(created_paths) == 2
    assert all(not p.exists() for p in created_paths)
