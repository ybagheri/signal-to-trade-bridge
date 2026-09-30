"""The event vocabulary.

Each name below is a ``StrEnum`` member, so the values are the strings themselves
and a test can assert on either. The reason for an enum rather than bare
constants is that a typo becomes a test failure instead of a log line nobody ever
searches for.

The names describe *what the bridge did*, never *what it inferred about the
market*. ``SIGNAL_RECEIVED`` is a fact about the process. ``TRADE_REJECTED``
carries a reason code that names a check that failed. Neither says the market was
weak, because that is the upstream engine's claim to make, and a log line that
blurred the two would make a later audit impossible.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Event", "all_events"]


class Event(StrEnum):
    """Every event the bridge can emit."""

    #: A signal arrived and was normalised. Carries the full identity.
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    #: A signal could not be read at all -- the source was unreachable or
    #: returned something unparsable. Distinct from ``SIGNAL_REJECTED``, which
    #: means the signal was read and then refused.
    SIGNAL_SOURCE_UNAVAILABLE = "SIGNAL_SOURCE_UNAVAILABLE"
    #: A readable signal was refused before any arithmetic.
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    #: The stop loss was resolved, and where it came from. Always emitted, even
    #: when the resolution was a refusal, because "no stop" is the single most
    #: important thing this bridge can report.
    STOP_RESOLVED = "STOP_RESOLVED"
    #: The take profit was resolved, and whether it came from the signal or from
    #: the ratio. A fallback is never silent.
    TAKE_PROFIT_RESOLVED = "TAKE_PROFIT_RESOLVED"
    #: The risk amount was computed from the balance and the configured
    #: percentage.
    RISK_CALCULATED = "RISK_CALCULATED"
    #: The position size was computed, with the full arithmetic retained.
    POSITION_SIZED = "POSITION_SIZED"
    #: Every check passed and the trade is ready to send.
    TRADE_VALIDATED = "TRADE_VALIDATED"
    #: A check failed. Carries the reason code.
    TRADE_REJECTED = "TRADE_REJECTED"
    #: The full decision pipeline ran and the executor was deliberately not
    #: reached.
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    #: The execution layer reported back, with its status mapped.
    EXECUTION_RESULT = "EXECUTION_RESULT"
    #: A signal was recognised as already acted on and not re-sent.
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    #: The kill switch is engaged, so nothing will be sent regardless of
    #: configuration.
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"


def all_events() -> tuple[str, ...]:
    """Every event name, for a test that asserts the vocabulary is complete."""
    return tuple(member.value for member in Event)
