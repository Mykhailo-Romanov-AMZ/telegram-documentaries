"""Telegram long-polling gateway and multimodal documentary stage orchestrator.

Routes incoming updates through the vision gate, interview, converter, scripter, and narrator.
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
from telegram_documentaries.narrator import Narrator, NarratorError
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
NARRATOR_ERROR_FALLBACK_MESSAGE = (
    "My field microphone crackled out — send any message to retry recording your voiceover, "
    "or /restart to begin anew!"
)
INTERVIEW_PHOTO_GUARD_MESSAGE = (
    "Hold your horses, mate — we're right in the middle of our field interview! "
    "Reply with a text answer to the current question, or type /restart to start fresh."
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


@log_stage("narrator")
def _synthesize_and_send_voice(
    narrator: Narrator,
    client: TelegramClient,
    sessions: SessionStore,
    script: str,
    chat_id: int,
) -> None:
    """Synthesize speech to a temporary audio file and upload it via sendVoice with chat_id."""
    with narrator.synthesize_to_temp_file(script) as audio_path:
        sessions.register_ephemeral_file(chat_id, audio_path)
        try:
            audio_bytes = audio_path.read_bytes()
            if audio_path.suffix == ".ogg":
                client.send_voice(
                    chat_id=chat_id,
                    audio_bytes=audio_bytes,
                    filename="narration.ogg",
                    mime_type="audio/ogg",
                )
            else:
                client.send_voice(chat_id=chat_id, audio_bytes=audio_bytes)
        finally:
            sessions.cleanup_ephemeral_files(chat_id)


def _deliver_voice_narration(
    chat_id: int,
    script: str,
    client: TelegramClient,
    sessions: SessionStore,
    narrator: Narrator,
) -> None:
    """Synthesize and upload voice narration via a temporary file, then complete the documentary."""
    try:
        _synthesize_and_send_voice(
            narrator=narrator,
            client=client,
            sessions=sessions,
            script=script,
            chat_id=chat_id,
        )
    except (NarratorError, TelegramClientError) as exc:
        logger.exception(
            "event=narrator_failed stage=gateway chat_id=%s error=%s",
            chat_id,
            exc,
        )
        try:
            client.send_message(chat_id=chat_id, text=NARRATOR_ERROR_FALLBACK_MESSAGE)
        except TelegramClientError as fallback_exc:
            logger.exception(
                "event=narrator_fallback_failed stage=gateway chat_id=%s error=%s",
                chat_id,
                fallback_exc,
            )
        return

    sessions.complete_documentary(chat_id=chat_id)


def _deliver_script(
    chat_id: int,
    dossier: BehavioralDossier,
    client: TelegramClient,
    sessions: SessionStore,
    scripter: Scripter,
    narrator: Narrator,
) -> None:
    """Generate and send the documentary narration script, then deliver the voice narration."""
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
    _deliver_voice_narration(
        chat_id=chat_id,
        script=script,
        client=client,
        sessions=sessions,
        narrator=narrator,
    )


def _deliver_hybrid_portrait(
    chat_id: int,
    portrait: bytes,
    dossier: BehavioralDossier,
    client: TelegramClient,
    sessions: SessionStore,
    converter: Converter,
    scripter: Scripter,
    narrator: Narrator,
) -> None:
    """Synthesize and send the hybrid animal portrait, then deliver script and voice narration."""
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
        try:
            client.send_message(chat_id=chat_id, text=CONVERTER_ERROR_FALLBACK_MESSAGE)
        except TelegramClientError as fallback_exc:
            logger.exception(
                "event=converter_fallback_failed stage=gateway chat_id=%s error=%s",
                chat_id,
                fallback_exc,
            )
        return

    sessions.set_hybrid_portrait(chat_id=chat_id, hybrid_portrait=hybrid_bytes)
    _deliver_script(
        chat_id=chat_id,
        dossier=dossier,
        client=client,
        sessions=sessions,
        scripter=scripter,
        narrator=narrator,
    )


def _handle_photo(
    message: TelegramMessage,
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
    interviewer: Interviewer,
    converter: Converter,
    scripter: Scripter,
    narrator: Narrator,
) -> None:
    """Guard non-initial phases or download the largest photo, run Bouncer, and start Q1."""
    chat_id = message.chat.id
    phase = sessions.get(chat_id).phase
    if phase == SessionPhase.INTERVIEWING:
        client.send_message(chat_id=chat_id, text=INTERVIEW_PHOTO_GUARD_MESSAGE)
        return
    if phase == SessionPhase.GENERATING_MEDIA:
        _handle_generating_media_text(
            chat_id=chat_id,
            client=client,
            sessions=sessions,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
        return
    if phase == SessionPhase.COMPLETED:
        client.send_message(chat_id=chat_id, text=INTERVIEW_COMPLETE_NUDGE)
        return

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
        try:
            client.send_message(chat_id=chat_id, text=ERROR_FALLBACK_MESSAGE)
        except TelegramClientError as fallback_exc:
            logger.exception(
                "event=photo_gate_fallback_failed stage=gateway chat_id=%s error=%s",
                chat_id,
                fallback_exc,
            )
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
            try:
                client.send_message(chat_id=chat_id, text=ERROR_FALLBACK_MESSAGE)
            except TelegramClientError as fallback_exc:
                logger.exception(
                    "event=interview_start_fallback_failed stage=gateway chat_id=%s error=%s",
                    chat_id,
                    fallback_exc,
                )
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
    narrator: Narrator,
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
        try:
            client.send_message(chat_id=chat_id, text=INTERVIEW_ERROR_FALLBACK_MESSAGE)
        except TelegramClientError as fallback_exc:
            logger.exception(
                "event=interview_turn_fallback_failed stage=gateway chat_id=%s error=%s",
                chat_id,
                fallback_exc,
            )
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
            narrator=narrator,
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
    narrator: Narrator,
) -> None:
    """Handle stage-aware retries in GENERATING_MEDIA based on stored artifacts."""
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
            narrator=narrator,
        )
        return

    if state.hybrid_portrait is not None and state.script is None and state.dossier is not None:
        _deliver_script(
            chat_id=chat_id,
            dossier=state.dossier,
            client=client,
            sessions=sessions,
            scripter=scripter,
            narrator=narrator,
        )
        return

    if state.hybrid_portrait is not None and state.script is not None:
        _deliver_voice_narration(
            chat_id=chat_id,
            script=state.script,
            client=client,
            sessions=sessions,
            narrator=narrator,
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
    narrator: Narrator,
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
    command_candidate = message.text if message.text is not None else message.caption
    if command_candidate is not None and _is_reset_command(command_candidate):
        sessions.reset(chat_id=chat_id)
        client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
        return

    if message.photo:
        _handle_photo(
            message=message,
            client=client,
            sessions=sessions,
            bouncer=bouncer,
            interviewer=interviewer,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
        return

    if message.text is not None:
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
                narrator=narrator,
            )
        elif phase == SessionPhase.GENERATING_MEDIA:
            _handle_generating_media_text(
                chat_id=chat_id,
                client=client,
                sessions=sessions,
                converter=converter,
                scripter=scripter,
                narrator=narrator,
            )
        else:
            client.send_message(chat_id=chat_id, text=INTERVIEW_COMPLETE_NUDGE)
        return

    phase = sessions.get(chat_id).phase
    logger.info(
        "event=unsupported_payload stage=gateway chat_id=%s phase=%s",
        chat_id,
        phase,
    )
    if phase == SessionPhase.AWAITING_PHOTO:
        client.send_message(chat_id=chat_id, text=AWAITING_PHOTO_NUDGE)
    elif phase == SessionPhase.INTERVIEWING:
        client.send_message(chat_id=chat_id, text=INTERVIEW_PHOTO_GUARD_MESSAGE)
    elif phase == SessionPhase.GENERATING_MEDIA:
        _handle_generating_media_text(
            chat_id=chat_id,
            client=client,
            sessions=sessions,
            converter=converter,
            scripter=scripter,
            narrator=narrator,
        )
    else:
        client.send_message(chat_id=chat_id, text=INTERVIEW_COMPLETE_NUDGE)


@log_stage("polling")
def poll_once(
    client: TelegramClient,
    sessions: SessionStore,
    bouncer: Bouncer,
    interviewer: Interviewer,
    converter: Converter,
    scripter: Scripter,
    narrator: Narrator,
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
                narrator=narrator,
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
    narrator: Narrator,
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
            narrator=narrator,
            offset=offset,
            timeout=timeout,
        )
        iteration += 1
