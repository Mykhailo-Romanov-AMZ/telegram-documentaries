"""Tests for the Telegram long-polling loop and Bouncer-aware update processing."""

import logging
from unittest.mock import MagicMock

import pytest
import requests

from telegram_documentaries.bouncer import DEFAULT_REJECTION_MESSAGE, Bouncer, BouncerError
from telegram_documentaries.models import (
    BouncerVerdict,
    TelegramChat,
    TelegramFile,
    TelegramMessage,
    TelegramPhotoSize,
    TelegramUpdate,
)
from telegram_documentaries.poller import (
    AWAITING_PHOTO_NUDGE,
    ERROR_FALLBACK_MESSAGE,
    HARDCODED_REPLY,
    poll_once,
    process_update,
    run_polling_loop,
)
from telegram_documentaries.session import SessionPhase, SessionStore
from telegram_documentaries.telegram_client import TelegramClient, TelegramClientError

PORTRAIT_BYTES = b"\xff\xd8large-portrait-bytes"


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


def _client_with_portrait() -> MagicMock:
    client = MagicMock(spec=TelegramClient)
    client.get_file.return_value = TelegramFile(
        file_id="large",
        file_unique_id="u-large",
        file_path="photos/large.jpg",
    )
    client.download_file.return_value = PORTRAIT_BYTES
    return client


