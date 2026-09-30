"""Trade validation.

Every check that can refuse a trade before it is sized. The theme throughout is
**fail-closed with a reason**: a check that cannot evaluate its inputs refuses
rather than assuming, because a check that passes on missing data is not a check.

The validation is layered rather than a single function, because the checks have
different costs and different consequences:

* :func:`validate_signal` -- the signal itself, before any arithmetic
* :func:`validate_geometry` -- entry, stop and target against each other
* :func:`validate_policy` -- the configured risk and direction rules

A caller runs them in that order and stops at the first refusal, so a bad signal
is rejected on its own merits rather than after a position sizer has divided by
something meaningless.

Each check returns a :class:`~signal_to_trade_bridge.domain.resolution.Resolution`
over its own input type, so the result of a passing check is the value that
passed and the result of a failing one is the reason it did not. That keeps the
"did it pass" and "what did it produce" from ever disagreeing -- a design in which
they were separate return values could report a pass alongside a value from a
different computation.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from signal_to_trade_bridge.domain.enums import Direction, RejectionReason
from signal_to_trade_bridge.domain.models import (
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
)
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved
from signal_to_trade_bridge.domain.stops import is_protective
from signal_to_trade_bridge.domain.take_profit import is_favourable

__all__ = [
    "collect_reasons",
    "validate_against_spec",
    "validate_geometry",
    "validate_policy",
    "validate_signal",
]


def validate_signal(signal: Signal, risk: RiskParameters) -> Resolution[Signal]:
    """Check that a signal is one this bridge is willing to act on.

    Runs before any arithmetic, so a signal that is not tradable at all is
    rejected on that ground rather than on some later, more confusing one. An
    abstention lands here, and the reason it carries is the engine's own.
    """
    details: dict[str, object] = {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol_normalised,
        "action": signal.action.value,
    }

    if not signal.is_tradable:
        source_reason = str(signal.source_metadata.get("source_reason") or "")
        explanation = (
            f"the price-action engine returned {signal.action.value}"
            f"{f' ({source_reason})' if source_reason else ''}, which is not a trade. "
            f"Both abstentions reach this point carrying the engine's reason, so a quiet "
            f"session is explicable rather than mysterious."
        )
        return refused(
            RejectionReason.SIGNAL_INVALID,
            explanation,
            source_reason=source_reason,
            is_abstention=bool(signal.source_metadata.get("is_abstention", False)),
            **details,
        )

    if signal.entry is None:
        # `Signal` validates this itself, so reaching it means a signal was built
        # by a path that skipped construction. Checked anyway: this function is
        # public and is the last gate before sizing divides by things.
        return refused(
            RejectionReason.SIGNAL_ENTRY_INVALID,
            "the signal is tradable but carries no entry price",
            **details,
        )

    if not signal.symbol_normalised:
        return refused(
            RejectionReason.SIGNAL_SYMBOL_INVALID,
            "the signal carries no symbol, so there is nothing to trade",
            **details,
        )

    minimum = risk.minimum_evidence_score
    if minimum is not None:
        if signal.evidence_score is None:
            # Refused rather than allowed. "No evidence score" is not "evidence
            # above the threshold"; treating a missing value as a pass would make
            # the filter trivially bypassable by any signal source that omits it,
            # which is exactly the wrong failure direction for a risk control.
            return refused(
                RejectionReason.EVIDENCE_BELOW_MINIMUM,
                (
                    f"the minimum evidence score is {minimum} and this signal reported no "
                    f"score at all. A missing value is not a passing one."
                ),
                minimum_evidence_score=minimum,
                **details,
            )
        if signal.evidence_score < minimum:
            return refused(
                RejectionReason.EVIDENCE_BELOW_MINIMUM,
                (
                    f"the signal's evidence score is {signal.evidence_score:.4f}, below the "
                    f"configured minimum of {minimum}. This score is a concentration of "
                    f"evidence, not a probability of winning, and no threshold on it has been "
                    f"validated against outcomes."
                ),
                evidence_score=signal.evidence_score,
                minimum_evidence_score=minimum,
                **details,
            )

    return resolved(signal, **details)


def validate_geometry(
    signal: Signal,
    stop: StopLoss,
    take_profit: TakeProfit | None,
) -> Resolution[tuple[StopLoss, TakeProfit | None]]:
    """Check entry, stop and target against each other.

    The stop side is re-checked here even though
    :func:`~signal_to_trade_bridge.domain.stops.resolve_stop` already verified
    it. That is deliberate duplication, and the reason is worth stating: the stop
    resolver and this function are reached by different callers, the geometry can
    come from configuration as well as from a signal, and a geometry check that
    trusts its input to have been checked elsewhere is a geometry check that fails
    the first time somebody calls it directly.

    A redundant check costs a comparison. A missing one costs a rejected broker
    order.
    """
    entry = signal.entry
    if entry is None:
        return refused(
            RejectionReason.SIGNAL_ENTRY_INVALID,
            "the signal has no entry price, so the geometry cannot be checked",
            signal_id=signal.signal_id,
        )

    details: dict[str, object] = {
        "signal_id": signal.signal_id,
        "entry": str(entry),
        "stop": str(stop.price),
        "stop_distance": str(stop.distance),
    }

    if stop.price <= 0:
        return refused(
            RejectionReason.NO_VALID_STOP,
            f"the stop price {stop.price} is not a tradable level",
            **details,
        )

    if stop.distance <= 0:
        # Before the side test, for the reason the take-profit check below gives:
        # a stop at the entry is a distance problem, and reporting it as "wrong
        # side" would point an operator at a sign error that is not there.
        return refused(
            RejectionReason.INVALID_STOP_DISTANCE,
            f"the stop distance is {stop.distance}, so the position sizer would divide by zero",
            **details,
        )

    if not is_protective(signal.direction, entry, stop.price):
        side = _protective_side(signal)
        return refused(
            RejectionReason.STOP_ON_WRONG_SIDE,
            (
                f"the stop at {stop.price} is not {side} the entry of {entry} for a "
                f"{signal.direction.value.lower()}, so it does not limit loss. Nothing "
                f"downstream checks this."
            ),
            **details,
        )

    if take_profit is None:
        # A successful outcome, not a refusal: `TakeProfitSource.NONE` is a
        # deliberate configuration, and a log full of refusals for it would be
        # noise that trains people to ignore the reason codes.
        return resolved((stop, None), take_profit_configured=False, **details)

    details["take_profit"] = str(take_profit.price)
    details["take_profit_distance"] = str(take_profit.distance)

    if take_profit.price <= 0:
        return refused(
            RejectionReason.INVALID_TAKE_PROFIT,
            f"the take profit {take_profit.price} is not a tradable level",
            **details,
        )

    if take_profit.distance <= 0:
        # Checked *before* the side test, and the ordering is the same as the
        # stop's for the same reason: a target exactly at entry is a distance
        # problem, not a side problem. Reported as "wrong side" it would send an
        # operator looking for a sign error that is not there.
        return refused(
            RejectionReason.INVALID_TAKE_PROFIT,
            (
                f"the take profit is at the entry of {entry}, so its distance is zero. A target "
                f"at the entry price cannot be reached for a gain."
            ),
            **details,
        )

    if not is_favourable(signal.direction, entry, take_profit.price):
        side = _favourable_side(signal)
        return refused(
            RejectionReason.TAKE_PROFIT_ON_WRONG_SIDE,
            (
                f"the take profit at {take_profit.price} is not {side} the entry of {entry} for "
                f"a {signal.direction.value.lower()}, so it could not be reached for a gain"
            ),
            **details,
        )

    return resolved((stop, take_profit), take_profit_configured=True, **details)


def _protective_side(signal: Signal) -> str:
    """Where a *stop* belongs, for this direction.

    A long's stop goes below entry; a short's goes above.
    """
    return "below" if signal.direction is Direction.LONG else "above"


def _favourable_side(signal: Signal) -> str:
    """Where a *take profit* belongs, for this direction.

    The opposite of :func:`_protective_side`, and a separate function rather than
    a negated call to it. The two messages must not share a helper: a stop
    message that said "above" where the target message said "below" would make
    one geometry problem look like two different ones, and an operator comparing
    the two log lines would be comparing two descriptions of the same fault.
    """
    return "above" if signal.direction is Direction.LONG else "below"


def validate_policy(
    signal: Signal,
    risk: RiskParameters,
    account_open_positions: int | None = None,
) -> Resolution[Signal]:
    """Check the configured rules that decide whether this bridge may act.

    Separate from :func:`validate_geometry` because these are *operator's* rules
    rather than facts about the trade. A long-only configuration refusing a SELL
    is doing exactly what it was configured to do, and that refusal should be
    distinguishable in a log from a malformed signal.
    """
    details: dict[str, object] = {
        "signal_id": signal.signal_id,
        "symbol": signal.symbol_normalised,
        "direction": signal.direction.value,
    }

    if signal.direction is Direction.LONG and not risk.allow_buy:
        return refused(
            RejectionReason.DIRECTION_NOT_ALLOWED,
            (
                f"this bridge is configured long-only, so the {signal.direction.value} was "
                f"refused. That is a configuration doing its job, not a fault."
            ),
            allow_buy=risk.allow_buy,
            allow_sell=risk.allow_sell,
            **details,
        )

    if signal.direction is Direction.SHORT and not risk.allow_sell:
        return refused(
            RejectionReason.DIRECTION_NOT_ALLOWED,
            (
                f"this bridge is configured short-only, so the {signal.direction.value} was "
                f"refused. That is a configuration doing its job, not a fault."
            ),
            allow_buy=risk.allow_buy,
            allow_sell=risk.allow_sell,
            **details,
        )

    if not risk.symbol_allowed(signal.symbol_normalised):
        return refused(
            RejectionReason.SYMBOL_NOT_ALLOWED,
            (
                f"{signal.symbol_normalised} is not in this bridge's allowlist. Note that "
                f"this is not the only gate: the execution project has its own allowlist and "
                f"applies it independently."
            ),
            allowed=sorted(risk.allowed_symbols),
            **details,
        )

    limit = risk.max_open_positions
    if limit is not None and account_open_positions is not None and account_open_positions >= limit:
        return refused(
            RejectionReason.MAX_CONCURRENT_POSITIONS,
            (
                f"there are already {account_open_positions} open positions and the limit is "
                f"{limit}. Note that the execution project's equivalent gate is inert: its "
                f"adapter never populates the open-position count."
            ),
            open_positions=account_open_positions,
            max_open_positions=limit,
            **details,
        )

    return resolved(signal, **details)


def validate_against_spec(
    entry: Decimal,
    stop: Decimal,
    take_profit: TakeProfit | None,
    spec: SymbolSpec,
) -> Resolution[SymbolSpec]:
    """Check the levels against what the symbol can actually express.

    The fourth check the execution layer does not make. A stop 0.00001 away on a
    5-digit pair is inside the tick size and the broker will refuse it; a take
    profit rounded to fewer decimals than the symbol quotes is a different price
    than the one that was computed. Both are cheap to check here and expensive to
    discover through a rejected order.

    The spread check is *not* here. It needs a live quote rather than a
    specification, so it belongs with the market-data provider in Phase 7.
    """
    details: dict[str, object] = {
        "symbol": spec.symbol_normalised,
        "entry": str(entry),
        "stop": str(stop),
        "tick_size": str(spec.tick_size),
        "digits": spec.digits,
    }

    if spec.tick_size <= 0:
        return refused(
            RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
            f"{spec.symbol_normalised} reported a tick size of {spec.tick_size}",
            **details,
        )

    for label, price in (("stop loss", stop), ("entry", entry)):
        remainder = price % spec.tick_size
        if remainder != 0:
            # Reported as a warning in the details rather than refused outright,
            # because a broker will usually accept and round it. The alternative
            # -- silently accepting a price the broker will change -- means the
            # executed risk is not the risk that was calculated.
            details[f"{label.replace(' ', '_')}_off_tick"] = str(remainder)

    if take_profit is not None:
        remainder = take_profit.price % spec.tick_size
        if remainder != 0:
            details["take_profit_off_tick"] = str(remainder)

    return resolved(spec, **details)


def collect_reasons(resolutions: Iterable[Resolution[object]]) -> Sequence[RejectionReason]:
    """The reasons from a sequence of resolutions that failed.

    For a log or an alert that wants to know everything that was wrong, not just
    the first thing. The pipeline itself stops at the first refusal, because
    running later checks on a signal that has already failed produces arithmetic
    on meaningless inputs -- but a diagnostic may want the whole picture.
    """
    return [item.reason for item in resolutions if item.reason is not None]
