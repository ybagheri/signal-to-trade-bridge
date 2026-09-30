"""Take-profit resolution.

The configured default is a 1:1 reward:risk ratio, so the reward distance equals
the risk distance:

    BUY   entry 100  stop 99   ->  take profit 101
    SELL  entry 100  stop 101  ->  take profit 99

But the brief also says the engine's own target must not be silently overridden,
and both of those are true at once. The resolution is a **policy**, chosen per
deployment rather than per signal:

``RR_FALLBACK`` (default)
    Use the signal's own target when it is structurally defensible and on the
    correct side. Otherwise apply the ratio, and record that it was a fallback.

``SIGNAL``
    Use the signal's own target. Refuse the trade if it is unusable.

``RR_DERIVED``
    Ignore signal targets entirely. Always the ratio.

``NONE``
    No take profit. Only valid if exits are managed elsewhere.

The two that matter most are the first and the last, and they encode genuinely
different intentions:

* ``RR_FALLBACK`` says "the engine's target is usually better than mine, but not
  always, and I want 1:1 as the floor".
* ``RR_DERIVED`` says "I do not trust a target from a system that documents its
  own targets as unvalidated, and I want exactly 1:1". A trader who has read the
  engine's own caveats may well want this, and it is not the same preference as
  the default.

**A fallback is never silent.** Every resolved take profit records its
``TakeProfitSource``, so a log can never make a 1:1 target look like the engine's
own measured move. That distinction is the difference between an auditable
decision and a plausible-looking number.

The same upstream caveat applies here as to the stop. The engine's target bases
are ``MEASURED_MOVE``, ``FADE_ORIGIN`` and ``SWING`` for a level the market
produced, and ``ATR_FALLBACK`` for a volatility multiple.
"""

from __future__ import annotations

from decimal import Decimal

