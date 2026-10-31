"""Tests for the Phase 2 Pydantic boundary schemas."""

from typing import Any

import pytest
from pydantic import ValidationError

from telegram_documentaries.models import (
    BehavioralDossier,
    BouncerVerdict,
    GetFileResponse,
    InterviewerTurnResult,
    InterviewTurn,
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


def test_bouncer_verdict_orders_subject_before_is_human_with_descriptions() -> None:
    """BouncerVerdict lists subject first and documents every field for structured output."""
    assert list(BouncerVerdict.model_fields.keys()) == [
        "subject",
        "is_human",
        "rejection_message",
    ]
    for field_name, field_info in BouncerVerdict.model_fields.items():
        assert field_info.description is not None and field_info.description.strip(), (
            f"Expected non-empty description on BouncerVerdict.{field_name}"
        )


@pytest.mark.parametrize("blank_subject", ["", "   ", "\t\n"])
def test_bouncer_verdict_rejects_empty_or_whitespace_subject(blank_subject: str) -> None:
    """BouncerVerdict rejects an empty or whitespace-only subject."""
    with pytest.raises(ValidationError):
        BouncerVerdict.model_validate(
            {
                "subject": blank_subject,
                "is_human": False,
                "rejection_message": "Nope.",
            }
        )


def test_interview_turn_strips_and_accepts_valid_strings() -> None:
    """InterviewTurn accepts valid strings and strips surrounding whitespace."""
    turn = InterviewTurn(
        question="  What time do you emerge from your burrow?  ",
        answer="  Usually around 10am after coffee.  ",
    )

    assert turn.question == "What time do you emerge from your burrow?"
    assert turn.answer == "Usually around 10am after coffee."


@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_interview_turn_rejects_empty_or_whitespace_question_or_answer(
    blank_value: str,
) -> None:
    """InterviewTurn rejects empty or whitespace-only question or answer."""
    with pytest.raises(ValidationError):
        InterviewTurn(question=blank_value, answer="Valid answer")

    with pytest.raises(ValidationError):
        InterviewTurn(question="Valid question?", answer=blank_value)


def test_behavioral_dossier_validates_fields_and_has_descriptions() -> None:
    """BehavioralDossier requires non-empty summary and suggested_animal with descriptions."""
    dossier = BehavioralDossier(
        summary="  Nocturnal couch-dweller who hoards snacks and avoids loud noises.  ",
        suggested_animal="  Eurasian Badger  ",
    )

    assert dossier.summary == "Nocturnal couch-dweller who hoards snacks and avoids loud noises."
    assert dossier.suggested_animal == "Eurasian Badger"

    for field_name, field_info in BehavioralDossier.model_fields.items():
        assert field_info.description is not None and field_info.description.strip(), (
            f"Expected non-empty description on BehavioralDossier.{field_name}"
        )


@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_behavioral_dossier_rejects_empty_or_whitespace_fields(blank_value: str) -> None:
    """BehavioralDossier rejects empty or whitespace-only summary or suggested_animal."""
    with pytest.raises(ValidationError):
        BehavioralDossier(summary=blank_value, suggested_animal="Eurasian Badger")

    with pytest.raises(ValidationError):
        BehavioralDossier(summary="Nocturnal forager.", suggested_animal=blank_value)


def test_interviewer_turn_result_parses_in_progress_and_completed_turns() -> None:
    """InterviewerTurnResult parses both in-progress turns and completed turns with dossier."""
    in_progress = InterviewerTurnResult.model_validate(
        {
            "is_complete": False,
            "next_question": "Where do you hoard your snacks?",
        }
    )
    assert in_progress.is_complete is False
    assert in_progress.next_question == "Where do you hoard your snacks?"
    assert in_progress.dossier is None

    completed = InterviewerTurnResult.model_validate(
        {
            "is_complete": True,
            "next_question": "",
            "dossier": {
                "summary": "A solitary coffee-fueled creature of habit.",
                "suggested_animal": "Red Panda",
            },
        }
    )
    assert completed.is_complete is True
    assert completed.next_question == ""
    assert completed.dossier is not None
    assert completed.dossier.suggested_animal == "Red Panda"
    assert completed.dossier.summary == "A solitary coffee-fueled creature of habit."

    for field_name, field_info in InterviewerTurnResult.model_fields.items():
        assert field_info.description is not None and field_info.description.strip(), (
            f"Expected non-empty description on InterviewerTurnResult.{field_name}"
        )


@pytest.mark.parametrize("non_bool", ["false", "true", 1, 0, None])
def test_interviewer_turn_result_rejects_non_bool_is_complete(non_bool: Any) -> None:
    """InterviewerTurnResult enforces StrictBool on is_complete."""
    with pytest.raises(ValidationError):
        InterviewerTurnResult.model_validate(
            {
                "is_complete": non_bool,
                "next_question": "Another question?",
            }
        )
