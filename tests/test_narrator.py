"""Tests for the Narrator TTS execution tool (Gemini mocked, no network)."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import google.genai as genai
import pytest
from google.genai import types
from pydantic import ValidationError

from telegram_documentaries.narrator import (
    DEFAULT_NARRATOR_MODEL,
    DEFAULT_NARRATOR_VOICE,
    NARRATOR_VOICE_PROMPT_PREFIX,
    Narrator,
    NarratorError,
)

SAMPLE_SCRIPT = (
    "Here in the fluorescent-lit midnight kitchen, the solitary Eurasian Badger emerges "
    "from its duvet burrow to guard the biscuit tin with theatrical devotion."
)
RAW_PCM_24KHZ = b"\x00\x00\x20\x00\x40\x00\x20\x00" * 600


def _fake_client_with_response(response: types.GenerateContentResponse) -> MagicMock:
    """Build a fake genai.Client returning a real GenerateContentResponse object."""
    fake_client = MagicMock(spec=genai.Client)
    fake_client.models.generate_content.return_value = response
    return fake_client


def _audio_response(
    audio_bytes: bytes = RAW_PCM_24KHZ,
    mime_type: str = "audio/L16;rate=24000",
    leading_text: str | None = None,
) -> types.GenerateContentResponse:
    """Construct a GenerateContentResponse with an inline audio part."""
    parts: list[types.Part] = []
    if leading_text is not None:
        parts.append(types.Part.from_text(text=leading_text))
    parts.append(
        types.Part(
            inline_data=types.Blob(
                data=audio_bytes,
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


def test_narrator_default_model_and_voice_match_spec_and_can_be_overridden() -> None:
    """DEFAULT_NARRATOR_MODEL and DEFAULT_NARRATOR_VOICE match spec and can be overridden."""
    assert DEFAULT_NARRATOR_MODEL == "gemini-3.1-flash-tts-preview"
    assert DEFAULT_NARRATOR_VOICE == "Charon"

    default_client = _fake_client_with_response(_audio_response())
    Narrator(api_key="test-key", client=default_client).synthesize_speech(SAMPLE_SCRIPT)
    default_call = default_client.models.generate_content.call_args
    assert default_call.kwargs["model"] == "gemini-3.1-flash-tts-preview"
    default_config = default_call.kwargs["config"]
    assert isinstance(default_config, types.GenerateContentConfig)
    assert isinstance(default_config.speech_config, types.SpeechConfig)
    assert default_config.speech_config.voice_config is not None
    assert default_config.speech_config.voice_config.prebuilt_voice_config is not None
    assert default_config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Charon"

    custom_client = _fake_client_with_response(_audio_response())
    Narrator(
        api_key="test-key",
        model_name="custom-tts-model",
        voice_name="Fenrir",
        client=custom_client,
    ).synthesize_speech(SAMPLE_SCRIPT)
    custom_call = custom_client.models.generate_content.call_args
    assert custom_call.kwargs["model"] == "custom-tts-model"
    custom_config = custom_call.kwargs["config"]
    assert isinstance(custom_config, types.GenerateContentConfig)
    assert isinstance(custom_config.speech_config, types.SpeechConfig)
    assert custom_config.speech_config.voice_config is not None
    assert custom_config.speech_config.voice_config.prebuilt_voice_config is not None
    assert custom_config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Fenrir"


def test_synthesize_speech_uses_drozdov_prompt_speech_config_and_encodes_pcm_to_mp3(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """synthesize_speech configures Nikolai Drozdov TTS, converts PCM to MP3, and logs."""
    fake_client = _fake_client_with_response(
        _audio_response(audio_bytes=RAW_PCM_24KHZ, mime_type="audio/L16;rate=24000")
    )
    narrator = Narrator(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        mp3_bytes = narrator.synthesize_speech(SAMPLE_SCRIPT)

    assert isinstance(mp3_bytes, bytes)
    assert len(mp3_bytes) > 0
    assert mp3_bytes != RAW_PCM_24KHZ
    # MP3 frame sync (0xFF followed by >= 0xE0) or ID3 header
    assert mp3_bytes.startswith(b"ID3") or (mp3_bytes[0] == 0xFF and (mp3_bytes[1] & 0xE0) == 0xE0)

    fake_client.models.generate_content.assert_called_once()
    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == DEFAULT_NARRATOR_MODEL

    contents = call.kwargs["contents"]
    prompt_text = (
        " ".join(part for part in contents if isinstance(part, str))
        if isinstance(contents, list)
        else str(contents)
    )
    assert SAMPLE_SCRIPT in prompt_text
    assert NARRATOR_VOICE_PROMPT_PREFIX in prompt_text
    assert "Nikolai Drozdov" in NARRATOR_VOICE_PROMPT_PREFIX
    assert "In the World of Animals" in NARRATOR_VOICE_PROMPT_PREFIX

    config = call.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_modalities == ["AUDIO"]
    assert config.speech_config == types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=DEFAULT_NARRATOR_VOICE)
        )
    )

    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(
        "event=narrator_audio_generated stage=narrator "
        f"voice={DEFAULT_NARRATOR_VOICE!r} "
        f"mime_type='audio/mpeg' bytes={len(mp3_bytes)}" in msg
        for msg in info_logs
    )


@pytest.mark.parametrize(
    ("encoded_bytes", "mime_type"),
    [
        (b"ID3\x04\x00\x00\x00\x00\x00\x00mp3-stream", "audio/mpeg"),
        (b"\xff\xfb\x90\x64mp3-frame-stream", "audio/mp3"),
        (b"OggS\x00\x02ogg-opus-stream", "audio/ogg"),
    ],
)
def test_synthesize_speech_passes_through_already_encoded_audio_cleanly(
    encoded_bytes: bytes,
    mime_type: str,
) -> None:
    """Already-encoded MP3 or OGG inline audio bytes are returned unchanged."""
    fake_client = _fake_client_with_response(
        _audio_response(audio_bytes=encoded_bytes, mime_type=mime_type)
    )
    narrator = Narrator(api_key="test-key", client=fake_client)

    output = narrator.synthesize_speech(SAMPLE_SCRIPT)

    assert output == encoded_bytes


def test_synthesize_to_temp_file_writes_audio_and_deletes_file_on_normal_exit() -> None:
    """synthesize_to_temp_file yields an existing Path with audio bytes and unlinks on exit."""
    fake_client = _fake_client_with_response(
        _audio_response(audio_bytes=RAW_PCM_24KHZ, mime_type="audio/pcm;rate=24000")
    )
    narrator = Narrator(api_key="test-key", client=fake_client)

    captured_path: Path | None = None
    with narrator.synthesize_to_temp_file(SAMPLE_SCRIPT) as audio_path:
        captured_path = audio_path
        assert isinstance(audio_path, Path)
        assert audio_path.exists()
        assert audio_path.suffix in {".mp3", ".ogg"}
        data_on_disk = audio_path.read_bytes()
        assert len(data_on_disk) > 0

    assert captured_path is not None
    assert not captured_path.exists()


def test_synthesize_to_temp_file_preserves_ogg_suffix_when_model_returns_audio_ogg() -> None:
    """synthesize_to_temp_file writes a .ogg temporary file when Gemini returns audio/ogg."""
    ogg_bytes = b"OggS\x00\x02ogg-opus-stream"
    fake_client = _fake_client_with_response(
        _audio_response(audio_bytes=ogg_bytes, mime_type="audio/ogg")
    )
    narrator = Narrator(api_key="test-key", client=fake_client)

    captured_path: Path | None = None
    with narrator.synthesize_to_temp_file(SAMPLE_SCRIPT) as audio_path:
        captured_path = audio_path
        assert audio_path.exists()
        assert audio_path.suffix == ".ogg"
        assert audio_path.read_bytes() == ogg_bytes

    assert captured_path is not None
    assert not captured_path.exists()


def test_synthesize_to_temp_file_deletes_temp_file_when_caller_raises() -> None:
    """synthesize_to_temp_file unlinks the temporary file in finally even on caller exception."""
    fake_client = _fake_client_with_response(_audio_response())
    narrator = Narrator(api_key="test-key", client=fake_client)

    captured_path: Path | None = None
    with (
        pytest.raises(RuntimeError, match="upload failed"),
        narrator.synthesize_to_temp_file(SAMPLE_SCRIPT) as audio_path,
    ):
        captured_path = audio_path
        assert audio_path.exists()
        raise RuntimeError("upload failed")

    assert captured_path is not None
    assert not captured_path.exists()


@pytest.mark.parametrize("blank_script", ["", "   ", "\t\n"])
def test_synthesize_speech_rejects_empty_or_whitespace_script(blank_script: str) -> None:
    """synthesize_speech raises NarratorError when script is empty or whitespace-only."""
    fake_client = _fake_client_with_response(_audio_response())
    narrator = Narrator(api_key="test-key", client=fake_client)

    with pytest.raises(NarratorError, match="script"):
        narrator.synthesize_speech(blank_script)

    fake_client.models.generate_content.assert_not_called()


def test_synthesize_speech_raises_on_missing_audio_or_safety_blocks_with_diagnostics() -> None:
    """Missing candidates, text-only responses, or safety blocks raise NarratorError."""
    prompt_blocked = types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        ),
    )
    narrator_blocked = Narrator(
        api_key="test-key",
        client=_fake_client_with_response(prompt_blocked),
    )
    with pytest.raises(NarratorError) as blocked_exc:
        narrator_blocked.synthesize_speech(SAMPLE_SCRIPT)
    assert "SAFETY" in str(blocked_exc.value)
    assert "block_reason" in str(blocked_exc.value)

    candidate_safety = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]
    )
    narrator_cand = Narrator(
        api_key="test-key",
        client=_fake_client_with_response(candidate_safety),
    )
    with pytest.raises(NarratorError) as cand_exc:
        narrator_cand.synthesize_speech(SAMPLE_SCRIPT)
    assert "SAFETY" in str(cand_exc.value)
    assert "finish_reason" in str(cand_exc.value)

    text_only = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="Text instead of audio.")],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ]
    )
    narrator_text = Narrator(
        api_key="test-key",
        client=_fake_client_with_response(text_only),
    )
    with pytest.raises(NarratorError, match="audio"):
        narrator_text.synthesize_speech(SAMPLE_SCRIPT)


def test_synthesize_speech_raises_when_inline_data_has_invalid_mime_type() -> None:
    """An inline_data part with a non-audio MIME type fails GeneratedAudio validation."""
    bad_mime_response = _audio_response(
        audio_bytes=b"not-audio",
        mime_type="application/octet-stream",
    )
    narrator = Narrator(
        api_key="test-key",
        client=_fake_client_with_response(bad_mime_response),
    )

    with pytest.raises(NarratorError) as exc_info:
        narrator.synthesize_speech(SAMPLE_SCRIPT)

    assert isinstance(exc_info.value.__cause__, ValidationError)


def test_synthesize_speech_wraps_sdk_exceptions_and_chains_cause() -> None:
    """SDK exceptions from generate_content are wrapped in NarratorError with __cause__."""
    fake_client = MagicMock(spec=genai.Client)
    original = RuntimeError("gemini tts endpoint unavailable")
    fake_client.models.generate_content.side_effect = original
    narrator = Narrator(api_key="test-key", client=fake_client)

    with pytest.raises(NarratorError) as exc_info:
        narrator.synthesize_speech(SAMPLE_SCRIPT)

    assert exc_info.value.__cause__ is original
