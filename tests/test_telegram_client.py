"""Tests for Telegram Pydantic boundary schemas and TelegramClient."""

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests
from pydantic import ValidationError

from telegram_documentaries.models import (
    GetUpdatesResponse,
    SendMessagePayload,
    TelegramChat,
    TelegramFile,
    TelegramMessage,
    TelegramUpdate,
)
from telegram_documentaries.telegram_client import TelegramClient, TelegramClientError


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


def test_telegram_client_get_file_calls_get_file_and_returns_validated_model() -> None:
    """TelegramClient.get_file requests getFile metadata and returns a TelegramFile."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.json.return_value = {
        "ok": True,
        "result": {
            "file_id": "photo-file-id",
            "file_unique_id": "unique-photo-id",
            "file_size": 4096,
            "file_path": "photos/file_2.jpg",
        },
    }
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)
    telegram_file = client.get_file(file_id="photo-file-id")

    assert isinstance(telegram_file, TelegramFile)
    assert telegram_file.file_id == "photo-file-id"
    assert telegram_file.file_path == "photos/file_2.jpg"
    assert telegram_file.file_size == 4096

    mock_session.get.assert_called_once_with(
        "https://api.telegram.org/bottest-token-123/getFile",
        params={"file_id": "photo-file-id"},
        timeout=15,
    )
    mock_response.raise_for_status.assert_called_once()


def test_telegram_client_get_file_raises_on_non_200_response() -> None:
    """get_file never silently returns None when Telegram responds with an HTTP error."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.raise_for_status.side_effect = requests.HTTPError("400 Bad Request")
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)

    with pytest.raises(TelegramClientError) as exc_info:
        client.get_file(file_id="photo-file-id")

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_telegram_client_download_file_returns_raw_bytes() -> None:
    """download_file GETs the Telegram file endpoint and returns the raw body bytes."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.content = b"\xff\xd8\xff\xe0raw-jpeg-bytes"
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)
    downloaded = client.download_file(file_path="photos/file_2.jpg")

    assert downloaded == b"\xff\xd8\xff\xe0raw-jpeg-bytes"
    mock_session.get.assert_called_once_with(
        "https://api.telegram.org/file/bottest-token-123/photos/file_2.jpg",
        timeout=30,
    )
    mock_response.raise_for_status.assert_called_once()


def test_telegram_client_download_file_raises_on_empty_body() -> None:
    """download_file rejects an empty response body instead of returning empty bytes."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.content = b""
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)

    with pytest.raises(TelegramClientError):
        client.download_file(file_path="photos/file_2.jpg")


def test_telegram_client_download_file_raises_on_non_200_response() -> None:
    """download_file raises a typed error when Telegram responds with an HTTP error."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)

    with pytest.raises(TelegramClientError) as exc_info:
        client.download_file(file_path="photos/missing.jpg")

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_telegram_client_download_file_normalises_leading_slash_in_path() -> None:
    """download_file builds the same file URL when file_path starts with a slash."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.content = b"raw-bytes"
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)
    downloaded = client.download_file(file_path="/photos/file_2.jpg")

    assert downloaded == b"raw-bytes"
    mock_session.get.assert_called_once_with(
        "https://api.telegram.org/file/bottest-token-123/photos/file_2.jpg",
        timeout=30,
    )


def test_telegram_client_get_file_wraps_malformed_envelope() -> None:
    """get_file wraps a schema-invalid getFile envelope instead of leaking ValidationError."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.json.return_value = {"ok": True, "result": {"file_id": "missing-unique-id"}}
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)

    with pytest.raises(TelegramClientError) as exc_info:
        client.get_file(file_id="photo-file-id")

    assert exc_info.value.__cause__ is None
    assert "invalid response" in str(exc_info.value)


def test_telegram_client_reports_json_decode_error_as_invalid_response() -> None:
    """Malformed JSON is reported as an invalid response rather than a failed HTTP request."""
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "", 0)
    mock_session.get.return_value = mock_response

    client = TelegramClient(bot_token="test-token-123", session=mock_session)

    with pytest.raises(TelegramClientError) as exc_info:
        client.get_updates()

    assert exc_info.value.__cause__ is None
    assert "invalid response" in str(exc_info.value)


@pytest.mark.parametrize(
    ("method_name", "http_method", "endpoint"),
    [
        ("get_updates", "get", "getUpdates"),
        ("send_message", "post", "sendMessage"),
        ("get_file", "get", "getFile"),
        ("download_file", "get", "download_file"),
    ],
)
def test_telegram_client_errors_never_leak_bot_token(
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    http_method: str,
    endpoint: str,
) -> None:
    """Every Telegram request failure raises a token-free typed error and logs no token."""
    token = "super-secret-bot-token-123"
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock(spec=requests.Response)
    mock_response.status_code = 401
    mock_response.raise_for_status.side_effect = requests.HTTPError(
        f"401 Client Error: Unauthorized for url: https://api.telegram.org/bot{token}/{endpoint}",
        response=mock_response,
    )
    getattr(mock_session, http_method).return_value = mock_response

    client = TelegramClient(bot_token=token, session=mock_session)

    with caplog.at_level(logging.ERROR), pytest.raises(TelegramClientError) as exc_info:
        if method_name == "get_updates":
            client.get_updates()
        elif method_name == "send_message":
            client.send_message(chat_id=123, text="hey mate!")
        elif method_name == "get_file":
            client.get_file(file_id="photo-file-id")
        else:
            client.download_file(file_path="photos/file_2.jpg")

    message = str(exc_info.value)
    assert token not in message
    assert token not in caplog.text
    assert endpoint in message
    assert "401" in message
    assert exc_info.value.__cause__ is None
