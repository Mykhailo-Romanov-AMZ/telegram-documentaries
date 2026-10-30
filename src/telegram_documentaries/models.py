"""Pydantic v2 boundary schemas for Telegram Bot API payloads."""

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator


class TelegramChat(BaseModel):
    """Represents a Telegram chat with a strictly integer chat ID."""

    id: StrictInt


class TelegramPhotoSize(BaseModel):
    """Represents one resolution variant of a photo uploaded to Telegram."""

    file_id: str
    file_unique_id: str
    width: StrictInt
    height: StrictInt
    file_size: StrictInt | None = None


class TelegramMessage(BaseModel):
    """Represents an incoming or outgoing Telegram message."""

    message_id: StrictInt
    chat: TelegramChat
    text: str | None = None
    photo: list[TelegramPhotoSize] | None = None


class TelegramUpdate(BaseModel):
    """Represents a single Telegram update returned by getUpdates."""

    update_id: StrictInt
    message: TelegramMessage | None = None


class GetUpdatesResponse(BaseModel):
    """Envelope returned by Telegram's getUpdates endpoint."""

    ok: StrictBool
    result: list[TelegramUpdate] = Field(default_factory=list)


class TelegramFile(BaseModel):
    """Metadata describing a file stored on Telegram's servers."""

    file_id: str
    file_unique_id: str
    file_size: StrictInt | None = None
    file_path: str | None = None


class GetFileResponse(BaseModel):
    """Envelope returned by Telegram's getFile endpoint."""

    ok: StrictBool
    result: TelegramFile


class BouncerVerdict(BaseModel):
    """Structured verdict returned by the Bouncer vision gate."""

    subject: str = Field(
        description=(
            "Description of the single primary, central subject of the image "
            "(e.g. a parked car, a coffee mug, a cat, or a human portrait)."
        )
    )
    is_human: StrictBool = Field(
        description=(
            "True ONLY when the primary, central subject of the photo is a real human "
            "person (portrait/selfie/person); False if the primary subject is a car, "
            "vehicle, pet, animal, food, object, landscape, or illustration — even if a "
            "driver inside a car, a reflection, or background bystanders are visible."
        )
    )
    rejection_message: str = Field(
        default="",
        description=(
            "Short, cheeky, in-character rejection naming the non-human subject when "
            "is_human is False (empty when is_human is True)."
        ),
    )

    @field_validator("subject")
    @classmethod
    def validate_non_empty_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("BouncerVerdict subject must not be empty.")
        return value


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
