"""Position sizing.

The formula, from ``docs/architecture.md`` §5.6:

    risk_amount      = balance * risk_percent / 100          (see :mod:`risk`)
    ticks            = |entry - stop| / tick_size
    risk_per_unit    = ticks * conservative_tick_value
    raw_volume       = risk_amount / risk_per_unit
    volume           = round_down_to_step(raw_volume)  then clamp to [min, max]

**Why tick-value based and not a pip formula.** It is what makes the sizer
correct for gold, indices, CFDs and futures-like instruments with no special
cases. A 5-digit EURUSD and a 2-decimal XAUUSD both reach ``$300`` per lot over
a stop, by completely different arithmetic -- ``0.00300 / 0.00001 * $1`` and
``3.00 / 0.01 * $1``. A sizer that assumed a 100000-lot contract size and a
5-digit pair would be wrong on gold by two orders of magnitude, and wrong in the
direction of *oversizing*: it would place a position risking far more than the
budget. Both cases are in the test suite, deliberately, because the pair of them
is what distinguishes a tick-value sizer from a forex sizer that happens to work.

**Why the worse of the two tick values.** ``tick_value_profit`` and
``tick_value_loss`` can differ on a hedging account and on some CFDs, and since

    volume = risk_amount / (ticks * tick_value)

the volume is **inversely** proportional to the tick value. So the conservative
choice is the *larger* of the two: it is an upper bound on what one tick can cost,
and dividing by an upper bound cannot produce a position larger than the budget
allows. Dividing by the smaller would size for the cheapest possible tick -- the
least conservative choice available, and the one Phase 1 had implemented by
mistake. :attr:`SymbolSpec.conservative_tick_value` explains it at length;
:func:`resolve_position_size` records both raw values in the resolution so a log
line shows which way the symbol was asymmetric.

Three rules this module exists to keep, and they are the reason it is not three
lines of arithmetic:

1. **A volume below ``volume_min`` is a refusal, never a floor-up.** Flooring up
   to reach the broker minimum would place a position whose risk exceeds the
   budget by an unbounded amount -- the budget is a fixed number of dollars and
   the floor is whatever the broker demands. It is the single most dangerous line
   in any position sizer, and it is guarded three times over:
   :meth:`SymbolSpec.clamp_volume` returns zero rather than the minimum,
   :class:`PositionSize` raises if ``clamped_to_minimum`` is ever set, and
   :func:`resolve_position_size` refuses rather than clamping.
2. **Rounding to ``volume_step`` is down.** Rounding up can exceed the budget;
   rounding down leaves it fractionally under, which is the safe direction. An
   under-spent budget is a disappointment and an over-spent one is a loss.
3. **Clamping down to ``volume_max`` is allowed and is recorded.** The real risk
   is then *below* the budget, which is a fact a log should state rather than
   hide -- a position at the maximum is not a position at the intended size.

**There is no code path that invents a balance or a tick value**, the same rule
:mod:`stops` applies to a stop. This module is handed both facts and refuses when
either is unusable. It never supplies a default, and a companion test walks this
module's AST to keep it that way.

Failures are :class:`~signal_to_trade_bridge.domain.resolution.Resolution`
values. An untradeable size -- too small to place, or a specification the
terminal has not populated -- is an expected outcome on a quiet market or an
unfinished session, not an exceptional condition.
"""

from __future__ import annotations

from decimal import Decimal

from signal_to_trade_bridge.domain.enums import RejectionReason
from signal_to_trade_bridge.domain.models import PositionSize, RiskBudget, StopLoss, SymbolSpec
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved

__all__ = ["check_broker_constraints", "resolve_position_size"]


def _is_on_step(volume: Decimal, step: Decimal) -> bool:
    """Whether a volume is a whole number of steps.

    Dividing and comparing rather than using ``%``, because the quotient is the
    quantity that has to be integral and computing it once is clearer than
    reasoning about ``Decimal`` remainder semantics at 28 significant digits.
    """
    quotient = volume / step
    return quotient == quotient.to_integral_value()


