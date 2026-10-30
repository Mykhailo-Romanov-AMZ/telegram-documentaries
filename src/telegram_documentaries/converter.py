"""The Converter: a Gemini multimodal agent that synthesizes a hybrid animal-human portrait."""

import google.genai as genai
from google.genai import types

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import BehavioralDossier, GeneratedImage

DEFAULT_CONVERTER_MODEL = "gemini-3.1-flash-image-preview"

CONVERTER_INSTRUCTION = (
    "You are the Converter, a master wildlife documentary portrait artist. "
    "Transform the supplied human portrait photo into an anthropomorphic human-animal hybrid "
    "wildlife portrait that fuses the subject's distinctive facial features, expression, "
    "hairstyle/accessories, lighting, posture, and composition with their assigned spirit animal "
    "and behavioral habits. Keep the result recognizable as a witty, cinematic wildlife "
    "documentary portrait of this specific individual merged with their spirit animal."
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


def _build_converter_prompt(dossier: BehavioralDossier) -> str:
    """Build the multimodal fusion prompt incorporating the subject's BehavioralDossier."""
    return (
        "Create a single hybrid animal-human portrait from this human photo and behavioral "
        "field dossier.\n\n"
        f"Spirit Animal Species: {dossier.suggested_animal}\n"
        f"Behavioral Dossier: {dossier.summary}\n\n"
        "Preserve the person's facial likeness, expression, pose, clothing cues, and framing "
        f"while seamlessly transforming them into an expressive {dossier.suggested_animal} "
        "in a natural habitat that reflects their behavioral habits."
    )


def _extract_generated_image(response: types.GenerateContentResponse) -> GeneratedImage:
    """Extract and validate the first inline image part from a Gemini response."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        if content is None or not content.parts:
            continue
        for part in content.parts:
            inline_data = part.inline_data
            if inline_data is not None and inline_data.data:
                mime_type = inline_data.mime_type or "image/png"
                return GeneratedImage(
                    image_bytes=inline_data.data,
                    mime_type=mime_type,
                )

    diagnostics = _response_diagnostics(response)
    detail_suffix = f" ({diagnostics})" if diagnostics else ""
    raise ConverterError(f"Converter model did not return an inline image part.{detail_suffix}")


class ConverterError(Exception):
    """Raised when the Converter cannot synthesize a valid hybrid animal portrait."""


class Converter:
    """Multimodal image synthesis agent wrapping Gemini native image generation."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_CONVERTER_MODEL,
        client: genai.Client | None = None,
    ) -> None:
        self._model_name = model_name
        self._client = client if client is not None else genai.Client(api_key=api_key)

    @log_stage("converter")
    def generate_hybrid_portrait(
        self,
        portrait_bytes: bytes,
        dossier: BehavioralDossier,
    ) -> bytes:
        """Synthesize a hybrid animal-human portrait from the original portrait and dossier."""
        if not portrait_bytes:
            raise ConverterError("Converter portrait_bytes must not be empty.")

        prompt = _build_converter_prompt(dossier)
        image_part = types.Part.from_bytes(data=portrait_bytes, mime_type="image/jpeg")
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            system_instruction=CONVERTER_INSTRUCTION,
        )

        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=[prompt, image_part],
                config=config,
            )
            generated = _extract_generated_image(response)
        except ConverterError:
            raise
        except Exception as exc:
            raise ConverterError("Converter image synthesis failed.") from exc

        logger.info(
            "event=converter_portrait_generated stage=converter "
            "suggested_animal=%r mime_type=%r bytes=%d",
            dossier.suggested_animal,
            generated.mime_type,
            len(generated.image_bytes),
        )
        return generated.image_bytes
