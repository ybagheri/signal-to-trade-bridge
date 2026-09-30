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

**The "1:1 as the floor" half of that sentence was not true until Phase 5.**
Nothing compared the signal's implied ratio against anything, so a structurally
sound target at 0.2:1 was accepted silently under the default policy and the
configured ratio decided only the *fallback* distance. Phase 5 added
:attr:`RiskParameters.minimum_reward_risk_ratio` to make the floor real --
**off by default**, because turning it on starts replacing engine-measured
targets with the configured distance, which is a trading decision rather than a
bug fix. Set it equal to ``reward_risk_ratio`` for the behaviour the paragraph
above has always described.

**A fallback is never silent.** Every resolved take profit records its
``TakeProfitSource``, so a log can never make a 1:1 target look like the engine's
own measured move. That distinction is the difference between an auditable
decision and a plausible-looking number. **Every successful resolution also
records ``achieved_ratio``** -- what the trade actually has, not what was
configured -- and it is ``None`` only under ``NONE``, where there is no target and
therefore no ratio.

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
from signal_to_trade_bridge.domain.models import (
    RiskParameters,
    Signal,
    StopLoss,
    TakeProfit,
    achieved_ratio,
    canonical_ratio,
)
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved

__all__ = [
    "STRUCTURAL_TARGET_BASES",
    "VOLATILITY_TARGET_BASES",
    "is_favourable",
    "is_structural_target_basis",
    "resolve_take_profit",
    "signal_target_ratio",
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

# Reward distance is derived by scaling the risk distance, so a zero distance would
# divide by nothing. The stop resolver already refuses a zero distance, so
# `target_from_ratio` guards it a second time. **There is deliberately no minimum
# ratio constant here.** Phase 3 had one, `_MIN_RATIO`, with a docstring
# explaining that a "minimum-ratio clamp" protected the target computation -- and
# nothing referenced it. The guard that actually runs is `ratio <= 0` in
# `target_from_ratio`, and `RiskParameters.minimum_reward_risk_ratio` is the real
# minimum-ratio setting. A constant with a reassuring docstring and no reader is
# worse than no constant: it makes the next person believe a clamp exists.


def signal_target_ratio(stop: StopLoss, signal: Signal) -> Decimal | None:
    """The reward:risk a signal's own target implies, or ``None`` if it has none.

    Separate from :func:`~signal_to_trade_bridge.domain.models.achieved_ratio`
    because this one takes a *signal*, and the whole question here is whether the
    signal's target — a price that has not been accepted yet — would clear a floor.
    Comparing an unaccepted target against a threshold is what
    :attr:`RiskParameters.minimum_reward_risk_ratio` exists for.

    ``None`` rather than zero when the signal has no target, so a caller can tell
    "no target to judge" from "a target at 0.2:1, which is a judgement".
    """
    if signal.take_profit is None:
        return None
    entry = signal.entry
    if entry is None or stop.distance <= 0:
        return None
    return canonical_ratio(abs(signal.take_profit - entry) / stop.distance)


def _ratio(take_profit: TakeProfit, stop: StopLoss) -> str | None:
    """``achieved_ratio`` as the string that goes into a decision record.

    A thin wrapper rather than four inline calls, so the conversion from number to
    log text happens once and `None` is handled in one place. Every path that
    emits a take profit reports through here, which is what makes the guarantee
    "``achieved_ratio`` is always present on a successful resolution, and is
    ``None`` only when there is no target" true rather than aspirational.

    A string because :attr:`~signal_to_trade_bridge.domain.resolution.Resolution.details`
    is a record for a log and a later audit, and a ``Decimal`` in there serialises
    through ``default=str`` at the formatter while comparing as a number in a test
    -- one representation, two behaviours. The underlying
    :func:`~signal_to_trade_bridge.domain.models.achieved_ratio` keeps the number
    for anyone who wants to do arithmetic on it.
    """
    ratio = achieved_ratio(stop, take_profit)
    return None if ratio is None else str(ratio)


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
        "minimum_reward_risk_ratio": (
            str(risk.minimum_reward_risk_ratio)
            if risk.minimum_reward_risk_ratio is not None
            else None
        ),
    }
    signal_target = signal.take_profit
    if signal_target is not None:
        details["signal_target"] = str(signal_target)
        details["signal_target_basis"] = signal.take_profit_basis

    policy = risk.take_profit_source

    # -- NONE: a deliberate configuration, not a problem -------------------
    if policy is TakeProfitSource.NONE:
        # `achieved_ratio` is present and `None` rather than absent. Before Phase 5
        # this key simply did not exist on this path, and the first consumer to
        # index it would have raised `KeyError` on a *successful* resolution --
        # which is the worst possible moment for a missing key, because the code
        # around it has already decided the trade is fine. `None` says "there is
        # no target, so there is no ratio", which is a different statement from a
        # refusal and is the only thing `None` means anywhere in this type.
        return resolved(
            None,
            reason_code="TARGET_DISABLED",
            achieved_ratio=None,
            **details,
        )

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
    else:
        # The floor, checked last so that it only ever sees a target that is
        # already acceptable on every other ground. Reporting "its basis was a
        # volatility multiple" is more useful than "its ratio was 0.2:1" when both
        # are true, because the basis is the upstream engine's own defect and the
        # remedy is upstream.
        floor = risk.minimum_reward_risk_ratio
        implied = signal_target_ratio(stop, signal)
        if floor is not None and implied is not None and implied < floor:
            details["signal_target_ratio"] = str(implied)
            unusable_reason = (
                f"the signal's target of {signal_target} implies a reward:risk of "
                f"{implied}, below the configured floor of {floor}"
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
                achieved_ratio=_ratio(take_profit, stop),
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
            achieved_ratio=_ratio(ratio_target, stop),
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
            achieved_ratio=_ratio(take_profit, stop),
            **details,
        )

    return resolved(
        ratio_target,
        achieved_ratio=_ratio(ratio_target, stop),
        # Recorded explicitly rather than left to the source field alone, so a
        # log line shows *why* the ratio was used rather than only *that* it was.
        fallback_because=unusable_reason,
        **details,
    )