def check_broker_constraints(volume: Decimal, spec: SymbolSpec) -> Resolution[Decimal]:
    """Whether the broker would accept this volume at all.

    Public and separate from the sizer because the constraints are a property of
    the *symbol*, not of the arithmetic, and a caller with a volume from somewhere
    other than :func:`resolve_position_size` -- a risk profile, a hand-tuned
    default, a future margin check -- needs the same four answers the sizer gets.
    They are also the only place in the bridge that can produce
    ``VOLUME_NOT_ON_STEP``, which would otherwise be a reason code nothing could
    ever emit.

    Ordered as a sequence of independent facts rather than as nested ranges,
    because the messages differ: "too small to trade", "too large for this
    symbol", and "not a size this broker accepts" are three different problems
    with three different remedies, and a single "out of range" would send an
    operator looking for the wrong one.
    """
    details: dict[str, object] = {
        "symbol": spec.symbol_normalised,
        "volume": str(volume),
        "volume_min": str(spec.volume_min),
        "volume_max": str(spec.volume_max),
        "volume_step": str(spec.volume_step),
    }

    if volume <= 0:
        return refused(
            RejectionReason.INVALID_VOLUME,
            f"a volume of {volume} is not a tradable size, and zero is not a way to decline",
            **details,
        )

    if volume < spec.volume_min:
        return refused(
            RejectionReason.VOLUME_BELOW_BROKER_MINIMUM,
            (
                f"the computed volume of {volume} is below this broker's minimum of "
                f"{spec.volume_min}. It is refused rather than raised to the minimum: the "
                f"budget is a fixed number of dollars and the minimum is whatever the broker "
                f"demands, so flooring up risks an unbounded amount more than was budgeted. "
                f"The usual cause is a risk percentage too small for this instrument's stop "
                f"distance, which means the configuration is wrong rather than the signal."
            ),
            **details,
        )

    if volume > spec.volume_max:
        return refused(
            RejectionReason.VOLUME_ABOVE_BROKER_MAXIMUM,
            (
                f"the volume of {volume} exceeds this broker's maximum of {spec.volume_max}. "
                f"Unlike a sub-minimum volume this may be clamped down, because doing so "
                f"reduces the risk rather than increasing it."
            ),
            **details,
        )

    if not _is_on_step(volume, spec.volume_step):
        return refused(
            RejectionReason.VOLUME_NOT_ON_STEP,
            (
                f"the volume of {volume} is not a multiple of this broker's step of "
                f"{spec.volume_step}, so the broker would refuse it or change it. The sizer "
                f"rounds down to the step; reaching here means a volume came from somewhere "
                f"that did not."
            ),
            **details,
        )

    return resolved(volume, **details)


