"""Configuration loader and Pydantic settings validation."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

PLACEHOLDER_TELEGRAM_TOKEN = "your_telegram_bot_token_here"
PLACEHOLDER_GEMINI_KEY = "your_gemini_api_key_here"


class Settings(BaseModel):
    """Validated application configuration loaded from environment / .env."""

    telegram_bot_token: str
    gemini_api_key: str

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_telegram_bot_token(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned == PLACEHOLDER_TELEGRAM_TOKEN:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN must be a non-empty valid token and not the placeholder value."
            )
        return cleaned

    @field_validator("gemini_api_key")
    @classmethod
    def validate_gemini_api_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned == PLACEHOLDER_GEMINI_KEY:
            raise ValueError(
                "GEMINI_API_KEY must be a non-empty valid API key and not the placeholder value."
            )
        return cleaned


def load_settings(env_file: Path | str | None = None) -> Settings:
    """Load environment variables from .env (if present) and validate Settings."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        load_dotenv()

    raw_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    raw_gemini_key = os.environ.get("GEMINI_API_KEY", "")
    return Settings(
        telegram_bot_token=raw_token,
        gemini_api_key=raw_gemini_key.strip(),
    )
