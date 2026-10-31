"""Tests for the Scripter documentary narration agent (Gemini mocked, no network)."""

import json
import logging
from unittest.mock import MagicMock

import google.genai as genai
import pytest
from google.genai import types
from pydantic import ValidationError

from telegram_documentaries.models import BehavioralDossier, DocumentaryScript
from telegram_documentaries.scripter import (
    DEFAULT_SCRIPTER_MODEL,
    SCRIPTER_INSTRUCTION,
    Scripter,
    ScripterError,
)

SAMPLE_DOSSIER = BehavioralDossier(
    summary="A nocturnal tea-drinking burrower who guards the biscuit tin and avoids loud crowds.",
    suggested_animal="Eurasian Badger",
)
SAMPLE_NARRATION = (
    "Here, beneath the flickering fluorescent canopy of the midnight kitchen, the solitary "
    "Eurasian Badger emerges from its duvet burrow. Driven by an ancient, unyielding instinct "
    "for Earl Grey and chocolate digestives, this magnificent creature tiptoes across the cold "
    "linoleum, ears twitching at the faintest creak of the kettle. Should a housemate approach "
    "the biscuit tin, it will retreat into the shadows, clutching its mug in quiet triumph."
)


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


def test_scripter_default_model_matches_spec_and_can_be_overridden() -> None:
    """DEFAULT_SCRIPTER_MODEL is gemini-3.1-flash-lite-preview and can be overridden."""
    assert DEFAULT_SCRIPTER_MODEL == "gemini-3.1-flash-lite-preview"

    payload = json.dumps({"narration": SAMPLE_NARRATION})
    default_client = _fake_client(payload)
    Scripter(api_key="test-key", client=default_client).generate_script(dossier=SAMPLE_DOSSIER)
    assert default_client.models.generate_content.call_args.kwargs["model"] == (
        "gemini-3.1-flash-lite-preview"
    )

    custom_client = _fake_client(payload)
    Scripter(
        api_key="test-key",
        model_name="custom-scripter-model",
        client=custom_client,
    ).generate_script(dossier=SAMPLE_DOSSIER)
    assert custom_client.models.generate_content.call_args.kwargs["model"] == (
        "custom-scripter-model"
    )


def test_generate_script_passes_dossier_structured_config_and_logs_narration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """generate_script invokes Gemini with DocumentaryScript schema, logs, and returns narration."""
    fake_client = _fake_client(json.dumps({"narration": SAMPLE_NARRATION}))
    scripter = Scripter(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        narration = scripter.generate_script(dossier=SAMPLE_DOSSIER)

    assert narration == SAMPLE_NARRATION
    fake_client.models.generate_content.assert_called_once()

    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == DEFAULT_SCRIPTER_MODEL

    contents = call.kwargs["contents"]
    if isinstance(contents, list):
        prompt_text = " ".join(part for part in contents if isinstance(part, str))
    else:
        prompt_text = str(contents)
    assert SAMPLE_DOSSIER.suggested_animal in prompt_text
    assert SAMPLE_DOSSIER.summary in prompt_text

    config = call.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_mime_type == "application/json"
    assert config.response_schema is DocumentaryScript
    assert config.temperature == 0.7
    assert config.system_instruction == SCRIPTER_INSTRUCTION
    assert (
        "Write like a highly dramatic, British nature documentary narrator observing a wild beast"
        in SCRIPTER_INSTRUCTION
    )
    assert "60" in SCRIPTER_INSTRUCTION and "90" in SCRIPTER_INSTRUCTION

    expected_words = len(SAMPLE_NARRATION.split())
    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(
        "event=scripter_narration_generated stage=scripter "
        f"suggested_animal={SAMPLE_DOSSIER.suggested_animal!r} "
        f"words={expected_words} script={SAMPLE_NARRATION!r}" in msg
        for msg in info_logs
    )


def test_generate_script_collapses_multiline_narration_into_single_paragraph() -> None:
    """generate_script normalizes internal line breaks via DocumentaryScript validation."""
    multiline = "Line one in the undergrowth.\n\nLine two by the kettle."
    fake_client = _fake_client(json.dumps({"narration": multiline}))
    scripter = Scripter(api_key="test-key", client=fake_client)

    narration = scripter.generate_script(dossier=SAMPLE_DOSSIER)

    assert narration == "Line one in the undergrowth. Line two by the kettle."


def test_generate_script_raises_on_empty_or_blocked_responses_with_diagnostics() -> None:
    """Empty responses or safety blocks raise ScripterError with block/finish reasons."""
    empty_response = types.GenerateContentResponse(candidates=[])
    scripter_empty = Scripter(
        api_key="test-key",
        client=_fake_client_with_response(empty_response),
    )
    with pytest.raises(ScripterError, match="empty response"):
        scripter_empty.generate_script(dossier=SAMPLE_DOSSIER)

    whitespace_client = _fake_client("   \n\t  ")
    with pytest.raises(ScripterError, match="empty response"):
        Scripter(api_key="test-key", client=whitespace_client).generate_script(
            dossier=SAMPLE_DOSSIER
        )

    prompt_blocked = types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        ),
    )
    scripter_blocked = Scripter(
        api_key="test-key",
        client=_fake_client_with_response(prompt_blocked),
    )
    with pytest.raises(ScripterError) as blocked_exc:
        scripter_blocked.generate_script(dossier=SAMPLE_DOSSIER)
    assert "SAFETY" in str(blocked_exc.value)
    assert "block_reason" in str(blocked_exc.value)

    candidate_safety = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]
    )
    scripter_cand = Scripter(
        api_key="test-key",
        client=_fake_client_with_response(candidate_safety),
    )
    with pytest.raises(ScripterError) as cand_exc:
        scripter_cand.generate_script(dossier=SAMPLE_DOSSIER)
    assert "SAFETY" in str(cand_exc.value)
    assert "finish_reason" in str(cand_exc.value)


@pytest.mark.parametrize(
    "malformed_payload",
    [
        "not valid json",
        "{}",
        '{"narration": ""}',
        '{"narration": "   "}',
    ],
)
def test_generate_script_raises_scripter_error_on_invalid_json_or_schema(
    malformed_payload: str,
) -> None:
    """Invalid JSON or empty narration raises ScripterError chaining ValidationError."""
    fake_client = _fake_client(malformed_payload)
    scripter = Scripter(api_key="test-key", client=fake_client)

    with pytest.raises(ScripterError) as exc_info:
        scripter.generate_script(dossier=SAMPLE_DOSSIER)

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_generate_script_wraps_sdk_exceptions_and_chains_cause() -> None:
    """SDK exceptions from generate_content are wrapped in ScripterError with __cause__."""
    fake_client = MagicMock(spec=genai.Client)
    original = RuntimeError("gemini text endpoint timeout")
    fake_client.models.generate_content.side_effect = original
    scripter = Scripter(api_key="test-key", client=fake_client)

    with pytest.raises(ScripterError) as exc_info:
        scripter.generate_script(dossier=SAMPLE_DOSSIER)

    assert exc_info.value.__cause__ is original
