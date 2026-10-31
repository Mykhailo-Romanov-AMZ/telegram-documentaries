"""The Narrator: a deterministic Gemini TTS execution tool for documentary voice synthesis."""

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import google.genai as genai
import lameenc
from google.genai import types

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import GeneratedAudio

DEFAULT_NARRATOR_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_NARRATOR_VOICE = "Charon"
DEFAULT_PCM_SAMPLE_RATE = 24000
DEFAULT_MP3_BIT_RATE_KBPS = 64
DEFAULT_MP3_QUALITY = 2
_SAMPLE_RATE_PATTERN = re.compile(r"rate\s*=\s*(\d+)")

NARRATOR_VOICE_PROMPT_PREFIX = (
    "Read the following wildlife documentary narration aloud in the warm, affectionate, "
    "enthusiastic, grandfatherly Russian-accented naturalist voice and joyful storytelling "
    "cadence of Nikolai Drozdov hosting 'In the World of Animals' ('В мире животных'). "
    "Speak only the provided narration text aloud with gentle humor and wonder:\n\n"
)


def _response_diagnostics(response: types.GenerateContentResponse) -> str:
    """Describe any safety-block or finish-reason metadata attached to a Gemini response."""
    details: list[str] = []
    prompt_feedback = response.prompt_feedback
    if prompt_feedback is not None and prompt_feedback.block_reason is not None:
        details.append(f"block_reason={prompt_feedback.block_reason}")
    candidates = response.candidates or []
    if candidates and candidates[0].finish_reason is not None:
        details.append(f"finish_reason={candidates[0].finish_reason}")
    return ", ".join(details)


def _parse_sample_rate(mime_type: str) -> int:
    """Extract the PCM sample rate from a MIME string like 'audio/l16;rate=24000'."""
    match = _SAMPLE_RATE_PATTERN.search(mime_type)
    if match is not None:
        rate = int(match.group(1))
        if rate > 0:
            return rate
    return DEFAULT_PCM_SAMPLE_RATE


def _encode_pcm_to_mp3(pcm_bytes: bytes, sample_rate: int = DEFAULT_PCM_SAMPLE_RATE) -> bytes:
    """Encode 16-bit little-endian mono PCM bytes into an MP3 audio stream."""
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(DEFAULT_MP3_BIT_RATE_KBPS)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(DEFAULT_MP3_QUALITY)
    mp3_data = bytes(encoder.encode(pcm_bytes) + encoder.flush())
    if not mp3_data:
        raise NarratorError("PCM-to-MP3 encoding produced empty audio output.")
    return mp3_data


def _extract_generated_audio(response: types.GenerateContentResponse) -> GeneratedAudio:
    """Extract and validate the first inline audio part from a Gemini TTS response."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        if content is None or not content.parts:
            continue
        for part in content.parts:
            inline_data = part.inline_data
            if inline_data is not None and inline_data.data:
                raw_mime = inline_data.mime_type or "audio/mpeg"
                validated = GeneratedAudio(
                    audio_bytes=inline_data.data,
                    mime_type=raw_mime,
                )
                base_mime = validated.mime_type.partition(";")[0].strip()
                if base_mime in {"audio/l16", "audio/pcm"}:
                    sample_rate = _parse_sample_rate(validated.mime_type)
                    mp3_bytes = _encode_pcm_to_mp3(
                        pcm_bytes=validated.audio_bytes,
                        sample_rate=sample_rate,
                    )
                    return GeneratedAudio(audio_bytes=mp3_bytes, mime_type="audio/mpeg")
                if base_mime == "audio/mp3":
                    return GeneratedAudio(
                        audio_bytes=validated.audio_bytes,
                        mime_type="audio/mpeg",
                    )
                return validated

    diagnostics = _response_diagnostics(response)
    detail_suffix = f" ({diagnostics})" if diagnostics else ""
    raise NarratorError(f"Narrator model did not return an inline audio part.{detail_suffix}")


class NarratorError(Exception):
    """Raised when the Narrator cannot synthesize valid documentary voiceover audio."""


class Narrator:
    """Deterministic TTS execution tool wrapping Gemini native speech synthesis."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_NARRATOR_MODEL,
        voice_name: str = DEFAULT_NARRATOR_VOICE,
        client: genai.Client | None = None,
    ) -> None:
        self._model_name = model_name
        self._voice_name = voice_name
        self._client = client if client is not None else genai.Client(api_key=api_key)
        self._last_mime_type = "audio/mpeg"

    def _synthesize_audio(self, script: str) -> GeneratedAudio:
        """Synthesize a documentary script into a validated GeneratedAudio model."""
        cleaned_script = script.strip()
        if not cleaned_script:
            raise NarratorError("Narrator script must not be empty.")

        prompt = f"{NARRATOR_VOICE_PROMPT_PREFIX}{cleaned_script}"
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._voice_name,
                    )
                )
            ),
        )

        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=[prompt],
                config=config,
            )
            generated = _extract_generated_audio(response)
        except NarratorError:
            raise
        except Exception as exc:
            raise NarratorError("Narrator speech synthesis failed.") from exc

        logger.info(
            "event=narrator_audio_generated stage=narrator voice=%r mime_type=%r bytes=%d",
            self._voice_name,
            generated.mime_type,
            len(generated.audio_bytes),
        )
        self._last_mime_type = generated.mime_type
        return generated

    @log_stage("narrator")
    def synthesize_speech(self, script: str) -> bytes:
        """Synthesize a documentary script into Telegram-compatible MP3/OGG audio bytes."""
        return self._synthesize_audio(script).audio_bytes

    @contextmanager
    def synthesize_to_temp_file(self, script: str) -> Iterator[Path]:
        """Synthesize speech to a temporary audio file and unconditionally delete it on exit."""
        audio_bytes = self.synthesize_speech(script)
        base_mime = self._last_mime_type.partition(";")[0].strip()
        suffix = ".ogg" if base_mime == "audio/ogg" or audio_bytes.startswith(b"OggS") else ".mp3"
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(audio_bytes)
            yield temp_path
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