def resolve_position_size(
    stop: StopLoss,
    budget: RiskBudget,
    spec: SymbolSpec,
) -> Resolution[PositionSize]:
    """The volume for this trade, or a refusal naming what was wrong with it.

    Checked in this order, and the order is the design:

    1. **Is the stop distance usable?** A zero distance means the division below
       is a division by zero.
    2. **Does the symbol have a tick size and a tick value?** Without them the
       risk per lot is unknown and no size can be computed. This is refused, not
       defaulted -- a made-up tick value would produce a confident number.
    3. **Is the risk per lot positive?** The consequence of the two above, checked
       explicitly rather than allowed to divide.
    4. **Round down to the step, then clamp.**
    5. **Is what came out of that tradeable?** The sub-minimum refusal lives
       here, and it is the most important line in the module.

    Every refusal keeps the arithmetic it had already done. "Raw volume 0.004,
    rounded 0.00, minimum 0.01" is the difference between a refusal an operator
    can act on and one they can only observe.
    """
    details: dict[str, object] = {
        "symbol": spec.symbol_normalised,
        "stop_distance": str(stop.distance),
        "tick_size": str(spec.tick_size),
        "tick_value": str(spec.conservative_tick_value),
        "tick_value_profit": str(spec.tick_value_profit),
        "tick_value_loss": str(spec.tick_value_loss),
        "risk_amount": str(budget.amount),
        "balance": str(budget.balance),
        "risk_percent": str(budget.risk_percent),
        "currency": budget.currency,
    }

    if stop.distance <= 0:
        # `StopLoss` validates this at construction, so reaching it means the
        # stop was built by a path that skipped construction. The stop resolver
        # refuses a zero distance first, which is why this is a second line.
        return refused(
            RejectionReason.INVALID_STOP_DISTANCE,
            (
                f"the stop distance is {stop.distance}, so the ticks over it cannot be counted "
                f"and the position size would be a division by zero."
            ),
            **details,
        )

    if spec.tick_size <= 0:
        return refused(
            RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
            (
                f"{spec.symbol_normalised} reported a tick size of {spec.tick_size}, so the "
                f"stop distance cannot be converted into ticks. The specification comes from the "
                f"terminal; it is not defaulted here, because an invented tick size produces a "
                f"confident and entirely wrong volume."
            ),
            **details,
        )

    tick_value = spec.conservative_tick_value
    if tick_value <= 0:
        return refused(
            RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
            (
                f"{spec.symbol_normalised} reported a tick value of {tick_value}, so the money "
                f"at risk per lot is unknown. The specification comes from the terminal; it is "
                f"not defaulted here, because the whole point of a risk budget is that the number "
                f"it is divided by means something."
            ),
            **details,
        )

    ticks = stop.distance / spec.tick_size
    details["ticks"] = str(ticks)
    risk_per_unit = ticks * tick_value
    details["risk_per_unit"] = str(risk_per_unit)

    # No zero check on `risk_per_unit`, and its absence is deliberate. The three
    # guards above establish a positive distance, a positive tick size and a
    # positive tick value, so their product is positive and the division below
    # cannot divide by zero. An earlier draft did check, and the check was
    # unreachable -- which is worse than useless, because a guard nobody can
    # reach reads as though it handles a case it does not.
    #
    # The same argument does *not* cover `raw_volume`, because a `RiskBudget` can
    # reach this function without having come from `resolve_risk_budget`. So that
    # one is not guarded either: a zero budget divides to a zero volume, which
    # falls through to the broker-constraint check below and is refused there as a
    # sub-minimum volume, which is the correct answer and needs no branch of its
    # own to produce it.
    raw_volume = budget.amount / risk_per_unit
    details["raw_volume"] = str(raw_volume)

    rounded = spec.round_volume(raw_volume, round_down=True)
    details["rounded_volume"] = str(rounded)
    details["rounded"] = rounded != raw_volume

    candidate = spec.clamp_volume(rounded)
    details["candidate_volume"] = str(candidate)

    if candidate <= 0:
        # THE refusal. Reached whenever rounding down took the volume below the
        # broker's minimum, and the answer is never "raise it to the minimum".
        return refused(
            RejectionReason.VOLUME_BELOW_BROKER_MINIMUM,
            (
                f"risking {budget.amount} ({budget.risk_percent}% of a balance of "
                f"{budget.balance}) over a stop of {stop.distance} gives {ticks} ticks and a "
                f"raw volume of {raw_volume}, which rounds down to {rounded} -- below this "
                f"broker's minimum of {spec.volume_min}. The trade is refused rather than "
                f"floored up to the minimum, because the minimum risks an unbounded amount "
                f"more than was budgeted. Either raise BRIDGE_RISK_PERCENT, accept that this "
                f"instrument is too coarse for this account, or wait for a setup with a tighter "
                f"stop."
            ),
            **details,
        )

    clamped_to_maximum = candidate < rounded
    details["clamped_to_maximum"] = clamped_to_maximum

    constraints = check_broker_constraints(candidate, spec)
    if not constraints.ok:
        # Unreachable through the path above -- `round_volume` puts the volume on
        # the step and `clamp_volume` bounds it -- and it is kept anyway, because
        # it is reachable the moment either helper changes. That is a realistic
        # refactor, not a hypothetical one, and the failure it prevents is the
        # expensive kind: the sizer hands the broker a size it will refuse, and
        # the round trip is paid in a confusing log entry on the far side.
        #
        # The refusal is returned rather than repaired, so a change to either
        # helper surfaces here loudly instead of silently producing a worse size.
        return refused(
            constraints.reason or RejectionReason.INVALID_VOLUME,
            constraints.explanation,
            **details,
        )

    position = PositionSize(
        volume=candidate,
        raw_volume=raw_volume,
        risk_amount=budget.amount,
        stop_distance=stop.distance,
        risk_per_unit=risk_per_unit,
        ticks=ticks,
        tick_size=spec.tick_size,
        tick_value=tick_value,
        # Never set. A floor-up is a refusal, and `PositionSize` raises if it is
        # ever constructed with one.
        clamped_to_minimum=False,
        rounded=rounded != raw_volume,
        clamped_to_maximum=clamped_to_maximum,
    )

    if not position.is_within_budget:
        # Also unreachable: rounding down and clamping down both move the volume
        # away from the budget, never towards it. This is the invariant that the
        # whole module exists to preserve, so it is asserted in code rather than
        # only in a test -- a test proves it today, and this proves it on the day
        # somebody changes the rounding direction.
        return refused(
            RejectionReason.SIZING_FAILED,
            (
                f"the computed volume of {position.volume} would risk "
                f"{position.planned_loss} against a budget of {position.risk_amount}. A position "
                f"sizer that can exceed its own budget is the failure this project exists to "
                f"prevent, so the size is discarded rather than sent."
            ),
            planned_loss=str(position.planned_loss),
            **details,
        )

    details["volume"] = str(position.volume)
    details["planned_loss"] = str(position.planned_loss)
    details["within_budget"] = position.is_within_budget
    return resolved(position, **details)
