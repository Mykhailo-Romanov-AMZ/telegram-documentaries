"""Pydantic v2 boundary schemas for Telegram Bot API payloads."""

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator


class TelegramChat(BaseModel):
    """Represents a Telegram chat with a strictly integer chat ID."""

    id: StrictInt


class TelegramMessage(BaseModel):
    """Represents an incoming or outgoing Telegram message."""

    message_id: StrictInt
    chat: TelegramChat
    text: str | None = None


class TelegramUpdate(BaseModel):
    """Represents a single Telegram update returned by getUpdates."""

    update_id: StrictInt
    message: TelegramMessage | None = None


class GetUpdatesResponse(BaseModel):
    """Envelope returned by Telegram's getUpdates endpoint."""

    ok: StrictBool
    result: list[TelegramUpdate] = Field(default_factory=list)


class SendMessageResponse(BaseModel):
    """Envelope returned by Telegram's sendMessage endpoint."""

    ok: StrictBool
    result: TelegramMessage


class SendMessagePayload(BaseModel):
    """Validated outgoing payload for Telegram's sendMessage endpoint."""

    chat_id: StrictInt
    text: str

    @field_validator("text")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message text must not be empty.")
        return value
