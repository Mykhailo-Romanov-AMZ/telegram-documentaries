"""Pydantic v2 boundary schemas for Telegram Bot API and Gemini agent payloads."""

import re

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
    caption: str | None = None
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


class InterviewTurn(BaseModel):
    """A single recorded question-and-answer pair from the behavioral interview."""

    question: str
    answer: str

    @field_validator("question", "answer")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("InterviewTurn question and answer must not be empty.")
        return cleaned


class BehavioralDossier(BaseModel):
    """Compiled behavioral profile and spirit animal chosen after the interview."""

    summary: str = Field(
        description=(
            "Cohesive descriptive paragraph summarizing the subject's daily habits, "
            "behavioral quirks, preferred habitats, and rituals observed during the interview."
        )
    )
    suggested_animal: str = Field(
        description=(
            "Specific wildlife species (e.g. 'Eurasian Badger', 'Red Panda', 'Barn Owl') "
            "that best matches the subject's behavioral dossier."
        )
    )

    @field_validator("summary", "suggested_animal")
    @classmethod
    def validate_non_empty_field(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("BehavioralDossier fields must not be empty.")
        return cleaned


class InterviewerTurnResult(BaseModel):
    """Structured output returned by the Interviewer agent on each turn."""

    is_complete: StrictBool = Field(
        description=(
            "True ONLY when the interview has gathered at least 5 answers (and always True "
            "by the 7th answer) and a complete BehavioralDossier is provided; False when "
            "asking the next single interview question."
        )
    )
    next_question: str = Field(
        default="",
        description=(
            "The single next playful wildlife-researcher interview question to ask the subject "
            "when is_complete is False (empty string when is_complete is True)."
        ),
    )
    dossier: BehavioralDossier | None = Field(
        default=None,
        description=(
            "The compiled BehavioralDossier and suggested spirit animal when is_complete is "
            "True (null when is_complete is False)."
        ),
    )


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


class GeneratedImage(BaseModel):
    """Validated image bytes and MIME type returned by the Converter model."""

    image_bytes: bytes
    mime_type: str = "image/png"

    @field_validator("image_bytes")
    @classmethod
    def validate_non_empty_bytes(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("GeneratedImage image_bytes must not be empty.")
        return value

    @field_validator("mime_type")
    @classmethod
    def validate_image_mime_type(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned.startswith("image/") or len(cleaned) <= len("image/"):
            raise ValueError("GeneratedImage mime_type must be a non-empty 'image/*' MIME type.")
        return cleaned


class SendPhotoPayload(BaseModel):
    """Validated outgoing payload for Telegram's sendPhoto endpoint."""

    chat_id: StrictInt
    photo_bytes: bytes
    caption: str | None = None

    @field_validator("photo_bytes")
    @classmethod
    def validate_non_empty_photo_bytes(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("Photo bytes must not be empty.")
        return value

    @field_validator("caption")
    @classmethod
    def validate_optional_caption(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Photo caption must not be empty or whitespace-only when provided.")
        return cleaned


class SendPhotoResponse(BaseModel):
    """Envelope returned by Telegram's sendPhoto endpoint."""

    ok: StrictBool
    result: TelegramMessage


_MARKDOWN_EMPHASIS_MARKERS = str.maketrans("", "", "*_`")
_MARKDOWN_HEADING_PREFIX = re.compile(r"(?m)^\s*#{1,6}\s*")


class DocumentaryScript(BaseModel):
    """Structured output returned by the Scripter agent for documentary voiceover narration."""

    narration: str = Field(
        description=(
            "A single dramatic, comedic British nature documentary voiceover paragraph "
            "(roughly 60 to 90 words) observing the subject as their spirit animal in the wild. "
            "Plain spoken prose only: no line breaks, markdown formatting, or stage directions, "
            "so it is ready for direct voice synthesis."
        )
    )

    @field_validator("narration")
    @classmethod
    def validate_single_paragraph_narration(cls, value: str) -> str:
        """Normalise narration into plain single-paragraph prose for voice synthesis."""
        cleaned = value.translate(_MARKDOWN_EMPHASIS_MARKERS)
        cleaned = _MARKDOWN_HEADING_PREFIX.sub("", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            raise ValueError("DocumentaryScript narration must not be empty.")
        return cleaned


class GeneratedAudio(BaseModel):
    """Validated audio bytes and MIME type returned by the Narrator TTS model."""

    audio_bytes: bytes
    mime_type: str = "audio/mpeg"

    @field_validator("audio_bytes")
    @classmethod
    def validate_non_empty_bytes(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("GeneratedAudio audio_bytes must not be empty.")
        return value

    @field_validator("mime_type")
    @classmethod
    def validate_audio_mime_type(cls, value: str) -> str:
        cleaned = value.strip().lower()
        base_mime = cleaned.partition(";")[0].strip()
        if not base_mime.startswith("audio/") or len(base_mime) <= len("audio/"):
            raise ValueError("GeneratedAudio mime_type must be a non-empty 'audio/*' MIME type.")
        return cleaned


class SendVoicePayload(BaseModel):
    """Validated outgoing payload for Telegram's sendVoice endpoint."""

    chat_id: StrictInt
    audio_bytes: bytes
    caption: str | None = None

    @field_validator("audio_bytes")
    @classmethod
    def validate_non_empty_audio_bytes(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("Voice audio_bytes must not be empty.")
        return value

    @field_validator("caption")
    @classmethod
    def validate_optional_caption(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Voice caption must not be empty or whitespace-only when provided.")
        return cleaned


class SendVoiceResponse(BaseModel):
    """Envelope returned by Telegram's sendVoice endpoint."""

    ok: StrictBool
    result: TelegramMessage
