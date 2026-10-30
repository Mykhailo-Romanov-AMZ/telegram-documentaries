"""The Scripter: a Gemini agent that writes a British wildlife documentary narration paragraph."""

import google.genai as genai
from google.genai import types

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import BehavioralDossier, DocumentaryScript

DEFAULT_SCRIPTER_MODEL = "gemini-3.1-flash-lite-preview"

SCRIPTER_INSTRUCTION = (
    "You are the Scripter, a revered British wildlife documentary writer. "
    "Write like a highly dramatic, British nature documentary narrator observing a wild beast. "
    "Transform the subject's behavioral dossier and spirit animal into a comedic, theatrical "
    "voiceover script that frames their daily quirks, bedtime habits, and snacking rituals "
    "as survival instincts in the wild. Write strictly ONE cohesive paragraph of roughly "
    "60 to 90 words in plain spoken prose with no markdown formatting, bullet points, or stage "
    "directions, ready for direct voice synthesis."
)


def _empty_response_diagnostics(response: types.GenerateContentResponse) -> str:
    """Describe any safety-block or finish-reason metadata attached to an empty response."""
    details: list[str] = []
    prompt_feedback = response.prompt_feedback
    if prompt_feedback is not None and prompt_feedback.block_reason is not None:
        details.append(f"block_reason={prompt_feedback.block_reason}")
    candidates = response.candidates or []
    if candidates and candidates[0].finish_reason is not None:
        details.append(f"finish_reason={candidates[0].finish_reason}")
    return ", ".join(details)


def _build_scripter_prompt(dossier: BehavioralDossier) -> str:
    """Build the Scripter prompt incorporating the subject's BehavioralDossier."""
    return (
        "Compose the single-paragraph (~60 to 90 words) British wildlife documentary voiceover "
        "narration for this specimen.\n\n"
        f"Spirit Animal Species: {dossier.suggested_animal}\n"
        f"Behavioral Dossier: {dossier.summary}"
    )


class ScripterError(Exception):
    """Raised when the Scripter cannot generate a valid documentary narration script."""


class Scripter:
    """Specialist text-generation agent wrapping Gemini structured output for narration."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_SCRIPTER_MODEL,
        client: genai.Client | None = None,
    ) -> None:
        self._model_name = model_name
        self._client = client if client is not None else genai.Client(api_key=api_key)

    @log_stage("scripter")
    def generate_script(self, dossier: BehavioralDossier) -> str:
        """Generate a validated single-paragraph British nature documentary narration script."""
        prompt = _build_scripter_prompt(dossier)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DocumentaryScript,
            temperature=0.7,
            system_instruction=SCRIPTER_INSTRUCTION,
        )

        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=[prompt],
                config=config,
            )
            text = response.text
            if text is None or not text.strip():
                diagnostics = _empty_response_diagnostics(response)
                detail_suffix = f" ({diagnostics})" if diagnostics else ""
                raise ScripterError(f"Scripter model returned an empty response.{detail_suffix}")
            script_model = DocumentaryScript.model_validate_json(text)
        except ScripterError:
            raise
        except Exception as exc:
            raise ScripterError("Scripter narration generation failed.") from exc

        narration = script_model.narration
        logger.info(
            "event=scripter_narration_generated stage=scripter "
            "suggested_animal=%r words=%d script=%r",
            dossier.suggested_animal,
            len(narration.split()),
            narration,
        )
        return narration
