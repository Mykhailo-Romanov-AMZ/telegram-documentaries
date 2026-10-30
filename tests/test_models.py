"""Tests for the Phase 2 Pydantic boundary schemas."""

from typing import Any

import pytest
from pydantic import ValidationError

from telegram_documentaries.models import (
    BouncerVerdict,
    GetFileResponse,
    TelegramFile,
    TelegramPhotoSize,
    TelegramUpdate,
)


def test_text_only_update_still_parses_with_photo_defaulting_to_none() -> None:
    """A text-only update keeps parsing unchanged and leaves photo as None."""
    update = TelegramUpdate.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 555},
                "text": "hello",
            },
        }
    )

    assert update.message is not None
    assert update.message.text == "hello"
    assert update.message.photo is None


def test_update_with_photo_sizes_parses_typed_photo_models() -> None:
    """A photo update parses every size into a typed TelegramPhotoSize."""
    raw: dict[str, Any] = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "chat": {"id": 555},
            "photo": [
                {
                    "file_id": "small",
                    "file_unique_id": "u-small",
                    "width": 90,
                    "height": 90,
                    "file_size": 1000,
                },
                {
                    "file_id": "medium",
                    "file_unique_id": "u-medium",
                    "width": 320,
                    "height": 320,
                    "file_size": 5000,
                },
                {
                    "file_id": "large",
                    "file_unique_id": "u-large",
                    "width": 800,
                    "height": 800,
                },
            ],
        },
    }

    update = TelegramUpdate.model_validate(raw)

    assert update.message is not None
    photo = update.message.photo
    assert photo is not None
    assert len(photo) == 3
    assert all(isinstance(size, TelegramPhotoSize) for size in photo)
    assert photo[0].file_id == "small"
    assert photo[0].width == 90
    assert photo[0].height == 90
    assert photo[2].file_id == "large"
    assert photo[2].file_size is None


@pytest.mark.parametrize(
    "malformed_photo",
    [
        {
            "file_id": "x",
            "file_unique_id": "ux",
            "width": "800",
            "height": 800,
        },
        {
            "file_id": "x",
            "file_unique_id": "ux",
            "width": 800,
        },
        {
            "file_unique_id": "ux",
            "width": 800,
            "height": 800,
        },
    ],
)
def test_malformed_photo_entries_are_rejected(malformed_photo: dict[str, Any]) -> None:
    """Photo entries with wrong types or missing fields raise ValidationError."""
    raw = {
        "update_id": 3,
        "message": {
            "message_id": 12,
            "chat": {"id": 555},
            "photo": [malformed_photo],
        },
    }

    with pytest.raises(ValidationError):
        TelegramUpdate.model_validate(raw)


def test_get_file_response_parses_envelope_with_and_without_file_path() -> None:
    """GetFileResponse validates the getFile envelope including optional file_path."""
    with_path = GetFileResponse.model_validate(
        {
            "ok": True,
            "result": {
                "file_id": "abc",
                "file_unique_id": "uabc",
                "file_size": 2048,
                "file_path": "photos/file_1.jpg",
            },
        }
    )
    assert with_path.ok is True
    assert isinstance(with_path.result, TelegramFile)
    assert with_path.result.file_path == "photos/file_1.jpg"
    assert with_path.result.file_size == 2048

    without_path = GetFileResponse.model_validate(
        {
            "ok": True,
            "result": {
                "file_id": "abc",
                "file_unique_id": "uabc",
            },
        }
    )
    assert without_path.result.file_path is None
    assert without_path.result.file_size is None


def test_bouncer_verdict_parses_human_and_non_human() -> None:
    """BouncerVerdict parses both human and non-human verdicts."""
    human = BouncerVerdict.model_validate(
        {"is_human": True, "subject": "a person", "rejection_message": ""}
    )
    assert human.is_human is True
    assert human.subject == "a person"
    assert human.rejection_message == ""

    non_human = BouncerVerdict.model_validate(
        {"is_human": False, "subject": "a toaster", "rejection_message": "That's a toaster."}
    )
    assert non_human.is_human is False
    assert non_human.subject == "a toaster"
    assert non_human.rejection_message == "That's a toaster."


def test_bouncer_verdict_requires_is_human() -> None:
    """A verdict missing is_human is rejected rather than defaulted."""
    with pytest.raises(ValidationError):
        BouncerVerdict.model_validate({"subject": "a toaster"})
