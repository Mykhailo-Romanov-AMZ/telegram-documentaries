"""Tests for the Bouncer vision-gate agent (Gemini mocked, no network)."""

import json
import logging
from unittest.mock import MagicMock

import google.genai as genai
import pytest
from google.genai import types
from pydantic import ValidationError

from telegram_documentaries.bouncer import (
    BOUNCER_INSTRUCTION,
    BOUNCER_PROMPT,
    DEFAULT_BOUNCER_MODEL,
    DEFAULT_REJECTION_MESSAGE,
    Bouncer,
    BouncerError,
)
from telegram_documentaries.models import BouncerVerdict


def _fake_client(return_text: str | None) -> MagicMock:
    """Build a fake genai.Client whose generate_content returns the given text."""
    fake_client = MagicMock(spec=genai.Client)
    response = MagicMock()
    response.text = return_text
    fake_client.models.generate_content.return_value = response
    return fake_client


def _fake_client_with_response(response: types.GenerateContentResponse) -> MagicMock:
    """Build a fake genai.Client returning a real GenerateContentResponse object."""
    fake_client = MagicMock(spec=genai.Client)
    fake_client.models.generate_content.return_value = response
    return fake_client


def test_evaluate_sends_image_and_returns_parsed_human_verdict() -> None:
    """evaluate sends the image bytes and parses valid schema JSON into BouncerVerdict."""
    fake_client = _fake_client(
        '{"is_human": true, "subject": "a smiling person", "rejection_message": ""}'
    )
    bouncer = Bouncer(api_key="test-key", client=fake_client)
    image_bytes = b"\xff\xd8image-bytes"

    verdict = bouncer.evaluate(image_bytes=image_bytes)

    assert isinstance(verdict, BouncerVerdict)
    assert verdict.is_human is True
    assert verdict.subject == "a smiling person"

    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == DEFAULT_BOUNCER_MODEL
    contents = call.kwargs["contents"]
    image_parts = [
        part for part in contents if isinstance(part, types.Part) and part.inline_data is not None
    ]
    assert len(image_parts) == 1
    assert image_parts[0].inline_data is not None
    assert image_parts[0].inline_data.data == image_bytes

    config = call.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_mime_type == "application/json"
    assert config.response_schema is BouncerVerdict


def test_evaluate_returns_non_human_verdict_with_cheeky_copy_verbatim() -> None:
    """A non-human verdict keeps the model's rejection message exactly as written."""
    cheeky = "That's a toaster, mate. Magnificent specimen — but I need a human."
    fake_client = _fake_client(
        json.dumps(
            {
                "is_human": False,
                "subject": "a toaster",
                "rejection_message": cheeky,
            }
        )
    )
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    verdict = bouncer.evaluate(image_bytes=b"toaster-bytes")

    assert verdict.is_human is False
    assert verdict.subject == "a toaster"
    assert verdict.rejection_message == cheeky


@pytest.mark.parametrize(
    "malformed_text",
    [
        "this is not json",
        '{"is_human": true}',
        '{"subject": "a toaster"}',
        '{"is_human": "yes", "subject": "a toaster"}',
    ],
)
def test_evaluate_raises_bouncer_error_on_malformed_output(malformed_text: str) -> None:
    """Invalid JSON or schema-shaped output raises BouncerError, never a silent default."""
    fake_client = _fake_client(malformed_text)
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    with pytest.raises(BouncerError):
        bouncer.evaluate(image_bytes=b"image-bytes")


def test_evaluate_raises_bouncer_error_on_empty_model_output() -> None:
    """A response with no candidates is treated as malformed and raises BouncerError."""
    response = types.GenerateContentResponse(candidates=[])
    bouncer = Bouncer(api_key="test-key", client=_fake_client_with_response(response))

    with pytest.raises(BouncerError):
        bouncer.evaluate(image_bytes=b"image-bytes")


def test_evaluate_reports_safety_finish_reason_in_empty_response_error() -> None:
    """A safety-blocked candidate surfaces its finish_reason in the BouncerError."""
    response = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]
    )
    bouncer = Bouncer(api_key="test-key", client=_fake_client_with_response(response))

    with pytest.raises(BouncerError) as exc_info:
        bouncer.evaluate(image_bytes=b"image-bytes")

    message = str(exc_info.value)
    assert "SAFETY" in message
    assert "finish_reason" in message


def test_evaluate_reports_prompt_block_reason_in_empty_response_error() -> None:
    """A prompt-level safety block surfaces its block_reason in the BouncerError."""
    response = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )
    bouncer = Bouncer(api_key="test-key", client=_fake_client_with_response(response))

    with pytest.raises(BouncerError) as exc_info:
        bouncer.evaluate(image_bytes=b"image-bytes")

    message = str(exc_info.value)
    assert "SAFETY" in message
    assert "block_reason" in message


def test_evaluate_fills_local_fallback_when_non_human_rejection_is_empty() -> None:
    """A non-human verdict without rejection copy receives the deterministic fallback text."""
    fake_client = _fake_client(
        '{"is_human": false, "subject": "a coffee mug", "rejection_message": ""}'
    )
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    verdict = bouncer.evaluate(image_bytes=b"mug-bytes")

    assert verdict.is_human is False
    assert verdict.subject == "a coffee mug"
    assert verdict.rejection_message == DEFAULT_REJECTION_MESSAGE
    assert DEFAULT_REJECTION_MESSAGE.strip()


