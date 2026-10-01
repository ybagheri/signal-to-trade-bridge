"""Direction, action and the reasons a trade was refused.

These are the bridge's own controlled vocabularies. They deliberately do **not**
reuse either upstream project's enums, because doing so would couple the domain
to both of them and defeat the anti-corruption layer. The adapters map at the
boundary instead.

The distinction the upstream engine draws between ``WAIT`` and ``NO_TRADE`` is
preserved here as :attr:`SignalAction.WAIT` versus
:attr:`SignalAction.NO_TRADE`, because they are different claims: an abstention
versus a condition that was evaluated and not met. Both are no-trade, and the
bridge treats them identically in effect, but collapsing them would lose the
ability to explain afterwards why nothing happened.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "DecisionAction",
    "Direction",
    "RejectionReason",
    "SignalAction",
    "StopSource",
    "TakeProfitSource",
]


class Direction(StrEnum):
    """Which way a trade goes.

    ``FLAT`` exists as an explicit member rather than being represented by
    ``NONE``, because a missing direction and a deliberate no-trade are different
    inputs and the adapter must be able to tell them apart: a missing direction is
    a malformed signal, a deliberate one is a valid abstention.
    """

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def is_tradable(self) -> bool:
        """Whether this direction names a trade rather than an abstention."""
        return self in (Direction.LONG, Direction.SHORT)

    @property
    def sign(self) -> int:
        """``+1`` long, ``-1`` short, ``0`` flat.

        The sign convention matches the upstream price-action engine, which uses
        ``+1``/``-1`` throughout, so a conversion at the adapter boundary is a
        cast rather than a negation.
        """
        if self is Direction.LONG:
            return 1
        if self is Direction.SHORT:
            return -1
        return 0

    def inverted(self) -> Direction:
        """The opposite direction, or ``FLAT`` for ``FLAT``."""
        if self is Direction.LONG:
            return Direction.SHORT
        if self is Direction.SHORT:
            return Direction.LONG
        return Direction.FLAT

    @classmethod
    def from_sign(cls, sign: int) -> Direction:
        """Build a direction from the engine's ``+1``/``-1``/``0`` convention.

        Any non-zero magnitude normalises to its sign, matching ``TradePlan``,
        which stores the normalised sign rather than whatever a detector
        reported. A magnitude of zero is ``FLAT``; it is not an error, because
        "the setup named no direction" is a real and common reading.
        """
        return cls.LONG if sign > 0 else cls.SHORT if sign < 0 else cls.FLAT


class SignalAction(StrEnum):
    """What the signal source is asking for.

    Mirrors the four answers the upstream engine names, with the distinction
    between the two abstentions kept.
    """

    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"

    @property
    def is_tradable(self) -> bool:
        """Whether this action asks for a trade."""
        return self in (SignalAction.BUY, SignalAction.SELL)

    @property
    def direction(self) -> Direction:
        """The direction implied by this action.

        Both abstentions are ``FLAT``: they ask for no trade, and treating them
        as directional would be the first step towards trading something nobody
        asked for.
        """
        if self is SignalAction.BUY:
            return Direction.LONG
        if self is SignalAction.SELL:
            return Direction.SHORT
        return Direction.FLAT


class DecisionAction(StrEnum):
    """What the bridge decided to do.

    Note the asymmetry with :class:`SignalAction`: the engine can say ``WAIT``,
    because it is describing a market condition. The bridge cannot. From the
    bridge's point of view a decision is either an order or a refusal, and the
    reason a refusal happened is a separate field. Collapsing the two would mean
    a caller had to parse a reason code to learn whether a trade was placed.
    """

    EXECUTE = "EXECUTE"
    DRY_RUN = "DRY_RUN"
    NO_TRADE = "NO_TRADE"

    @property
    def is_tradable(self) -> bool:
        """Whether this decision results in an order reaching the executor."""
        return self is DecisionAction.EXECUTE


class StopSource(StrEnum):
    """Where the stop loss came from.

    Recorded rather than inferred, because "the stop was a volatility fallback
    and nobody was told" is exactly the kind of thing that has to be visible in
    a log six months later.
    """

    #: A structural level the price action produced. The only source trusted by
    #: default.
    SIGNAL = "SIGNAL"
    #: The signal supplied a stop, but it was a volatility multiple rather than a
    #: level the market produced. Permitted only when explicitly configured.
    SIGNAL_VOLATILITY_FALLBACK = "SIGNAL_VOLATILITY_FALLBACK"
    #: Computed from the risk distance and the configured reward:risk ratio.
    RR_DERIVED = "RR_DERIVED"
    #: There is no stop. The trade is refused. There is no code path that
    #: invents one.
    NONE = "NONE"


class TakeProfitSource(StrEnum):
    """Where the take profit came from."""

    #: The signal's own target, used because the policy says to prefer it.
    SIGNAL = "SIGNAL"
    #: The signal's target was unusable, so the reward:risk policy was applied
    #: instead. Recorded explicitly so a fallback is never silent.
    RR_FALLBACK = "RR_FALLBACK"
    #: The policy ignores signal targets entirely.
    RR_DERIVED = "RR_DERIVED"
    NONE = "NONE"


class RejectionReason(StrEnum):
    """Why a trade was refused.

    Stable, machine-comparable codes. The brief requires that every refusal carry
    one, because a refusal expressed only as log prose cannot be counted, alerted
    on, or tested for.

    These are the *bridge's* reasons. The upstream engine has its own vocabulary
    describing the reading, and the two are kept separate on purpose: an engine
    veto says the setup was weak, a bridge reason says no order was produced.
    """

    # -- signal problems -------------------------------------------------
    SIGNAL_INVALID = "SIGNAL_INVALID"
    SIGNAL_MALFORMED = "SIGNAL_MALFORMED"
    SIGNAL_DIRECTION_UNKNOWN = "SIGNAL_DIRECTION_UNKNOWN"
    SIGNAL_SYMBOL_INVALID = "SIGNAL_SYMBOL_INVALID"
    SIGNAL_ENTRY_INVALID = "SIGNAL_ENTRY_INVALID"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"

    # -- stop loss -------------------------------------------------------
    NO_VALID_STOP = "NO_VALID_STOP"
    INVALID_STOP_DISTANCE = "INVALID_STOP_DISTANCE"
    STOP_ON_WRONG_SIDE = "STOP_ON_WRONG_SIDE"
    STRUCTURAL_STOP_REQUIRED = "STRUCTURAL_STOP_REQUIRED"

    # -- take profit -----------------------------------------------------
    INVALID_TAKE_PROFIT = "INVALID_TAKE_PROFIT"
    TAKE_PROFIT_ON_WRONG_SIDE = "TAKE_PROFIT_ON_WRONG_SIDE"

    # -- market data -----------------------------------------------------
    MARKET_DATA_UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"
    ACCOUNT_BALANCE_UNAVAILABLE = "ACCOUNT_BALANCE_UNAVAILABLE"
    SYMBOL_SPEC_UNAVAILABLE = "SYMBOL_SPEC_UNAVAILABLE"

    # -- sizing ----------------------------------------------------------
    INVALID_RISK_PARAMETERS = "INVALID_RISK_PARAMETERS"
    SIZING_FAILED = "SIZING_FAILED"
    INVALID_VOLUME = "INVALID_VOLUME"
    VOLUME_BELOW_BROKER_MINIMUM = "VOLUME_BELOW_BROKER_MINIMUM"
    VOLUME_ABOVE_BROKER_MAXIMUM = "VOLUME_ABOVE_BROKER_MAXIMUM"
    VOLUME_NOT_ON_STEP = "VOLUME_NOT_ON_STEP"

    # -- policy ----------------------------------------------------------
    DIRECTION_NOT_ALLOWED = "DIRECTION_NOT_ALLOWED"
    EVIDENCE_BELOW_MINIMUM = "EVIDENCE_BELOW_MINIMUM"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    SYMBOL_NOT_TRADEABLE = "SYMBOL_NOT_TRADEABLE"

    SYMBOL_NOT_ALLOWED = "SYMBOL_NOT_ALLOWED"

    # -- idempotency and safety ------------------------------------------
    #: This signal id is already in the idempotency ledger. Phase 9 made this
    #: reachable from the pipeline; the same vocabulary upstream uses
    #: ("duplicate signal id"), so the bridge and the execution project now agree
    #: about *why* rather than one permitting what the other forbids.
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    #: A deliberate stop, not a fault. Its own code because an operator reading a
    #: log needs to tell "we chose not to" from "we could not".
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    EXECUTION_DISABLED = "EXECUTION_DISABLED"
    MAX_CONCURRENT_POSITIONS = "MAX_CONCURRENT_POSITIONS"

    # -- execution -------------------------------------------------------
    EXECUTION_REJECTED = "EXECUTION_REJECTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"
