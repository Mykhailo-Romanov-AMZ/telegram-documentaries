"""Telegram Bot API HTTP client for long polling and messaging."""

import requests
from pydantic import ValidationError

from telegram_documentaries.logging_utils import log_stage
from telegram_documentaries.models import (
    GetFileResponse,
    GetUpdatesResponse,
    SendMessagePayload,
    SendMessageResponse,
    SendPhotoPayload,
    SendPhotoResponse,
    TelegramFile,
    TelegramMessage,
    TelegramUpdate,
)

DEFAULT_BASE_URL = "https://api.telegram.org"
SEND_MESSAGE_TIMEOUT_SECONDS = 15
SEND_PHOTO_TIMEOUT_SECONDS = 30
GET_FILE_TIMEOUT_SECONDS = 15
DOWNLOAD_FILE_TIMEOUT_SECONDS = 30


class TelegramClientError(Exception):
    """Raised when a Telegram Bot API operation fails."""


def _endpoint_error(endpoint: str, exc: requests.RequestException) -> TelegramClientError:
    """Build a token-free error describing a failed Telegram API request."""
    response = exc.response
    status_code = response.status_code if response is not None else "no response"
    return TelegramClientError(f"Telegram API request to {endpoint} failed (status={status_code}).")


class TelegramClient:
    """Thin, boundary-validated client for the Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        session: requests.Session | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._bot_token = bot_token
        self._session = session or requests.Session()
        base = base_url.rstrip("/")
        self._api_base = f"{base}/bot{bot_token}"
        self._file_base = f"{base}/file/bot{bot_token}"

    @log_stage("telegram_api")
    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[TelegramUpdate]:
        """Fetch updates from Telegram using long polling and validate with Pydantic."""
        endpoint = "getUpdates"
        params: dict[str, int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        try:
            response = self._session.get(
                f"{self._api_base}/{endpoint}",
                params=params,
                timeout=timeout + 10,
            )
            response.raise_for_status()
            parsed = GetUpdatesResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            raise TelegramClientError(
                f"Telegram API {endpoint} returned an invalid response."
            ) from None
        except requests.RequestException as exc:
            raise _endpoint_error(endpoint, exc) from None
        return parsed.result

    @log_stage("telegram_api")
    def send_message(self, chat_id: int, text: str) -> TelegramMessage:
        """Validate and send a text message to a Telegram chat."""
        endpoint = "sendMessage"
        payload = SendMessagePayload(chat_id=chat_id, text=text)
        try:
            response = self._session.post(
                f"{self._api_base}/{endpoint}",
                json=payload.model_dump(),
                timeout=SEND_MESSAGE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            parsed = SendMessageResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            raise TelegramClientError(
                f"Telegram API {endpoint} returned an invalid response."
            ) from None
        except requests.RequestException as exc:
            raise _endpoint_error(endpoint, exc) from None
        return parsed.result

    @log_stage("telegram_api")
    def send_photo(
        self,
        chat_id: int,
        photo_bytes: bytes,
        caption: str | None = None,
    ) -> TelegramMessage:
        """Validate and upload a photo to a Telegram chat via sendPhoto."""
        endpoint = "sendPhoto"
        payload = SendPhotoPayload(
            chat_id=chat_id,
            photo_bytes=photo_bytes,
            caption=caption,
        )
        form_data: dict[str, int | str] = {"chat_id": payload.chat_id}
        if payload.caption is not None:
            form_data["caption"] = payload.caption

        try:
            response = self._session.post(
                f"{self._api_base}/{endpoint}",
                data=form_data,
                files={"photo": ("hybrid_portrait.png", payload.photo_bytes, "image/png")},
                timeout=SEND_PHOTO_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            parsed = SendPhotoResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            raise TelegramClientError(
                f"Telegram API {endpoint} returned an invalid response."
            ) from None
        except requests.RequestException as exc:
            raise _endpoint_error(endpoint, exc) from None
        return parsed.result

    @log_stage("telegram_api")
    def get_file(self, file_id: str) -> TelegramFile:
        """Fetch metadata (including file_path) for a Telegram-hosted file."""
        endpoint = "getFile"
        try:
            response = self._session.get(
                f"{self._api_base}/{endpoint}",
                params={"file_id": file_id},
                timeout=GET_FILE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            parsed = GetFileResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            raise TelegramClientError(
                f"Telegram API {endpoint} returned an invalid response."
            ) from None
        except requests.RequestException as exc:
            raise _endpoint_error(endpoint, exc) from None
        return parsed.result

    @log_stage("telegram_api")
    def download_file(self, file_path: str) -> bytes:
        """Download raw bytes for a Telegram file path returned by getFile."""
        endpoint = "download_file"
        try:
            response = self._session.get(
                f"{self._file_base}/{file_path.lstrip('/')}",
                timeout=DOWNLOAD_FILE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            content = response.content
        except requests.RequestException as exc:
            raise _endpoint_error(endpoint, exc) from None

        if not content:
            raise TelegramClientError(f"Telegram API {endpoint} returned an empty body.")
        return content
