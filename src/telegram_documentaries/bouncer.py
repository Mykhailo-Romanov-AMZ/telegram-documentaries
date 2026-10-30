"""The Bouncer: a Gemini-powered vision gate that checks for a human subject."""

import google.genai as genai
from google.genai import types

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import BouncerVerdict

DEFAULT_BOUNCER_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_REJECTION_MESSAGE = (
    "That's a fascinating specimen, mate — but I need a human. Send me a portrait of yourself."
)

BOUNCER_INSTRUCTION = (
    "You are the Bouncer, a deadpan, slightly theatrical security guard for a nature "
    "documentary bot. Inspect the supplied image and first identify the single primary, "
    "central subject of the photo in 'subject'. Set is_human=true ONLY if the primary, "
    "central subject of the photo is a real human person (portrait, selfie, or person). "
    "Set is_human=false for cars, vehicles (even if a driver is visible inside, a person "
    "appears in a window reflection, or background bystanders/pedestrians are present), "
    "pets, animals, food, landscapes, objects, and illustrations. If is_human is false, "
    "write a short, cheeky, in-character rejection in 'rejection_message' that names the "
    "real subject you saw (for example, \"That's a toaster, mate. Magnificent specimen — "
    'but I need a human."). Never sound like an HTTP error or a stack trace. If is_human '
    "is true, leave rejection_message empty."
)

BOUNCER_PROMPT = (
    "First identify the primary, central subject of this photo in 'subject', then decide "
    "whether that primary subject is a real human portrait/person and return your verdict."
)


def _empty_response_diagnostics(response: types.GenerateContentResponse) -> str:
    """Describe any safety-block metadata attached to an empty Gemini response."""
    details: list[str] = []
    prompt_feedback = response.prompt_feedback
    if prompt_feedback is not None and prompt_feedback.block_reason is not None:
        details.append(f"block_reason={prompt_feedback.block_reason}")
    candidates = response.candidates or []
    if candidates and candidates[0].finish_reason is not None:
        details.append(f"finish_reason={candidates[0].finish_reason}")
    return ", ".join(details)


class BouncerError(Exception):
    """Raised when the Bouncer cannot produce a valid verdict."""


class Bouncer:
    """Thin wrapper around Gemini structured output for human detection."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_BOUNCER_MODEL,
        client: genai.Client | None = None,
    ) -> None:
        self._model_name = model_name
        self._client = client if client is not None else genai.Client(api_key=api_key)

    @log_stage("bouncer")
    def evaluate(self, image_bytes: bytes) -> BouncerVerdict:
        """Classify an image as human or non-human and return a validated verdict."""
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BouncerVerdict,
            temperature=0.2,
            system_instruction=BOUNCER_INSTRUCTION,
        )

        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=[BOUNCER_PROMPT, image_part],
                config=config,
            )
            text = response.text
            if text is None or not text.strip():
                diagnostics = _empty_response_diagnostics(response)
                detail_suffix = f" ({diagnostics})" if diagnostics else ""
                raise BouncerError(f"Bouncer model returned an empty response.{detail_suffix}")
            verdict = BouncerVerdict.model_validate_json(text)
        except BouncerError:
            raise
        except Exception as exc:
            raise BouncerError("Bouncer evaluation failed.") from exc

        if not verdict.is_human and not verdict.rejection_message.strip():
            logger.warning(
                "event=bouncer_rejection_fallback stage=bouncer subject=%r",
                verdict.subject,
            )
            verdict = verdict.model_copy(update={"rejection_message": DEFAULT_REJECTION_MESSAGE})
        logger.info(
            "event=bouncer_verdict stage=bouncer is_human=%s subject=%r",
            verdict.is_human,
            verdict.subject,
        )
        return verdict
