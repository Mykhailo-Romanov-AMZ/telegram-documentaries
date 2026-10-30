"""Telegram long-polling gateway and Bouncer + Interviewer + Converter + Scripter orchestrator.

Routes incoming updates through the vision gate, interview, converter, and scripter stages.
"""

from telegram_documentaries.bouncer import DEFAULT_REJECTION_MESSAGE, Bouncer, BouncerError
from telegram_documentaries.converter import Converter, ConverterError
from telegram_documentaries.interviewer import (
    Interviewer,
    InterviewerError,
)
from telegram_documentaries.logging_utils import log_stage, logger
from telegram_documentaries.models import (
    BehavioralDossier,
    BouncerVerdict,
    InterviewerTurnResult,
    InterviewTurn,
    TelegramMessage,
    TelegramPhotoSize,
    TelegramUpdate,
)
from telegram_documentaries.scripter import Scripter, ScripterError
from telegram_documentaries.session import SessionPhase, SessionStore
from telegram_documentaries.telegram_client import TelegramClient, TelegramClientError

AWAITING_PHOTO_NUDGE = (
    "Easy there, mate — I need a portrait photo of a human before the documentary begins. "
    "Send one through and I'll take a look."
)
ERROR_FALLBACK_MESSAGE = "My binoculars fogged up — send that photo again?"
INTERVIEW_ERROR_FALLBACK_MESSAGE = (
    "My field notebook got rained on — could you repeat that answer, mate?"
)
CONVERTER_ERROR_FALLBACK_MESSAGE = (
    "My darkroom chemicals fizzled out — send any message to retry developing your portrait, "
    "or /restart to begin anew!"
)
SCRIPTER_ERROR_FALLBACK_MESSAGE = (
    "My typewriter ribbon snapped mid-narration — send any message to retry your "
    "documentary script, or /restart to begin anew!"
)
INTERVIEW_COMPLETE_NUDGE = (
    "Field research is already wrapped up, mate! Send /restart if you'd like to begin a "
    "fresh documentary."
)
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


@log_stage("interviewer")
def _start_interviewer(interviewer: Interviewer, portrait: bytes, chat_id: int) -> str:
    """Run Interviewer.start_interview with chat_id context for structured logging."""
    return interviewer.start_interview(portrait)


@log_stage("interviewer")
def _advance_interviewer(
    interviewer: Interviewer,
    history: list[InterviewTurn],
    chat_id: int,
) -> InterviewerTurnResult:
    """Run Interviewer.advance_interview with chat_id context for structured logging."""
    return interviewer.advance_interview(history)


@log_stage("converter")
def _generate_hybrid_portrait(
    converter: Converter,
    portrait: bytes,
    dossier: BehavioralDossier,
    chat_id: int,
) -> bytes:
    """Run Converter.generate_hybrid_portrait with chat_id context for structured logging."""
    return converter.generate_hybrid_portrait(portrait, dossier)


@log_stage("scripter")
def _generate_script(
    scripter: Scripter,
    dossier: BehavioralDossier,
    chat_id: int,
) -> str:
    """Run Scripter.generate_script with chat_id context for structured logging."""
    return scripter.generate_script(dossier)


def _deliver_script(
    chat_id: int,
    dossier: BehavioralDossier,
    client: TelegramClient,
    sessions: SessionStore,
    scripter: Scripter,
) -> None:
    """Generate and send the documentary narration script, or send fallback preserving portrait."""
    try:
        script = _generate_script(scripter=scripter, dossier=dossier, chat_id=chat_id)
        client.send_message(chat_id=chat_id, text=script)
    except (ScripterError, TelegramClientError) as exc:
        logger.exception(
            "event=scripter_failed stage=gateway chat_id=%s error=%s",
            chat_id,
            exc,
        )
        try:
            client.send_message(chat_id=chat_id, text=SCRIPTER_ERROR_FALLBACK_MESSAGE)
        except TelegramClientError as fallback_exc:
            logger.exception(
                "event=scripter_fallback_failed stage=gateway chat_id=%s error=%s",
                chat_id,
                fallback_exc,
            )
        return

    sessions.set_script(chat_id=chat_id, script=script)


def _deliver_hybrid_portrait(
    chat_id: int,
    portrait: bytes,
    dossier: BehavioralDossier,
    client: TelegramClient,
    sessions: SessionStore,
    converter: Converter,
    scripter: Scripter,
) -> None:
    """Synthesize and send the hybrid animal portrait, then deliver the narration script."""
    try:
        hybrid_bytes = _generate_hybrid_portrait(
            converter=converter,
            portrait=portrait,
            dossier=dossier,
            chat_id=chat_id,
        )
        client.send_photo(chat_id=chat_id, photo_bytes=hybrid_bytes)
    except (ConverterError, TelegramClientError) as exc:
        logger.exception(
            "event=converter_failed stage=gateway chat_id=%s error=%s",
            chat_id,
            exc,
        )
        client.send_message(chat_id=chat_id, text=CONVERTER_ERROR_FALLBACK_MESSAGE)
        return

    sessions.set_hybrid_portrait(chat_id=chat_id, hybrid_portrait=hybrid_bytes)
    _deliver_script(
        chat_id=chat_id,
        dossier=dossier,
        client=client,
        sessions=sessions,
        scripter=scripter,
    )


