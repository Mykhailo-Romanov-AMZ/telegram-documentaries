"""The Interviewer: a Gemini-powered behavioral interviewer and dossier builder."""

from collections.abc import Sequence

import google.genai as genai
from google.genai import types

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import (
    BehavioralDossier,
    InterviewerTurnResult,
    InterviewTurn,
)

DEFAULT_INTERVIEWER_MODEL = "gemini-3.1-flash-lite-preview"
MIN_INTERVIEW_QUESTIONS = 5
MAX_INTERVIEW_QUESTIONS = 7

INTERVIEWER_INSTRUCTION = (
    "You are the Interviewer, an investigative, playful, slightly eccentric British wildlife "
    "documentary field researcher studying a human specimen in their natural habitat. "
    "Conduct a sequential 5 to 7 question behavioral interview, asking strictly ONE concise, "
    "engaging question at a time about the subject's daily habits, sleep cycles, territory "
    "marking, social rituals, stress responses, or favorite forage. "
    f"Never complete the interview in fewer than {MIN_INTERVIEW_QUESTIONS} answered questions, "
    f"and always complete the interview by the {MAX_INTERVIEW_QUESTIONS}th answered question at "
    "the latest. When continuing (is_complete=false), provide the single next question in "
    "'next_question' and leave 'dossier' null. When completing (is_complete=true, after 5 to 7 "
    "answers), compile a vivid 'dossier' with a cohesive 'summary' paragraph of their behavioral "
    "quirks and a specific wildlife species in 'suggested_animal', and leave 'next_question' empty."
)

START_INTERVIEW_PROMPT = (
    "Inspect this verified human portrait and open the behavioral field interview with Question 1 "
    f"(of {MIN_INTERVIEW_QUESTIONS} to {MAX_INTERVIEW_QUESTIONS}). Set is_complete=false, leave "
    "dossier null, and write your single opening question in 'next_question'."
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


def _format_transcript(history: Sequence[InterviewTurn]) -> str:
    """Format the accumulated Q&A history and turn guidance for the Gemini prompt."""
    lines = ["Field Interview Transcript So Far:"]
    for idx, turn in enumerate(history, start=1):
        lines.append(f"Q{idx}: {turn.question}")
        lines.append(f"A{idx}: {turn.answer}")
    answered = len(history)
    lines.append("")
    if answered < MIN_INTERVIEW_QUESTIONS:
        lines.append(
            f"The subject has answered {answered} of the required minimum "
            f"{MIN_INTERVIEW_QUESTIONS} questions. You MUST continue the interview: "
            f"set is_complete=false and provide Question {answered + 1} in 'next_question'."
        )
    elif answered < MAX_INTERVIEW_QUESTIONS:
        lines.append(
            f"The subject has answered {answered} questions (within the {MIN_INTERVIEW_QUESTIONS}-"
            f"{MAX_INTERVIEW_QUESTIONS} window). If you have enough behavioral detail, set "
            "is_complete=true and return the completed 'dossier'; otherwise set is_complete=false "
            f"and ask Question {answered + 1} in 'next_question'."
        )
    else:
        lines.append(
            f"The subject has answered {answered} questions, reaching the maximum of "
            f"{MAX_INTERVIEW_QUESTIONS}. You MUST complete the interview now: "
            "set is_complete=true, leave 'next_question' empty, and provide the complete "
            "'dossier'."
        )
    return "\n".join(lines)


def format_dossier_message(dossier: BehavioralDossier) -> str:
    """Format the compiled BehavioralDossier for delivery to the user's Telegram chat."""
    return (
        "Field Notes Complete!\n\n"
        f"Spirit Animal: {dossier.suggested_animal}\n\n"
        f"Behavioral Dossier:\n{dossier.summary}"
    )


class InterviewerError(Exception):
    """Raised when the Interviewer cannot produce a valid question or dossier."""


class Interviewer:
    """Stateful interview agent wrapping Gemini structured output."""

    def __init__(
        self,
        api_key: str,
        model_name: str = DEFAULT_INTERVIEWER_MODEL,
        client: genai.Client | None = None,
    ) -> None:
        self._model_name = model_name
        self._client = client if client is not None else genai.Client(api_key=api_key)

    def _generate_turn(self, contents: list[types.Part | str]) -> InterviewerTurnResult:
        """Invoke Gemini with the InterviewerTurnResult schema and validate the response."""
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=InterviewerTurnResult,
            temperature=0.7,
            system_instruction=INTERVIEWER_INSTRUCTION,
        )
        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=config,
            )
            text = response.text
            if text is None or not text.strip():
                diagnostics = _empty_response_diagnostics(response)
                detail_suffix = f" ({diagnostics})" if diagnostics else ""
                raise InterviewerError(
                    f"Interviewer model returned an empty response.{detail_suffix}"
                )
            return InterviewerTurnResult.model_validate_json(text)
        except InterviewerError:
            raise
        except Exception as exc:
            raise InterviewerError("Interviewer generation failed.") from exc

    @log_stage("interviewer")
    def start_interview(self, portrait_bytes: bytes) -> str:
        """Generate Question 1 from the user's verified portrait photo."""
        image_part = types.Part.from_bytes(data=portrait_bytes, mime_type="image/jpeg")
        result = self._generate_turn(contents=[START_INTERVIEW_PROMPT, image_part])
        first_question = result.next_question.strip()
        if not first_question:
            raise InterviewerError("Interviewer model returned an empty opening question.")
        logger.info("event=interviewer_question stage=interviewer turn=1")
        return first_question

    @log_stage("interviewer")
    def advance_interview(self, history: Sequence[InterviewTurn]) -> InterviewerTurnResult:
        """Evaluate the Q&A history and either return the next question or final dossier."""
        turn_count = len(history)
        if turn_count == 0:
            raise InterviewerError("advance_interview requires at least one recorded turn.")

        prompt = _format_transcript(history)
        result = self._generate_turn(contents=[prompt])

        if turn_count < MIN_INTERVIEW_QUESTIONS and result.is_complete:
            cleaned_next = result.next_question.strip()
            if not cleaned_next:
                raise InterviewerError(
                    f"Interviewer completed early at turn {turn_count} without a next_question."
                )
            logger.warning(
                "event=interviewer_early_completion_coerced stage=interviewer turns=%d",
                turn_count,
            )
            result = result.model_copy(
                update={
                    "is_complete": False,
                    "next_question": cleaned_next,
                    "dossier": None,
                }
            )

        if turn_count >= MAX_INTERVIEW_QUESTIONS and not result.is_complete:
            if result.dossier is None:
                raise InterviewerError(
                    f"Interviewer reached max turns ({turn_count}) without a dossier."
                )
            logger.warning(
                "event=interviewer_max_turns_coerced stage=interviewer turns=%d",
                turn_count,
            )
            result = result.model_copy(
                update={
                    "is_complete": True,
                    "next_question": "",
                }
            )

        if result.is_complete:
            if result.dossier is None:
                raise InterviewerError("Interviewer marked interview complete without a dossier.")
            logger.info(
                "event=interviewer_dossier stage=interviewer turns=%d suggested_animal=%r",
                turn_count,
                result.dossier.suggested_animal,
            )
            return result.model_copy(update={"next_question": ""})

        cleaned_next = result.next_question.strip()
        if not cleaned_next:
            raise InterviewerError("Interviewer returned an empty next_question.")
        logger.info(
            "event=interviewer_question stage=interviewer turn=%d",
            turn_count + 1,
        )
        return result.model_copy(update={"next_question": cleaned_next, "dossier": None})
