"""CLI entrypoint for The Telegram Documentaries bot."""

from telegram_documentaries.bouncer import Bouncer
from telegram_documentaries.config import load_settings
from telegram_documentaries.interviewer import Interviewer
from telegram_documentaries.logging_utils import configure_logging
from telegram_documentaries.poller import run_polling_loop
from telegram_documentaries.session import SessionStore
from telegram_documentaries.telegram_client import TelegramClient


def main() -> None:
    """Configure logging, validate settings, and start the Telegram long-polling loop."""
    configure_logging()
    settings = load_settings()
    client = TelegramClient(bot_token=settings.telegram_bot_token)
    sessions = SessionStore()
    bouncer = Bouncer(api_key=settings.gemini_api_key)
    interviewer = Interviewer(api_key=settings.gemini_api_key)
    run_polling_loop(
        client=client,
        sessions=sessions,
        bouncer=bouncer,
        interviewer=interviewer,
    )


if __name__ == "__main__":
    main()
