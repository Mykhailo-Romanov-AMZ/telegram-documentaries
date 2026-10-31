"""Tests for the Interviewer agent and stateful 5-7 turn bounds enforcement (Gemini mocked)."""

import json
import logging
from unittest.mock import MagicMock

import google.genai as genai
import pytest
from google.genai import types
from pydantic import ValidationError

from telegram_documentaries.interviewer import (
    DEFAULT_INTERVIEWER_MODEL,
    MAX_INTERVIEW_QUESTIONS,
    MIN_INTERVIEW_QUESTIONS,
    Interviewer,
    InterviewerError,
    format_dossier_message,
)
from telegram_documentaries.models import (
    BehavioralDossier,
    InterviewerTurnResult,
    InterviewTurn,
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


def _make_history(count: int) -> list[InterviewTurn]:
    """Build a deterministic history of `count` answered interview turns."""
    return [
        InterviewTurn(question=f"Question {i}?", answer=f"Answer {i}") for i in range(1, count + 1)
    ]


def test_interviewer_constants_match_spec() -> None:
    """Model identifier and 5-7 question bounds match TECH.md and requirements.md."""
    assert DEFAULT_INTERVIEWER_MODEL == "gemini-3.1-flash-lite-preview"
    assert MIN_INTERVIEW_QUESTIONS == 5
    assert MAX_INTERVIEW_QUESTIONS == 7


def test_start_interview_sends_portrait_logs_turn_1_and_returns_first_question(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """start_interview sends the portrait image part, logs turn=1, and returns Question 1."""
    fake_client = _fake_client(
        json.dumps(
            {
                "is_complete": False,
                "next_question": (
                    "  When the sun rises, what is the very first ritual in your den?  "
                ),
                "dossier": None,
            }
        )
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)
    portrait = b"\xff\xd8portrait-bytes"

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        question = interviewer.start_interview(portrait_bytes=portrait)

    assert question == "When the sun rises, what is the very first ritual in your den?"

    call = fake_client.models.generate_content.call_args
    assert call.kwargs["model"] == DEFAULT_INTERVIEWER_MODEL
    contents = call.kwargs["contents"]
    image_parts = [
        part for part in contents if isinstance(part, types.Part) and part.inline_data is not None
    ]
    assert len(image_parts) == 1
    assert image_parts[0].inline_data is not None
    assert image_parts[0].inline_data.data == portrait

    config = call.kwargs["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_mime_type == "application/json"
    assert config.response_schema is InterviewerTurnResult

    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any("event=interviewer_question stage=interviewer turn=1" in msg for msg in info_logs)


@pytest.mark.parametrize("blank_question", ["", "   "])
def test_start_interview_raises_when_opening_question_is_empty(blank_question: str) -> None:
    """start_interview raises InterviewerError if the model returns a blank opening question."""
    fake_client = _fake_client(
        json.dumps({"is_complete": False, "next_question": blank_question, "dossier": None})
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with pytest.raises(InterviewerError, match="question"):
        interviewer.start_interview(portrait_bytes=b"portrait")


@pytest.mark.parametrize("answered_count", [1, 2, 3, 4])
def test_advance_interview_continues_when_fewer_than_5_answers(
    caplog: pytest.LogCaptureFixture,
    answered_count: int,
) -> None:
    """When len(history) < 5 and is_complete=False, returns next_question and logs turn."""
    next_q = f"Follow-up question {answered_count + 1}?"
    fake_client = _fake_client(
        json.dumps({"is_complete": False, "next_question": next_q, "dossier": None})
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)
    history = _make_history(answered_count)

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        result = interviewer.advance_interview(history=history)

    assert result == InterviewerTurnResult(
        is_complete=False,
        next_question=next_q,
        dossier=None,
    )
    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(
        f"event=interviewer_question stage=interviewer turn={answered_count + 1}" in msg
        for msg in info_logs
    )


def test_advance_interview_coerces_early_completion_with_warning_when_next_question_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When len(history) < 5 and is_complete=True with next_question, logs warning and coerces."""
    fake_client = _fake_client(
        json.dumps(
            {
                "is_complete": True,
                "next_question": "Where do you hide when guests arrive?",
                "dossier": {
                    "summary": "Premature summary.",
                    "suggested_animal": "Meerkat",
                },
            }
        )
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)
    history = _make_history(3)

    with caplog.at_level(logging.WARNING, logger="telegram_documentaries"):
        result = interviewer.advance_interview(history=history)

    assert result.is_complete is False
    assert result.next_question == "Where do you hide when guests arrive?"
    assert result.dossier is None
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any("event=interviewer_early_completion_coerced" in msg for msg in warnings)


@pytest.mark.parametrize("blank_next", ["", "   "])
def test_advance_interview_raises_on_early_completion_without_next_question(
    blank_next: str,
) -> None:
    """When len(history) < 5 and is_complete=True without next_question, raises error."""
    fake_client = _fake_client(
        json.dumps(
            {
                "is_complete": True,
                "next_question": blank_next,
                "dossier": {
                    "summary": "Premature summary.",
                    "suggested_animal": "Meerkat",
                },
            }
        )
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with pytest.raises(InterviewerError):
        interviewer.advance_interview(history=_make_history(4))


@pytest.mark.parametrize("answered_count", [5, 6])
def test_advance_interview_between_5_and_6_answers_respects_continuing_or_completing(
    caplog: pytest.LogCaptureFixture,
    answered_count: int,
) -> None:
    """When 5 <= len(history) < 7, model may ask another question or complete with a dossier."""
    continuing_client = _fake_client(
        json.dumps(
            {
                "is_complete": False,
                "next_question": "One more question about your territory?",
                "dossier": None,
            }
        )
    )
    interviewer_cont = Interviewer(api_key="test-key", client=continuing_client)
    cont_result = interviewer_cont.advance_interview(history=_make_history(answered_count))
    assert cont_result.is_complete is False
    assert cont_result.next_question == "One more question about your territory?"

    completing_client = _fake_client(
        json.dumps(
            {
                "is_complete": True,
                "next_question": "",
                "dossier": {
                    "summary": "Solitary nocturnal tea drinker who hoards cozy blankets.",
                    "suggested_animal": "Eurasian Badger",
                },
            }
        )
    )
    interviewer_done = Interviewer(api_key="test-key", client=completing_client)
    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        done_result = interviewer_done.advance_interview(history=_make_history(answered_count))

    assert done_result.is_complete is True
    assert done_result.dossier == BehavioralDossier(
        summary="Solitary nocturnal tea drinker who hoards cozy blankets.",
        suggested_animal="Eurasian Badger",
    )
    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(
        f"event=interviewer_dossier stage=interviewer turns={answered_count} "
        "suggested_animal='Eurasian Badger'" in msg
        for msg in info_logs
    )


def test_advance_interview_at_7_answers_completes_and_logs_dossier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When len(history) >= 7 and is_complete=True with dossier, returns completed result."""
    fake_client = _fake_client(
        json.dumps(
            {
                "is_complete": True,
                "next_question": "",
                "dossier": {
                    "summary": "Methodical collector of shiny gadgets who naps at midday.",
                    "suggested_animal": "Eurasian Magpie",
                },
            }
        )
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.INFO, logger="telegram_documentaries"):
        result = interviewer.advance_interview(history=_make_history(7))

    assert result.is_complete is True
    assert result.dossier is not None
    assert result.dossier.suggested_animal == "Eurasian Magpie"
    info_logs = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert any(
        "event=interviewer_dossier stage=interviewer turns=7 suggested_animal='Eurasian Magpie'"
        in msg
        for msg in info_logs
    )


def test_advance_interview_at_7_answers_coerces_incomplete_with_warning_when_dossier_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When len(history) >= 7 and is_complete=False with dossier, logs warning and coerces."""
    fake_client = _fake_client(
        json.dumps(
            {
                "is_complete": False,
                "next_question": "An 8th question that should not be asked?",
                "dossier": {
                    "summary": "Persistent night owl who thrives on espresso.",
                    "suggested_animal": "Barn Owl",
                },
            }
        )
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with caplog.at_level(logging.WARNING, logger="telegram_documentaries"):
        result = interviewer.advance_interview(history=_make_history(7))

    assert result.is_complete is True
    assert result.next_question == ""
    assert result.dossier is not None
    assert result.dossier.suggested_animal == "Barn Owl"
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert any("event=interviewer_max_turns_coerced" in msg for msg in warnings)


def test_advance_interview_at_7_answers_raises_when_dossier_missing() -> None:
    """When len(history) >= 7 and dossier is None, raises InterviewerError."""
    fake_client = _fake_client(
        json.dumps(
            {
                "is_complete": False,
                "next_question": "An 8th question?",
                "dossier": None,
            }
        )
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with pytest.raises(InterviewerError):
        interviewer.advance_interview(history=_make_history(7))


def test_advance_interview_raises_when_incomplete_without_next_question() -> None:
    """When model returns is_complete=False with empty next_question, raises InterviewerError."""
    fake_client = _fake_client(
        json.dumps({"is_complete": False, "next_question": "   ", "dossier": None})
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with pytest.raises(InterviewerError):
        interviewer.advance_interview(history=_make_history(2))


def test_advance_interview_raises_when_complete_at_turn_5_without_dossier() -> None:
    """When model returns is_complete=True at turn 5 with dossier=None, raises InterviewerError."""
    fake_client = _fake_client(
        json.dumps({"is_complete": True, "next_question": "", "dossier": None})
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with pytest.raises(InterviewerError):
        interviewer.advance_interview(history=_make_history(5))


def test_advance_interview_raises_on_empty_history() -> None:
    """advance_interview rejects an empty history list."""
    fake_client = _fake_client(
        json.dumps({"is_complete": False, "next_question": "Q?", "dossier": None})
    )
    interviewer = Interviewer(api_key="test-key", client=fake_client)

    with pytest.raises(InterviewerError):
        interviewer.advance_interview(history=[])


def test_interviewer_raises_on_empty_response_with_safety_diagnostics() -> None:
    """Safety-blocked or empty Gemini responses raise InterviewerError with diagnostics."""
    candidate_blocked = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]
    )
    interviewer_cand = Interviewer(
        api_key="test-key",
        client=_fake_client_with_response(candidate_blocked),
    )
    with pytest.raises(InterviewerError) as exc_info:
        interviewer_cand.start_interview(portrait_bytes=b"portrait")
    assert "SAFETY" in str(exc_info.value)
    assert "finish_reason" in str(exc_info.value)

    prompt_blocked = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )
    interviewer_prompt = Interviewer(
        api_key="test-key",
        client=_fake_client_with_response(prompt_blocked),
    )
    with pytest.raises(InterviewerError) as exc_info_2:
        interviewer_prompt.advance_interview(history=_make_history(2))
    assert "SAFETY" in str(exc_info_2.value)
    assert "block_reason" in str(exc_info_2.value)


def test_interviewer_chains_validation_and_transport_errors() -> None:
    """Invalid JSON chains ValidationError and SDK exceptions chain the original error."""
    bad_json_client = _fake_client("not valid json")
    interviewer_json = Interviewer(api_key="test-key", client=bad_json_client)
    with pytest.raises(InterviewerError) as json_exc:
        interviewer_json.advance_interview(history=_make_history(1))
    assert isinstance(json_exc.value.__cause__, ValidationError)

    transport_client = MagicMock(spec=genai.Client)
    original = RuntimeError("gemini network timeout")
    transport_client.models.generate_content.side_effect = original
    interviewer_net = Interviewer(api_key="test-key", client=transport_client)
    with pytest.raises(InterviewerError) as net_exc:
        interviewer_net.start_interview(portrait_bytes=b"portrait")
    assert net_exc.value.__cause__ is original


def test_format_dossier_message_includes_suggested_animal_and_summary() -> None:
    """format_dossier_message includes both suggested_animal and summary."""
    dossier = BehavioralDossier(
        summary="A nocturnal forager who guards the kettle fiercely.",
        suggested_animal="Eurasian Badger",
    )

    formatted = format_dossier_message(dossier)

    assert "Eurasian Badger" in formatted
    assert "A nocturnal forager who guards the kettle fiercely." in formatted
