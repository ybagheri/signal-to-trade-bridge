"""Structured logging.

Every event the bridge emits is a dictionary with an ``event`` name and a stable
set of fields, not a sentence. A log line that says what happened *and* carries
the numbers that explain it is the difference between debugging a decision in
minutes and reconstructing it from memory.

The event names live in :mod:`events` as a single ``Event`` enum rather than as
loose module-level constants. That way a call site writes
``log.event(Event.TRADE_REJECTED, ...)`` and cannot invent a name, and a typo
fails an import instead of creating a log line nobody ever searches for.
"""

from signal_to_trade_bridge.infrastructure.logging.events import Event, all_events
from signal_to_trade_bridge.infrastructure.logging.structured import (
    REDACTED,
    SENSITIVE_KEYS,
    StructuredLogger,
    configure_logging,
    get_logger,
    redact,
)

__all__ = [
    "REDACTED",
    "SENSITIVE_KEYS",
    "Event",
    "StructuredLogger",
    "all_events",
    "configure_logging",
    "get_logger",
    "redact",
]
