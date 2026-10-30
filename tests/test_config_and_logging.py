"""Tests for configuration loading and decorator-based structured logging."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from telegram_documentaries.config import Settings, load_settings
from telegram_documentaries.logging_utils import configure_logging, log_stage


def test_load_settings_succeeds_with_valid_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_settings returns validated Settings when valid credentials are provided."""
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=test-bot-token\nGEMINI_API_KEY=test-gemini-key\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    settings = load_settings(env_file=env_file)

    assert isinstance(settings, Settings)
    assert settings.telegram_bot_token == "test-bot-token"
    assert settings.gemini_api_key == "test-gemini-key"


@pytest.mark.parametrize(
    "invalid_token",
    [
        "",
        "   ",
        "your_telegram_bot_token_here",
    ],
)
def test_load_settings_rejects_empty_or_placeholder_telegram_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_token: str
) -> None:
    """load_settings raises ValidationError when TELEGRAM_BOT_TOKEN is empty or placeholder."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"TELEGRAM_BOT_TOKEN={invalid_token}\nGEMINI_API_KEY=valid_key\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        load_settings(env_file=env_file)


def test_load_settings_rejects_missing_telegram_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """load_settings raises ValidationError when TELEGRAM_BOT_TOKEN is missing completely."""
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=valid_key\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        load_settings(env_file=env_file)


@pytest.mark.parametrize(
    "invalid_gemini_key",
    [
        "",
        "   ",
        "your_gemini_api_key_here",
    ],
)
def test_load_settings_rejects_empty_or_placeholder_gemini_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_gemini_key: str
) -> None:
    """load_settings raises ValidationError when GEMINI_API_KEY is empty or placeholder."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN=123456:valid_token\nGEMINI_API_KEY={invalid_gemini_key}\n"
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        load_settings(env_file=env_file)


def test_log_stage_decorator_logs_start_and_completion_with_chat_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """@log_stage logs entry, completion, stage name, and chat_id when present."""
    configure_logging(level=logging.INFO)

    @log_stage("gateway")
    def greet_user(chat_id: int, name: str) -> str:
        return f"Hello {name}"

    with caplog.at_level(logging.INFO):
        result = greet_user(chat_id=42, name="Attenborough")

    assert result == "Hello Attenborough"
    assert greet_user.__name__ == "greet_user"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "stage=gateway" in messages
    assert "greet_user" in messages
    assert "chat_id=42" in messages
    assert "start" in messages.lower()
    assert "complete" in messages.lower() or "success" in messages.lower()


def test_log_stage_decorator_logs_exception_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """@log_stage logs full exception context and re-raises when the wrapped function fails."""

    @log_stage("gateway")
    def failing_operation(chat_id: int) -> None:
        raise RuntimeError("upstream timeout")

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="upstream timeout"):
        failing_operation(chat_id=99)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_records) == 1
    assert "stage=gateway" in error_records[0].getMessage()
    assert "chat_id=99" in error_records[0].getMessage()
    assert error_records[0].exc_info is not None
