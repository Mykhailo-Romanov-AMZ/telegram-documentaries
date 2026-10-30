"""Telegram Bot API HTTP client for long polling and messaging."""

import requests

from telegram_documentaries.logging_utils import log_stage
from telegram_documentaries.models import (
    GetUpdatesResponse,
    SendMessagePayload,
    SendMessageResponse,
    TelegramMessage,
    TelegramUpdate,
)

DEFAULT_BASE_URL = "https://api.telegram.org"
SEND_MESSAGE_TIMEOUT_SECONDS = 15


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
        self._api_base = f"{base_url.rstrip('/')}/bot{bot_token}"

    @log_stage("telegram_api")
    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[TelegramUpdate]:
        """Fetch updates from Telegram using long polling and validate with Pydantic."""
        params: dict[str, int] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        response = self._session.get(
            f"{self._api_base}/getUpdates",
            params=params,
            timeout=timeout + 10,
        )
        response.raise_for_status()
        parsed = GetUpdatesResponse.model_validate(response.json())
        return parsed.result

    @log_stage("telegram_api")
    def send_message(self, chat_id: int, text: str) -> TelegramMessage:
        """Validate and send a text message to a Telegram chat."""
        payload = SendMessagePayload(chat_id=chat_id, text=text)
        response = self._session.post(
            f"{self._api_base}/sendMessage",
            json=payload.model_dump(),
            timeout=SEND_MESSAGE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        parsed = SendMessageResponse.model_validate(response.json())
        return parsed.result