def _handle_photo(
    message: TelegramMessage,
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
    interviewer: Interviewer,
) -> None:
    """Download the largest photo, run the Bouncer, and start the interview when human."""
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
        try:
            first_question = _start_interviewer(
                interviewer=interviewer,
                portrait=portrait,
                chat_id=chat_id,
            )
        except InterviewerError as exc:
            logger.exception(
                "event=interview_start_failed stage=gateway chat_id=%s error=%s",
                chat_id,
                exc,
            )
            sessions.reset(chat_id=chat_id)
            client.send_message(chat_id=chat_id, text=ERROR_FALLBACK_MESSAGE)
            return

        sessions.start_interview(
            chat_id=chat_id,
            portrait=portrait,
            first_question=first_question,
        )
        client.send_message(chat_id=chat_id, text=first_question)
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


def _handle_interview_text(
    chat_id: int,
    text: str,
    client: TelegramClient,
    sessions: SessionStore,
    interviewer: Interviewer,
    converter: Converter,
    scripter: Scripter,
) -> None:
    """Process one user text reply during the INTERVIEWING phase."""
    state = sessions.get(chat_id)
    current_question = state.current_question
    portrait = state.portrait
    if current_question is None or portrait is None:
        sessions.reset(chat_id=chat_id)
        client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
        return

    cleaned_answer = text.strip()
    if not cleaned_answer:
        client.send_message(chat_id=chat_id, text=current_question)
        return

    pending_history = [
        *state.interview_history,
        InterviewTurn(question=current_question, answer=cleaned_answer),
    ]
    try:
        turn_result = _advance_interviewer(
            interviewer=interviewer,
            history=pending_history,
            chat_id=chat_id,
        )
    except InterviewerError as exc:
        logger.exception(
            "event=interview_turn_failed stage=gateway chat_id=%s error=%s",
            chat_id,
            exc,
        )
        client.send_message(chat_id=chat_id, text=INTERVIEW_ERROR_FALLBACK_MESSAGE)
        return

    if turn_result.is_complete and turn_result.dossier is not None:
        sessions.complete_interview(
            chat_id=chat_id,
            answer=cleaned_answer,
            dossier=turn_result.dossier,
        )
        _deliver_hybrid_portrait(
            chat_id=chat_id,
            portrait=portrait,
            dossier=turn_result.dossier,
            client=client,
            sessions=sessions,
            converter=converter,
            scripter=scripter,
        )
        return

    sessions.record_answer(
        chat_id=chat_id,
        answer=cleaned_answer,
        next_question=turn_result.next_question,
    )
    client.send_message(chat_id=chat_id, text=turn_result.next_question)


def _handle_generating_media_text(
    chat_id: int,
    client: TelegramClient,
    sessions: SessionStore,
    converter: Converter,
    scripter: Scripter,
) -> None:
    """Handle retries in GENERATING_MEDIA based on whether portrait/script are present."""
    state = sessions.get(chat_id)
    if state.hybrid_portrait is None and state.portrait is not None and state.dossier is not None:
        _deliver_hybrid_portrait(
            chat_id=chat_id,
            portrait=state.portrait,
            dossier=state.dossier,
            client=client,
            sessions=sessions,
            converter=converter,
            scripter=scripter,
        )
        return

    if state.hybrid_portrait is not None and state.script is None and state.dossier is not None:
        _deliver_script(
            chat_id=chat_id,
            dossier=state.dossier,
            client=client,
            sessions=sessions,
            scripter=scripter,
        )
        return

    client.send_message(chat_id=chat_id, text=INTERVIEW_COMPLETE_NUDGE)


@log_stage("gateway")
def process_update(
    update: TelegramUpdate,
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
    interviewer: Interviewer,
    converter: Converter,
    scripter: Scripter,
) -> None:
    """Route one incoming Telegram update through the vision gate and interview state machine."""
    message = update.message
    if message is None:
        logger.debug(
            "event=update_ignored stage=gateway update_id=%s reason=no_message",
            update.update_id,
        )
        return

    chat_id = message.chat.id

    if message.photo:
        _handle_photo(
            message=message,
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
        )
        return

    if message.text is not None:
        if _is_reset_command(message.text):
            sessions.reset(chat_id=chat_id)
            client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
            return

        phase = sessions.get(chat_id).phase
        if phase == SessionPhase.AWAITING_PHOTO:
            client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
        elif phase == SessionPhase.INTERVIEWING:
            _handle_interview_text(
                chat_id=chat_id,
                text=message.text,
                client=client,
                sessions=sessions,
                interviewer=interviewer,
                converter=converter,
                scripter=scripter,
            )
        else:
            _handle_generating_media_text(
                chat_id=chat_id,
                client=client,
                sessions=sessions,
                converter=converter,
                scripter=scripter,
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
    interviewer: Interviewer,
    converter: Converter,
    scripter: Scripter,
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
            process_update(
                update=update,
                client=client,
                sessions=sessions,
                bouncer=bouncer,
                interviewer=interviewer,
                converter=converter,
                scripter=scripter,
            )
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
    interviewer: Interviewer,
    converter: Converter,
    scripter: Scripter,
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
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            offset=offset,
            timeout=timeout,
        )
        iteration += 1