def test_human_photo_downloads_largest_size_admits_session_and_replies() -> None:
    """A human photo picks the largest size, stores the portrait, and replies 'hey mate!'."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    sessions = SessionStore()

    process_update(
        update=_photo_update(chat_id=98765),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )

    client.get_file.assert_called_once_with("large")
    client.download_file.assert_called_once_with("photos/large.jpg")
    bouncer.evaluate.assert_called_once_with(PORTRAIT_BYTES)
    client.send_message.assert_called_once_with(chat_id=98765, text=HARDCODED_REPLY)
    assert HARDCODED_REPLY == "hey mate!"

    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == PORTRAIT_BYTES


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
    sessions = SessionStore()

    with caplog.at_level(logging.WARNING):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
        )

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
    sessions = SessionStore()

    with caplog.at_level(logging.WARNING):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
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
    sessions = SessionStore()

    process_update(
        update=_text_update(chat_id=111, text="hello there"),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )

    client.send_message.assert_called_once_with(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    client.get_file.assert_not_called()
    bouncer.evaluate.assert_not_called()
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_text_while_interviewing_is_left_for_later_phases(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Text during INTERVIEWING is not answered but is logged for later phases."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    sessions = SessionStore()
    sessions.set_portrait(chat_id=111, portrait=b"existing-portrait")

    with caplog.at_level(logging.DEBUG, logger="telegram_documentaries"):
        process_update(
            update=_text_update(chat_id=111, text="my answer"),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
        )

    client.send_message.assert_not_called()
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == b"existing-portrait"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "chat_id=111" in messages
    assert "reason=interviewing" in messages


def test_update_without_message_is_ignored(caplog: pytest.LogCaptureFixture) -> None:
    """An update with no message sends nothing, changes no state, and is logged."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    sessions = SessionStore()

    with caplog.at_level(logging.DEBUG, logger="telegram_documentaries"):
        process_update(
            update=TelegramUpdate(update_id=102, message=None),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
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
    sessions = SessionStore()
    sessions.set_portrait(chat_id=98765, portrait=b"existing-portrait")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    bouncer.evaluate.assert_not_called()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == b"existing-portrait"


def test_bouncer_error_sends_fallback_logs_traceback_and_preserves_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Gemini/Bouncer failure sends the fallback and leaves the session untouched."""
    client = _client_with_portrait()
    bouncer = MagicMock(spec=Bouncer)
    bouncer.evaluate.side_effect = BouncerError("gemini exploded")
    sessions = SessionStore()

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
        )

    client.send_message.assert_called_once_with(chat_id=98765, text=ERROR_FALLBACK_MESSAGE)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(record.exc_info is not None for record in error_records)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


def test_human_photo_during_interviewing_replaces_portrait_and_replies() -> None:
    """A new human photo mid-interview replaces the stored portrait without crashing."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    sessions = SessionStore()
    sessions.set_portrait(chat_id=98765, portrait=b"old-portrait")

    process_update(
        update=_photo_update(chat_id=98765, update_id=104),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )

    client.send_message.assert_called_once_with(chat_id=98765, text=HARDCODED_REPLY)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.INTERVIEWING
    assert state.portrait == PORTRAIT_BYTES


def test_human_photo_logs_bouncer_stage_with_chat_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Gateway photo processing emits a stage=bouncer log carrying the chat_id context."""
    client = _client_with_portrait()
    bouncer = _human_bouncer()
    sessions = SessionStore()

    with caplog.at_level(logging.INFO):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
        )

    bouncer_records = [r for r in caplog.records if "stage=bouncer" in r.getMessage()]
    assert any("chat_id=98765" in record.getMessage() for record in bouncer_records)


def test_non_human_photo_during_interviewing_resets_session() -> None:
    """A rejected replacement photo mid-interview clears the previously stored portrait."""
    client = _client_with_portrait()
    bouncer = MagicMock(spec=Bouncer)
    cheeky = "That's a cat, mate. Impressive whiskers — but I need a human."
    bouncer.evaluate.return_value = BouncerVerdict(
        is_human=False,
        subject="a cat",
        rejection_message=cheeky,
    )
    sessions = SessionStore()
    sessions.set_portrait(chat_id=98765, portrait=b"old-portrait")

    process_update(
        update=_photo_update(chat_id=98765, update_id=107),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )

    client.send_message.assert_called_once_with(chat_id=98765, text=cheeky)
    state = sessions.get(chat_id=98765)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None


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
    sessions = SessionStore()
    sessions.set_portrait(chat_id=98765, portrait=b"existing-portrait")

    with caplog.at_level(logging.ERROR):
        process_update(
            update=_photo_update(chat_id=98765),
            client=client,
            sessions=sessions,
            bouncer=bouncer,
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
    sessions = SessionStore()

    process_update(
        update=_photo_update(chat_id=1001, update_id=105),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )
    process_update(
        update=_text_update(chat_id=2002, update_id=106),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
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
def test_start_or_restart_command_resets_session_from_interviewing_and_sends_nudge(
    command_text: str,
) -> None:
    """/start and /restart in INTERVIEWING reset the session to AWAITING_PHOTO and send nudge."""
    client = MagicMock(spec=TelegramClient)
    bouncer = MagicMock(spec=Bouncer)
    sessions = SessionStore()
    sessions.set_portrait(chat_id=111, portrait=b"existing-portrait")

    process_update(
        update=_text_update(chat_id=111, text=command_text),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )

    client.send_message.assert_called_once_with(chat_id=111, text=AWAITING_PHOTO_NUDGE)
    state = sessions.get(chat_id=111)
    assert state.phase == SessionPhase.AWAITING_PHOTO
    assert state.portrait is None
    # Another chat's session remains untouched
    sessions.set_portrait(chat_id=222, portrait=b"other-portrait")
    process_update(
        update=_text_update(chat_id=111, text="/restart", update_id=120),
        client=client,
        sessions=sessions,
        bouncer=bouncer,
    )
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

    next_offset = poll_once(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
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

    next_offset = poll_once(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
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

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
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

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
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

    with caplog.at_level(logging.INFO):
        run_polling_loop(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            timeout=5,
            max_iterations=2,
        )

    info_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "Telegram polling loop started" in info_messages
    assert client.get_updates.call_args_list[0].kwargs == {"offset": None, "timeout": 5}
    assert client.get_updates.call_args_list[1].kwargs == {"offset": 11, "timeout": 5}
