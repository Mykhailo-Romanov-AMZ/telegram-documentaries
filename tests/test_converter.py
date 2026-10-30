"""Tests for the Converter hybrid animal portrait agent (Gemini mocked, no network)."""

import logging
from unittest.mock import MagicMock

import google.genai as genai
import pytest
from google.genai import types
from pydantic import ValidationError

from telegram_documentaries.converter import (
    CONVERTER_INSTRUCTION,
    DEFAULT_CONVERTER_MODEL,
    Converter,
    ConverterError,
)
from telegram_documentaries.models import BehavioralDossier

SAMPLE_DOSSIER = BehavioralDossier(
    summary="A nocturnal tea-drinking burrower who guards the biscuit tin and avoids loud crowds.",
    suggested_animal="Eurasian Badger",
)
PORTRAIT_BYTES = b"\xff\xd8\xff\xe0user-portrait-jpeg"
HYBRID_PNG_BYTES = b"\x89PNG\r\n\x1a\nsynthesized-hybrid-portrait"


def _fake_client_with_response(response: types.GenerateContentResponse) -> MagicMock:
    """Build a fake genai.Client returning a real GenerateContentResponse object."""
    fake_client = MagicMock(spec=genai.Client)
    fake_client.models.generate_content.return_value = response
    return fake_client


def _image_response(
    image_bytes: bytes = HYBRID_PNG_BYTES,
    mime_type: str = "image/png",
    leading_text: str | None = None,
) -> types.GenerateContentResponse:
    """Construct a GenerateContentResponse with an inline image part (and optional text part)."""
    parts: list[types.Part] = []
    if leading_text is not None:
        parts.append(types.Part.from_text(text=leading_text))
    parts.append(
        types.Part(
            inline_data=types.Blob(
                data=image_bytes,
                mime_type=mime_type,
            )
        )
    )
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )


def test_converter_default_model_matches_spec() -> None:
    """DEFAULT_CONVERTER_MODEL is gemini-3.1-flash-image-preview and can be overridden."""
    assert DEFAULT_CONVERTER_MODEL == "gemini-3.1-flash-image-preview"

    default_client = _fake_client_with_response(_image_response())
    Converter(api_key="test-key", client=default_client).generate_hybrid_portrait(
        portrait_bytes=PORTRAIT_BYTES,
        dossier=SAMPLE_DOSSIER,
    )
    assert default_client.models.generate_content.call_args.kwargs["model"] == (
        "gemini-3.1-flash-image-preview"
    )

    custom_client = _fake_client_with_response(_image_response())
    Converter(
        api_key="test-key",
        model_name="custom-image-model",
        client=custom_client,
    ).generate_hybrid_portrait(
        portrait_bytes=PORTRAIT_BYTES,
        dossier=SAMPLE_DOSSIER,
    )
    assert custom_client.models.generate_content.call_args.kwargs["model"] == "custom-image-model"


