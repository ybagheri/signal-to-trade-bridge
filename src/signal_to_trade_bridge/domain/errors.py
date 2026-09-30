"""Domain errors.

Every error the bridge raises on purpose is defined here, so that a caller can
distinguish "the bridge refused this trade for a stated reason" from "something
crashed". A bare ``ValueError`` from three layers down is not an answer a
trading system can act on.

The hierarchy is shallow on purpose. Two root classes are enough:

* :class:`ConfigurationError` -- the bridge is set up wrongly. Nothing can be
  trusted until it is fixed.
* :class:`TradingError` -- a specific trade was refused or failed. The bridge is
  fine; this one decision was not.

The distinction matters operationally. A configuration error should stop the
process; a refused trade should be logged and the next signal processed.
"""

from __future__ import annotations

__all__ = [
    "AutoTradeBridgeError",
    "ConfigurationError",
    "ExecutionFailedError",
    "ExecutionRejectedError",
    "ExecutionUnknownError",
    "InsufficientMarketDataError",
    "IntegrationError",
    "InvalidRiskParametersError",
    "InvalidSignalError",
    "InvalidStopLossError",
    "InvalidTakeProfitError",
    "InvalidVolumeError",
    "TradingError",
]


class AutoTradeBridgeError(Exception):
    """Base exception for the bridge.

    Every error below derives from this, so a caller embedding the bridge can
    catch one type and know nothing escaped that was not deliberate.
    """


class ConfigurationError(AutoTradeBridgeError):
    """The bridge is misconfigured, so its behaviour cannot be trusted.

    Raised for an unparsable value, a nonsensical combination, or a missing
    required setting. This is an operator problem, not a market problem, and it
    is not something the bridge can work around by refusing to trade.
    """


class TradingError(AutoTradeBridgeError):
    """A specific trade could not be produced, validated or executed.

    The base for every refusal that is about one decision rather than about the
    whole process.
    """


class IntegrationError(TradingError):
    """An external system could not be reached or answered unintelligibly.

    The upstream project is unavailable, returned something unparsable, or is in
    a state the bridge cannot work with. Distinct from a refused trade: this
    says nothing about whether the trade was a good idea, only that the bridge
    could not find out.
    """


class InvalidSignalError(TradingError):
    """The incoming signal is missing something or contradicts itself.

    Covers a malformed payload, an unrecognised direction, a non-positive price,
    and a signal that cannot be identified well enough to deduplicate.
    """


class InvalidRiskParametersError(TradingError):
    """The configured risk parameters are not usable.

    A negative percentage, a ratio of zero, a missing account balance, or a
    balance in a currency the symbol specification does not match. This is
    usually a configuration problem, but it is scoped to a trade because the
    parameters can vary per profile.
    """


class InvalidStopLossError(TradingError):
    """There is no usable stop loss, or the one supplied is wrong.

    The two cases a trader cares about are a missing stop and a stop on the wrong
    side of entry. The second is caught here because the execution layer does not
    check it and would hand it to the broker.
    """


class InvalidTakeProfitError(TradingError):
    """The take profit is missing, or on the wrong side of entry."""


class InsufficientMarketDataError(TradingError):
    """The account or symbol facts needed to size a trade are unavailable.

    Without an account balance and a symbol contract specification there is no
    way to compute a volume, so there is no way to trade. This is the error that
    fires when MetaTrader 5 is not running, or when a symbol is not loaded in the
    terminal.
    """


class InvalidVolumeError(TradingError):
    """The computed volume cannot be sent to the broker.

    Below the broker minimum, above the maximum, not a multiple of the volume
    step, or non-positive. The below-minimum case is a refusal by design: a
    volume floored up to the broker minimum would risk far more than the
    configured budget allows.
    """


class ExecutionRejectedError(TradingError):
    """The execution layer refused the order.

    A gate refused it, or the broker did. The order was not placed.
    """


class ExecutionFailedError(TradingError):
    """Execution was attempted and is known to have failed."""


class ExecutionUnknownError(TradingError):
    """Execution was attempted and the outcome could not be determined.

    **Never retry this automatically.** The final control may have been used, so
    a retry may open a second position. This is the single most dangerous state
    in the whole system, and it is why the upstream project distinguishes it
    from a refusal rather than folding the two together.
    """
