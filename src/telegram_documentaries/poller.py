"""Telegram long-polling gateway and update processor."""

from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import TelegramUpdate
from telegram_documentaries.telegram_client import TelegramClient

HARDCODED_REPLY = "hey mate!"


@log_stage("gateway")
def process_update(update: TelegramUpdate, client: TelegramClient) -> None:
    """Process a single incoming Telegram update and reply 'hey mate!' if it has a message."""
    if update.message is None:
        return

    client.send_message(
        chat_id=update.message.chat.id,
        text=HARDCODED_REPLY,
    )


@log_stage("polling")
def poll_once(
    client: TelegramClient,
    offset: int | None = None,
    timeout: int = 30,
) -> int | None:
    """Run one resilient polling cycle, catching and logging transient errors."""
    next_offset = offset
    try:
        updates = client.get_updates(offset=offset, timeout=timeout)
    except Exception as exc:
        logger.exception(
            "event=poll_cycle_failed stage=polling offset=%s error=%s",
            next_offset,
            exc,
        )
        return next_offset

    for update in updates:
        try:
            process_update(update=update, client=client)
        except Exception as exc:
            logger.exception(
                "event=update_processing_failed stage=polling update_id=%s error=%s",
                update.update_id,
                exc,
            )
        next_offset = update.update_id + 1
    return next_offset


def run_polling_loop(
    client: TelegramClient,
    timeout: int = 30,
    max_iterations: int | None = None,
) -> None:
    """Continuously poll Telegram for updates until interrupted or max_iterations reached."""
    logger.info("Telegram polling loop started...")
    offset: int | None = None
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        offset = poll_once(client=client, offset=offset, timeout=timeout)
        iteration += 1