from signal_to_trade_bridge.domain.enums import (
    Direction,
    RejectionReason,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.models import RiskParameters, Signal, StopLoss, TakeProfit
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved

__all__ = [
    "STRUCTURAL_TARGET_BASES",
    "VOLATILITY_TARGET_BASES",
    "is_favourable",
    "is_structural_target_basis",
    "resolve_take_profit",
    "target_from_ratio",
]

#: Bases meaning a level the market produced, as opposed to a volatility
#: multiple. The mirror of ``STRUCTURAL_STOP_BASES``.
STRUCTURAL_TARGET_BASES: frozenset[str] = frozenset(
    {
        "MEASURED_MOVE",
        "FADE_ORIGIN",
        "SWING",
    }
)

#: A target derived from volatility rather than structure.
VOLATILITY_TARGET_BASES: frozenset[str] = frozenset({"ATR_FALLBACK"})

#: Reward distance is derived by scaling the risk distance, so a zero distance
#: would divide by nothing. The stop resolver already refuses a zero distance, so
#: this is a second line rather than the first.
_MIN_RATIO = Decimal("0.000001")


def is_structural_target_basis(basis: str) -> bool:
    """Whether a target basis names a level the market produced.

    Unknown bases are treated as non-structural, for the same reason as on the
    stop: an unrecognised provenance is not a licence to trade it.
    """
    return basis.strip().upper() in STRUCTURAL_TARGET_BASES


def is_favourable(direction: Direction, entry: Decimal, target: Decimal) -> bool:
    """Whether a target is on the side where it can be reached for a gain.

    A long wants a target **above** entry; a short wants one **below**. The
    complement of :func:`~signal_to_trade_bridge.domain.stops.is_protective`, and
    checked for the same reason: nothing downstream verifies it.
    """
    if not direction.is_tradable:
        return False
    if direction is Direction.LONG:
        return target > entry
    return target < entry


def target_from_ratio(
    direction: Direction,
    entry: Decimal,
    stop_distance: Decimal,
    ratio: Decimal,
) -> Decimal | None:
    """A target at ``ratio`` times the risk distance, or ``None`` if impossible.

    ``None`` rather than an exception, because a caller resolving a take profit
    has a refusal path already and does not need a second way to fail. The two
    impossible cases are a non-tradable direction and a zero risk distance, and
    both are bugs upstream of here rather than market conditions.
    """
    if not direction.is_tradable or stop_distance <= 0 or ratio <= 0:
        return None
    reward = stop_distance * ratio
    if direction is Direction.LONG:
        return entry + reward
    return entry - reward


def resolve_take_profit(
    signal: Signal,
    stop: StopLoss,
    risk: RiskParameters,
) -> Resolution[TakeProfit | None]:
    """Resolve a take profit under the configured policy, or refuse.

    Returns ``Resolution[TakeProfit | None]`` because ``TakeProfitSource.NONE``
    is a *successful* resolution of "no take profit". Modelling it as a failure
    would be wrong: an operator who has deliberately disabled targets has not hit
    a problem, and a log full of refusals for a deliberate configuration would be
    noise that trains people to ignore the reason codes.
    """
    entry = signal.entry
    if entry is None:
        return refused(
            RejectionReason.INVALID_TAKE_PROFIT,
            "the signal has no entry price, so there is nothing to aim a target at",
            signal_id=signal.signal_id,
        )

    details: dict[str, object] = {
        "entry": str(entry),
        "signal_id": signal.signal_id,
        "policy": risk.take_profit_source.value,
        "stop_distance": str(stop.distance),
        "reward_risk_ratio": str(risk.reward_risk_ratio),
    }
    signal_target = signal.take_profit
    if signal_target is not None:
        details["signal_target"] = str(signal_target)
        details["signal_target_basis"] = signal.take_profit_basis

    policy = risk.take_profit_source

    # -- NONE: a deliberate configuration, not a problem -------------------
    if policy is TakeProfitSource.NONE:
        return resolved(None, reason_code="TARGET_DISABLED", **details)

    # -- Work out whether the signal's own target is usable ---------------
    unusable_reason = ""
    if signal_target is None:
        unusable_reason = "the signal carries no take profit"
    elif not is_favourable(signal.direction, entry, signal_target):
        side = "above" if signal.direction is Direction.LONG else "below"
        unusable_reason = (
            f"the signal's target of {signal_target} is not {side} the entry of {entry}, so a "
            f"{signal.direction.value.lower()} could not reach it for a gain"
        )
    elif not is_structural_target_basis(signal.take_profit_basis):
        unusable_reason = (
            f"the signal's target basis is {signal.take_profit_basis or 'unstated'}, which is a "
            f"volatility multiple rather than a level the market produced"
        )

    signal_target_usable = not unusable_reason

    # -- SIGNAL: the strict policy ----------------------------------------
    if policy is TakeProfitSource.SIGNAL:
        if signal_target_usable and signal_target is not None:
            take_profit = TakeProfit(
                price=signal_target,
                distance=abs(signal_target - entry),
                source=TakeProfitSource.SIGNAL,
                basis=signal.take_profit_basis,
            )
            return resolved(
                take_profit,
                achieved_ratio=str(take_profit.distance / stop.distance),
                **details,
            )
        return refused(
            RejectionReason.INVALID_TAKE_PROFIT,
            (
                f"BRIDGE_TAKE_PROFIT_SOURCE=SIGNAL requires the signal's own target, and "
                f"{unusable_reason}. Set it to RR_FALLBACK to apply the configured ratio when "
                f"the signal has no usable target."
            ),
            unusable_because=unusable_reason,
            **details,
        )

    # -- RR_DERIVED and RR_FALLBACK both need the computed target ----------
    computed = target_from_ratio(signal.direction, entry, stop.distance, risk.reward_risk_ratio)
    if computed is None:
        return refused(
            RejectionReason.INVALID_TAKE_PROFIT,
            (
                f"a {risk.reward_risk_ratio}:1 target could not be computed from an entry of "
                f"{entry} and a stop distance of {stop.distance}. The stop resolver should "
                f"have refused a zero distance first, so this indicates a bug rather than a "
                f"market condition."
            ),
            **details,
        )

    ratio_target = TakeProfit(
        price=computed,
        distance=abs(computed - entry),
        source=(
            TakeProfitSource.RR_DERIVED
            if policy is TakeProfitSource.RR_DERIVED
            else TakeProfitSource.RR_FALLBACK
        ),
        basis=f"{risk.reward_risk_ratio}:1",
    )

    # -- RR_DERIVED: the ratio always wins ---------------------------------
    if policy is TakeProfitSource.RR_DERIVED:
        if signal_target is not None:
            details["signal_target_ignored"] = str(signal_target)
        return resolved(
            ratio_target,
            achieved_ratio=str(ratio_target.distance / stop.distance),
            **details,
        )

    # -- RR_FALLBACK: the signal's target when usable, the ratio otherwise --
    if signal_target_usable and signal_target is not None:
        take_profit = TakeProfit(
            price=signal_target,
            distance=abs(signal_target - entry),
            source=TakeProfitSource.SIGNAL,
            basis=signal.take_profit_basis,
        )
        return resolved(
            take_profit,
            achieved_ratio=str(take_profit.distance / stop.distance),
            **details,
        )

    return resolved(
        ratio_target,
        achieved_ratio=str(ratio_target.distance / stop.distance),
        # Recorded explicitly rather than left to the source field alone, so a
        # log line shows *why* the ratio was used rather than only *that* it was.
        fallback_because=unusable_reason,
        **details,
    )