def test_evaluate_fills_local_fallback_when_non_human_rejection_is_whitespace() -> None:
    """Whitespace-only rejection copy is treated as absent and replaced by the fallback."""
    fake_client = _fake_client(
        '{"is_human": false, "subject": "a lamp post", "rejection_message": "   "}'
    )
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    verdict = bouncer.evaluate(image_bytes=b"lamp-bytes")

    assert verdict.rejection_message == DEFAULT_REJECTION_MESSAGE


def test_evaluate_warns_when_rejection_fallback_is_substituted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Substituting the local rejection copy emits a structured warning with the subject."""
    fake_client = _fake_client(
        '{"is_human": false, "subject": "a coffee mug", "rejection_message": ""}'
    )
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.WARNING):
        verdict = bouncer.evaluate(image_bytes=b"mug-bytes")

    assert verdict.rejection_message == DEFAULT_REJECTION_MESSAGE
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any(
        "event=bouncer_rejection_fallback" in record.getMessage()
        and "a coffee mug" in record.getMessage()
        for record in warnings
    )


def test_evaluate_does_not_warn_when_model_supplied_rejection_copy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No fallback warning is emitted when the model provides its own rejection copy."""
    cheeky = "That's a toaster, mate. Magnificent specimen — but I need a human."
    fake_client = _fake_client(
        json.dumps(
            {
                "is_human": False,
                "subject": "a toaster",
                "rejection_message": cheeky,
            }
        )
    )
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.WARNING):
        verdict = bouncer.evaluate(image_bytes=b"toaster-bytes")

    assert verdict.rejection_message == cheeky
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_evaluate_keeps_empty_rejection_for_human_verdict(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A human verdict is returned with its empty rejection_message untouched and no warning."""
    fake_client = _fake_client('{"is_human": true, "subject": "a person", "rejection_message": ""}')
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.WARNING):
        verdict = bouncer.evaluate(image_bytes=b"person-bytes")

    assert verdict.is_human is True
    assert verdict.rejection_message == ""
    assert "bouncer_rejection_fallback" not in caplog.text


def test_bouncer_model_id_defaults_and_can_be_overridden() -> None:
    """The model id defaults per TECH.md and a custom id is passed through to the SDK."""
    assert DEFAULT_BOUNCER_MODEL == "gemini-3.1-flash-lite-preview"

    default_client = _fake_client('{"is_human": true, "subject": "person"}')
    Bouncer(api_key="test-key", client=default_client).evaluate(image_bytes=b"bytes")
    assert default_client.models.generate_content.call_args.kwargs["model"] == (
        "gemini-3.1-flash-lite-preview"
    )

    custom_client = _fake_client('{"is_human": true, "subject": "person"}')
    Bouncer(api_key="test-key", model_name="custom-model", client=custom_client).evaluate(
        image_bytes=b"bytes"
    )
    assert custom_client.models.generate_content.call_args.kwargs["model"] == "custom-model"


def test_evaluate_wraps_transport_errors_and_chains_the_original() -> None:
    """SDK/transport failures are wrapped in BouncerError with the original exception chained."""
    fake_client = MagicMock(spec=genai.Client)
    original = RuntimeError("gemini transport exploded")
    fake_client.models.generate_content.side_effect = original
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    with pytest.raises(BouncerError) as exc_info:
        bouncer.evaluate(image_bytes=b"image-bytes")

    assert exc_info.value.__cause__ is original


def test_evaluate_chains_validation_error_for_malformed_output() -> None:
    """Malformed output chains the underlying pydantic ValidationError."""
    fake_client = _fake_client("not json at all")
    bouncer = Bouncer(api_key="test-key", client=fake_client)

    with pytest.raises(BouncerError) as exc_info:
        bouncer.evaluate(image_bytes=b"image-bytes")

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_bouncer_instruction_requires_primary_human_subject_and_rejects_cars_with_drivers() -> None:
    """BOUNCER_INSTRUCTION and BOUNCER_PROMPT require a primary human subject and reject cars."""
    lowered_instruction = BOUNCER_INSTRUCTION.lower()
    lowered_prompt = BOUNCER_PROMPT.lower()
    assert "primary" in lowered_instruction
    assert "car" in lowered_instruction or "vehicle" in lowered_instruction
    assert "driver" in lowered_instruction
    assert "reflection" in lowered_instruction
    assert "background" in lowered_instruction or "bystander" in lowered_instruction
    assert "subject" in lowered_prompt


def test_evaluate_logs_bouncer_verdict_at_info_for_human_and_non_human(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """evaluate logs event=bouncer_verdict with is_human and subject at INFO level."""
    human_client = _fake_client(
        '{"subject": "a portrait of a woman", "is_human": true, "rejection_message": ""}'
    )
    car_client = _fake_client(
        '{"subject": "a red parked car", "is_human": false, "rejection_message": "Nice car, mate."}'
    )

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        Bouncer(api_key="test-key", client=human_client).evaluate(image_bytes=b"human")
        Bouncer(api_key="test-key", client=car_client).evaluate(image_bytes=b"car")

    verdict_logs = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO and "event=bouncer_verdict" in record.getMessage()
    ]
    assert any(
        "stage=bouncer is_human=True subject='a portrait of a woman'" in msg for msg in verdict_logs
    )
    assert any(
        "stage=bouncer is_human=False subject='a red parked car'" in msg for msg in verdict_logs
    )
