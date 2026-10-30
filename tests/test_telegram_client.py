"""Tests for Telegram Pydantic boundary schemas and TelegramClient."""

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from pydantic import ValidationError

from telegram_documentaries.models import (
    GetUpdatesResponse,
    SendMessagePayload,
    TelegramChat,
    TelegramMessage,
    TelegramUpdate,
)
from telegram_documentaries.telegram_client import TelegramClient


def test_parse_valid_get_updates_response_with_text_and_non_text_messages() -> None:
    """GetUpdatesResponse parses text messages, non-text messages, and messageless updates."""
    raw_payload: dict[str, Any] = {
        "ok": True,
        "result": [
            {
                "update_id": 1001,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 123456, "type": "private"},
                    "text": "Hello documentary bot",
                },
            },
            {
                "update_id": 1002,
                "message": {
                    "message_id": 2,
                    "chat": {"id": 123456},
                    # Photo message without text field
                },
            },
            {
                "update_id": 1003,
                # Edited post or callback query without message field
            },
        ],
    }

    parsed = GetUpdatesResponse.model_validate(raw_payload)

    assert parsed.ok is True
    assert len(parsed.result) == 3

    first = parsed.result[0]
    assert isinstance(first, TelegramUpdate)
    assert first.update_id == 1001
    assert isinstance(first.message, TelegramMessage)
    assert isinstance(first.message.chat, TelegramChat)
    assert first.message.chat.id == 123456
    assert first.message.text == "Hello documentary bot"

    second = parsed.result[1]
    assert second.update_id == 1002
    assert second.message is not None
    assert second.message.text is None

    third = parsed.result[2]
    assert third.update_id == 1003
    assert third.message is None


@pytest.mark.parametrize(
    "malformed_payload",
    [
        {"ok": True, "result": [{"message": {"message_id": 1, "chat": {"id": 123}}}]},
        {
            "ok": True,
            "result": [
                {
                    "update_id": 1001,
                    "message": {"message_id": 1, "chat": {"id": "not-an-int"}},
                }
            ],
        },
        {
            "ok": True,
            "result": [
                {
                    "update_id": 1001,
                    "message": {"message_id": 1, "chat": {"id": "12345"}},
                }
            ],
        },
    ],
)
def test_reject_malformed_update_payloads(malformed_payload: dict[str, Any]) -> None:
    """GetUpdatesResponse raises ValidationError on missing update_id or non-int chat.id."""
    with pytest.raises(ValidationError):
        GetUpdatesResponse.model_validate(malformed_payload)


def test_send_message_payload_rejects_empty_text_or_non_int_chat_id() -> None:
    """SendMessagePayload enforces integer chat_id and non-empty text."""
    with pytest.raises(ValidationError):
        SendMessagePayload(chat_id=123, text="   ")

    with pytest.raises(ValidationError):
        SendMessagePayload.model_validate({"chat_id": "123", "text": "hey mate!"})


def test_telegram_client_get_updates_passes_offset_and_returns_validated_models() -> None:
    """TelegramClient.get_updates sends offset and timeout and validates the JSON response."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.json.return_value = {
        "ok": True,
        "result": [
            {
                "update_id": 500,
                "message": {
                    "message_id": 10,
                    "chat": {"id": 777},
                    "text": "/start",
                },
            }
        ],
    }
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)
    updates = client.get_updates(offset=500, timeout=25)

    assert len(updates) == 1
    assert updates[0].update_id == 500
    assert updates[0].message is not None
    assert updates[0].message.chat.id == 777
    assert updates[0].message.text == "/start"

    mock_session.get.assert_called_once_with(
        "https://api.telegram.org/bottest-token-123/getUpdates",
        params={"timeout": 25, "offset": 500},
        timeout=35,
    )
    mock_response.raise_for_status.assert_called_once()


def test_telegram_client_send_message_validates_and_posts_payload() -> None:
    """TelegramClient.send_message validates SendMessagePayload and posts to sendMessage."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.json.return_value = {
        "ok": True,
        "result": {
            "message_id": 11,
            "chat": {"id": 777},
            "text": "hey mate!",
        },
    }
    mock_session.post.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)
    sent_message = client.send_message(chat_id=777, text="hey mate!")

    assert isinstance(sent_message, TelegramMessage)
    assert sent_message.chat.id == 777
    assert sent_message.text == "hey mate!"

    mock_session.post.assert_called_once_with(
        "https://api.telegram.org/bottest-token-123/sendMessage",
        json={"chat_id": 777, "text": "hey mate!"},
        timeout=15,
    )
    mock_response.raise_for_status.assert_called_once()
