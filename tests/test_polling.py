"""Tests for the Telegram long-polling loop and update processing."""

import logging
from unittest.mock import MagicMock

import pytest
import requests

from telegram_documentaries.models import TelegramChat, TelegramMessage, TelegramUpdate
from telegram_documentaries.poller import (
    HARDCODED_REPLY,
    poll_once,
    process_update,
    run_polling_loop,
)
from telegram_documentaries.telegram_client import TelegramClient


def test_process_update_replies_hey_mate_to_incoming_message() -> None:
    """process_update sends 'hey mate!' to the chat ID of a valid incoming message."""
    client = MagicMock(spec=TelegramClient)
    update = TelegramUpdate(
        update_id=101,
        message=TelegramMessage(
            message_id=1,
            chat=TelegramChat(id=98765),
            text="Hello there!",
        ),
    )

    process_update(update=update, client=client)

    assert HARDCODED_REPLY == "hey mate!"
    client.send_message.assert_called_once_with(chat_id=98765, text="hey mate!")


def test_process_update_ignores_update_without_message() -> None:
    """process_update safely ignores updates where update.message is None."""
    client = MagicMock(spec=TelegramClient)
    update = TelegramUpdate(update_id=102, message=None)

    process_update(update=update, client=client)

    client.send_message.assert_not_called()


def test_poll_once_fetches_updates_replies_and_advances_offset() -> None:
    """poll_once processes all returned updates and returns max_update_id + 1."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.return_value = [
        TelegramUpdate(
            update_id=200,
            message=TelegramMessage(
                message_id=10,
                chat=TelegramChat(id=111),
                text="First message",
            ),
        ),
        TelegramUpdate(
            update_id=201,
            message=TelegramMessage(
                message_id=11,
                chat=TelegramChat(id=222),
                text=None,  # Non-text message (e.g., photo) still receives 'hey mate!' in Phase 1
            ),
        ),
    ]

    next_offset = poll_once(client=client, offset=200, timeout=30)

    assert next_offset == 202
    client.get_updates.assert_called_once_with(offset=200, timeout=30)
    assert client.send_message.call_count == 2
    client.send_message.assert_any_call(chat_id=111, text="hey mate!")
    client.send_message.assert_any_call(chat_id=222, text="hey mate!")


def test_poll_once_preserves_offset_when_no_updates_returned() -> None:
    """poll_once returns the existing offset unchanged when getUpdates returns an empty list."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.return_value = []

    next_offset = poll_once(client=client, offset=55, timeout=30)

    assert next_offset == 55
    client.send_message.assert_not_called()


def test_poll_once_handles_get_updates_error_gracefully_and_preserves_offset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """poll_once logs and catches network errors from get_updates without crashing."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.side_effect = requests.ConnectionError("Telegram unreachable")

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(client=client, offset=42, timeout=30)

    assert next_offset == 42
    error_messages = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    assert "Telegram unreachable" in error_messages or "stage=polling" in error_messages


def test_poll_once_handles_send_message_error_gracefully_and_advances_offset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """poll_once logs per-update errors and still advances offset so later updates proceed."""
    client = MagicMock(spec=TelegramClient)
    client.get_updates.return_value = [
        TelegramUpdate(
            update_id=300,
            message=TelegramMessage(
                message_id=20,
                chat=TelegramChat(id=333),
                text="Trigger send error",
            ),
        ),
        TelegramUpdate(
            update_id=301,
            message=TelegramMessage(
                message_id=21,
                chat=TelegramChat(id=444),
                text="Second update in batch",
            ),
        ),
    ]
    client.send_message.side_effect = [
        requests.Timeout("sendMessage timed out"),
        MagicMock(spec=TelegramMessage),
    ]

    with caplog.at_level(logging.ERROR):
        next_offset = poll_once(client=client, offset=300, timeout=30)

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
        [
            TelegramUpdate(
                update_id=10,
                message=TelegramMessage(
                    message_id=1,
                    chat=TelegramChat(id=555),
                    text="hi",
                ),
            )
        ],
        [],
    ]

    with caplog.at_level(logging.INFO):
        run_polling_loop(client=client, timeout=5, max_iterations=2)

    info_messages = " ".join(r.getMessage() for r in caplog.records)
    assert "Telegram polling loop started" in info_messages
    assert client.get_updates.call_args_list[0].kwargs == {"offset": None, "timeout": 5}
    assert client.get_updates.call_args_list[1].kwargs == {"offset": 11, "timeout": 5}