def test_generate_hybrid_portrait_passes_portrait_and_dossier_in_single_multimodal_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """generate_hybrid_portrait sends portrait bytes + dossier prompt in one call and logs."""
    fake_client = _fake_client_with_response(_image_response())
    converter = Converter(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        output_bytes = converter.generate_hybrid_portrait(
            portrait_bytes=PORTRAIT_BYTES,
            dossier=SAMPLE_DOSSIER,
        )

    assert output_bytes == HYBRID_PNG_BYTES
    fake_client.models.generate_content.assert_called_once()

    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == DEFAULT_CONVERTER_MODEL

    contents = call.kwargs["contents"]
    text_parts = [item for item in contents if isinstance(item, str)]
    image_parts = [
        item for item in contents if isinstance(item, types.Part) and item.inline_data is not None
    ]
    assert len(text_parts) == 1
    assert SAMPLE_DOSSIER.suggested_animal in text_parts[0]
    assert SAMPLE_DOSSIER.summary in text_parts[0]
    assert len(image_parts) == 1
    assert image_parts[0].inline_data is not None
    assert image_parts[0].inline_data.data == PORTRAIT_BYTES
    assert image_parts[0].inline_data.mime_type == "image/jpeg"

    config = call.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_modalities == ["IMAGE", "TEXT"]
    assert config.system_instruction == CONVERTER_INSTRUCTION
    assert CONVERTER_INSTRUCTION.strip()

    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(
        "event=converter_portrait_generated stage=converter "
        f"suggested_animal={SAMPLE_DOSSIER.suggested_animal!r} "
        f"mime_type='image/png' bytes={len(HYBRID_PNG_BYTES)}" in msg
        for msg in info_logs
    )


def test_generate_hybrid_portrait_extracts_image_when_response_has_leading_text_part() -> None:
    """When Gemini returns both a text commentary part and an inline image part, extracts image."""
    response = _image_response(
        image_bytes=b"\xff\xd8hybrid-jpeg",
        mime_type="image/jpeg",
        leading_text="Here is your Eurasian Badger portrait!",
    )
    converter = Converter(api_key="test-key", client=_fake_client_with_response(response))

    result = converter.generate_hybrid_portrait(
        portrait_bytes=PORTRAIT_BYTES,
        dossier=SAMPLE_DOSSIER,
    )

    assert result == b"\xff\xd8hybrid-jpeg"


def test_generate_hybrid_portrait_rejects_empty_portrait_bytes() -> None:
    """Passing empty portrait_bytes raises ConverterError before calling the Gemini API."""
    fake_client = _fake_client_with_response(_image_response())
    converter = Converter(api_key="test-key", client=fake_client)

    with pytest.raises(ConverterError, match="portrait_bytes"):
        converter.generate_hybrid_portrait(portrait_bytes=b"", dossier=SAMPLE_DOSSIER)

    fake_client.models.generate_content.assert_not_called()


def test_generate_hybrid_portrait_raises_on_empty_candidates_with_safety_diagnostics() -> None:
    """Empty candidates or safety blocks raise ConverterError including block/finish reasons."""
    prompt_blocked = types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        ),
    )
    converter_blocked = Converter(
        api_key="test-key",
        client=_fake_client_with_response(prompt_blocked),
    )
    with pytest.raises(ConverterError) as blocked_exc:
        converter_blocked.generate_hybrid_portrait(
            portrait_bytes=PORTRAIT_BYTES,
            dossier=SAMPLE_DOSSIER,
        )
    assert "SAFETY" in str(blocked_exc.value)
    assert "block_reason" in str(blocked_exc.value)

    candidate_safety = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]
    )
    converter_cand = Converter(
        api_key="test-key",
        client=_fake_client_with_response(candidate_safety),
    )
    with pytest.raises(ConverterError) as cand_exc:
        converter_cand.generate_hybrid_portrait(
            portrait_bytes=PORTRAIT_BYTES,
            dossier=SAMPLE_DOSSIER,
        )
    assert "SAFETY" in str(cand_exc.value)
    assert "finish_reason" in str(cand_exc.value)


def test_generate_hybrid_portrait_raises_when_response_has_only_text_and_no_image() -> None:
    """A response containing only text parts and no inline image part raises ConverterError."""
    text_only_response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="I can only describe the badger in words.")],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )
    converter = Converter(
        api_key="test-key",
        client=_fake_client_with_response(text_only_response),
    )

    with pytest.raises(ConverterError, match="image"):
        converter.generate_hybrid_portrait(
            portrait_bytes=PORTRAIT_BYTES,
            dossier=SAMPLE_DOSSIER,
        )


def test_generate_hybrid_portrait_raises_when_inline_data_has_invalid_mime_type() -> None:
    """An inline_data part with a non-image MIME type fails GeneratedImage validation."""
    bad_mime_response = _image_response(
        image_bytes=b"not-an-image",
        mime_type="application/octet-stream",
    )
    converter = Converter(
        api_key="test-key",
        client=_fake_client_with_response(bad_mime_response),
    )

    with pytest.raises(ConverterError) as exc_info:
        converter.generate_hybrid_portrait(
            portrait_bytes=PORTRAIT_BYTES,
            dossier=SAMPLE_DOSSIER,
        )

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_generate_hybrid_portrait_wraps_sdk_exceptions_and_chains_cause() -> None:
    """SDK exceptions from generate_content are wrapped in ConverterError with __cause__."""
    fake_client = MagicMock(spec=genai.Client)
    original = RuntimeError("gemini image endpoint unavailable")
    fake_client.models.generate_content.side_effect = original
    converter = Converter(api_key="test-key", client=fake_client)

    with pytest.raises(ConverterError) as exc_info:
        converter.generate_hybrid_portrait(
            portrait_bytes=PORTRAIT_BYTES,
            dossier=SAMPLE_DOSSIER,
        )

    assert exc_info.value.__cause__ is original
