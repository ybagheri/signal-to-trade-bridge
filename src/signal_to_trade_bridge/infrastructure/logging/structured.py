"""The logging implementation.

Thin on purpose. The standard library's :mod:`logging` does the work; this module
exists to add three things it does not:

* a fixed set of fields on every event, so a log line is queryable rather than
  merely readable
* a JSON formatter, because a decision that has to be reconstructed later is
  easier to reconstruct from structured data than from prose
* a redaction step, so that a field which should never be logged cannot be logged
  by accident
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Final

__all__ = [
    "REDACTED",
    "SENSITIVE_KEYS",
    "StructuredLogger",
    "configure_logging",
    "get_logger",
]

#: The logger name the bridge emits under. Namespaced so a host application's
#: logging configuration can silence or redirect the bridge without touching
#: anything else.
LOGGER_NAME: Final = "signal_to_trade_bridge"

#: Substituted for any value whose key matches, so a credential cannot reach a
#: log file even if a caller passes one in.
REDACTED: Final = "***redacted***"

#: Keys whose values are never written to a log, matched case-insensitively as
#: substrings. Deliberately broad. A key that merely *contains* ``token`` is
#: redacted, because the cost of redacting a field that was harmless is a slightly
#: less informative log line, and the cost of not redacting one is a leaked
#: credential in a file somebody will eventually paste into a bug report.
SENSITIVE_KEYS: Final = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "credential",
        "auth",
        "authorization",
        "bearer",
        "session_id",
        "license",
    }
)

#: Every event carries these, whatever else it carries. A reader should be able
#: to reconstruct the chain from signal to order using only these plus the fields
#: the individual events define.
COMMON_FIELDS: Final = ("signal_id", "symbol", "action", "reason")


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEYS)


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    """Replace sensitive values with :data:`REDACTED`.

    Applied by the logger, not by the callers. A caller who remembers to redact is
    a caller who will eventually forget, so the redaction has to be somewhere that
    cannot be skipped.
    """
    return {key: (REDACTED if _is_sensitive(key) else value) for key, value in fields.items()}


class _JsonFormatter(logging.Formatter):
    """Render a record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = str(event)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(redact(dict(fields)))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)


class _TextFormatter(logging.Formatter):
    """Render a record as a readable line with the fields appended.

    Human-readable, but not at the cost of the fields: they are appended
    ``key=value`` so nothing is lost, and a person reading a terminal can still
    see the numbers rather than having to open a JSON viewer.
    """

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        event = getattr(record, "event", None)
        head = f"{stamp} {record.levelname:<8} {event or record.name}"
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            rendered = " ".join(
                f"{key}={value}" for key, value in sorted(redact(dict(fields)).items())
            )
            head = f"{head} {rendered}"
        if record.exc_info:
            head = f"{head}\n{self.formatException(record.exc_info)}"
        return head


class StructuredLogger:
    """A thin wrapper that gives every call site the same shape.

    Wrapping rather than using :mod:`logging` directly is what makes the event
    vocabulary enforceable: a caller writes ``log.event(Event.TRADE_REJECTED,
    reason=...)`` and cannot invent an event name, and cannot bypass the redaction
    because the formatter applies it.
    """

    __slots__ = ("_logger",)

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(LOGGER_NAME)

    def event(self, event: Any, /, **fields: Any) -> None:
        """Emit one structured event at INFO."""
        self._emit(logging.INFO, event, fields)

    def debug(self, event: Any, /, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields)

    def warning(self, event: Any, /, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: Any, /, **fields: Any) -> None:
        self._emit(logging.ERROR, event, fields)

    def _emit(self, level: int, event: Any, fields: dict[str, Any]) -> None:
        if not self._logger.isEnabledFor(level):
            return
        self._logger.log(level, str(event), extra={"event": event, "fields": fields})


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    stream: Any | None = None,
) -> logging.Logger:
    """Install a single handler on the bridge's logger and return it.

    Replaces any handler it previously installed, so calling this twice does not
    produce doubled log lines. That matters because a test calls it for each case
    and a duplicated handler would make output-based assertions fail for a reason
    that has nothing to do with the code under test.

    Propagation is disabled. The bridge's events are its own; letting them bubble
    into a host application's root handler would put trading decisions into
    whatever unrelated log file that application happens to write.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(_JsonFormatter() if json_output else _TextFormatter())
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> StructuredLogger:
    """Get a structured logger.

    ``name`` becomes a child of the bridge's logger, so a submodule can be given
    its own level without any of the configuration leaking elsewhere.
    """
    if name is None:
        return StructuredLogger()
    return StructuredLogger(logging.getLogger(f"{LOGGER_NAME}.{name}"))
