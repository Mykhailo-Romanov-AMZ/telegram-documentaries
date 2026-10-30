"""CLI entrypoint for The Telegram Documentaries bot."""

from telegram_documentaries.config import load_settings
from telegram_documentaries.logging_utils import configure_logging
from telegram_documentaries.poller import run_polling_loop
from telegram_documentaries.telegram_client import TelegramClient


def main() -> None:
    """Configure logging, validate settings, and start the Telegram long-polling loop."""
    configure_logging()
    settings = load_settings()
    client = TelegramClient(bot_token=settings.telegram_bot_token)
    run_polling_loop(client=client)


if __name__ == "__main__":
    main()
