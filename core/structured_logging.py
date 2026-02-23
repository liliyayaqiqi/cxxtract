"""Structured logging helpers with run correlation context."""

from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager
from typing import Iterator

_RUN_ID_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "run_id", default="-"
)
_PHASE_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "phase", default="-"
)


class _RunContextFilter(logging.Filter):
    """Inject run correlation fields into all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _RUN_ID_VAR.get("-")
        record.phase = _PHASE_VAR.get("-")
        return True


def _ensure_filter_on_root_handlers() -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        has_filter = any(isinstance(f, _RunContextFilter) for f in handler.filters)
        if not has_filter:
            handler.addFilter(_RunContextFilter())


def configure_structured_logging(level: int = logging.INFO) -> None:
    """Configure root logging format with run/phase context."""
    fmt = (
        "%(asctime)s | %(levelname)s | run_id=%(run_id)s | phase=%(phase)s | "
        "%(name)s | %(message)s"
    )
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format=fmt)
    else:
        root_logger.setLevel(level)
        formatter = logging.Formatter(fmt)
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
    _ensure_filter_on_root_handlers()


def set_run_id(run_id: str | None = None) -> str:
    """Set or generate run correlation ID."""
    value = run_id or str(uuid.uuid4())
    _RUN_ID_VAR.set(value)
    return value


def get_run_id() -> str:
    """Get current run correlation ID."""
    return _RUN_ID_VAR.get("-")


@contextmanager
def phase_scope(phase: str) -> Iterator[None]:
    """Temporarily set phase context for emitted logs."""
    token = _PHASE_VAR.set(phase)
    try:
        yield
    finally:
        _PHASE_VAR.reset(token)
