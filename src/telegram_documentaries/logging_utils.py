"""Structured logging utilities and stage decorators."""

import functools
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger("telegram_documentaries")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure application-wide structured logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logger.setLevel(level)


def _extract_chat_id(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> int | None:
    """Attempt to extract chat_id from bound function arguments or nested update objects."""
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        arguments = bound.arguments
    except TypeError:
        arguments = kwargs

    if "chat_id" in arguments and isinstance(arguments["chat_id"], int):
        return arguments["chat_id"]

    update = arguments.get("update")
    if update is not None:
        message = getattr(update, "message", None)
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        if isinstance(chat_id, int):
            return chat_id

    return None


def log_stage(stage_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that logs function entry, completion, duration, chat_id, and exceptions."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            chat_id = _extract_chat_id(func, args, kwargs)
            chat_context = f" chat_id={chat_id}" if chat_id is not None else ""
            start_time = time.perf_counter()
            logger.info(
                "event=stage_start stage=%s func=%s%s",
                stage_name,
                func.__name__,
                chat_context,
            )
            try:
                result = func(*args, **kwargs)
            except Exception:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                logger.exception(
                    "event=stage_error stage=%s func=%s%s duration_ms=%.2f",
                    stage_name,
                    func.__name__,
                    chat_context,
                    elapsed_ms,
                )
                raise

            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.info(
                "event=stage_complete stage=%s func=%s%s duration_ms=%.2f",
                stage_name,
                func.__name__,
                chat_context,
                elapsed_ms,
            )
            return result

        return wrapper

    return decorator
