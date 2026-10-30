"""Telegram long-polling gateway and Bouncer-aware update processor."""

from telegram_documentaries.bouncer import DEFAULT_REJECTION_MESSAGE, Bouncer, BouncerError
from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import (
    BouncerVerdict,
    TelegramMessage,
    TelegramPhotoSize,
    TelegramUpdate,
)
from telegram_documentaries.session import SessionPhase, SessionStore
from telegram_documentaries.telegram_client import TelegramClient, TelegramClientError

HARDCODED_REPLY = "hey mate!"
AWAITING_PHOTO_NUDGE = (
    "Easy there, mate — I need a portrait photo of a human before the documentary begins. "
    "Send one through and I'll take a look."
)
ERROR_FALLBACK_MESSAGE = "My binoculars fogged up — send that photo again?"
_RESET_COMMANDS = frozenset({"/start", "/restart"})


def _is_reset_command(text: str) -> bool:
    """Return True when text is a /start or /restart bot command (with optional @bot suffix)."""
    command_token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    base_command = command_token.partition("@")[0].lower()
    return base_command in _RESET_COMMANDS


def _largest_photo(photo: list[TelegramPhotoSize]) -> TelegramPhotoSize:
    """Pick the highest-resolution variant Telegram offered for a photo."""
    return max(photo, key=lambda size: size.width * size.height)


@log_stage("bouncer")
def _evaluate_bouncer(bouncer: Bouncer, portrait: bytes, chat_id: int) -> BouncerVerdict:
    """Run the Bouncer with chat_id context so its stage logs are attributable to a chat."""
    return bouncer.evaluate(portrait)


def _handle_photo(
    message: TelegramMessage,
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
) -> None:
    """Download the largest photo, run the Bouncer, and route on its verdict."""
    chat_id = message.chat.id
    photo = _largest_photo(message.photo or [])

    try:
        telegram_file = client.get_file(photo.file_id)
        if not telegram_file.file_path:
            raise TelegramClientError(
                f"Telegram did not return a file_path for file_id={photo.file_id!r}."
            )
        portrait = client.download_file(telegram_file.file_path)
        verdict = _evaluate_bouncer(bouncer=bouncer, portrait=portrait, chat_id=chat_id)
    except (TelegramClientError, BouncerError) as exc:
        logger.exception(
            "event=photo_gate_failed stage=gateway chat_id=%s error=%s",
            chat_id,
            exc,
        )
        client.send_message(chat_id=chat_id, text=ERROR_FALLBACK_MESSAGE)
        return

    if verdict.is_human:
        sessions.set_portrait(chat_id=chat_id, portrait=portrait)
        client.send_message(chat_id=chat_id, text=HARDCODED_REPLY)
        return

    sessions.reset(chat_id=chat_id)
    rejection = verdict.rejection_message.strip()
    if not rejection:
        logger.warning(
            "event=gateway_rejection_fallback stage=gateway chat_id=%s subject=%r",
            chat_id,
            verdict.subject,
        )
        rejection = DEFAULT_REJECTION_MESSAGE
    client.send_message(chat_id=chat_id, text=rejection)


@log_stage("gateway")
def process_update(
    update: TelegramUpdate,
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
) -> None:
    """Route one incoming Telegram update through the vision gate."""
    message = update.message
    if message is None:
        logger.debug(
            "event=update_ignored stage=gateway update_id=%s reason=no_message",
            update.update_id,
        )
        return

    chat_id = message.chat.id

    if message.photo:
        _handle_photo(message=message, client=client, sessions=sessions, bouncer=bouncer)
        return

    if message.text is not None:
        if _is_reset_command(message.text):
            sessions.reset(chat_id=chat_id)
            client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
            return
        if sessions.get(chat_id).phase == SessionPhase.AWAITING_PHOTO:
            client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
        else:
            logger.debug(
                "event=text_ignored stage=gateway chat_id=%s reason=interviewing",
                chat_id,
            )
        return

    logger.debug(
        "event=update_ignored stage=gateway chat_id=%s reason=no_photo_or_text",
        chat_id,
    )


@log_stage("polling")
def poll_once(
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
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
            process_update(update=update, client=client, sessions=sessions, bouncer=bouncer)
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
    sessions: SessionStore,
    bouncer: Bouncer,
    timeout: int = 30,
    max_iterations: int | None = None,
) -> None:
    """Continuously poll Telegram for updates until interrupted or max_iterations reached."""
    logger.info("Telegram polling loop started...")
    offset: int | None = None
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        offset = poll_once(
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            offset=offset,
            timeout=timeout,
        )
        iteration += 1
