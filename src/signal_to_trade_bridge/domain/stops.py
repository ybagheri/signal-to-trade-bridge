"""Stop-loss resolution.

The single most important rule in this project lives here:

    **There is no code path that invents a stop.**

If a signal arrives without a defensible stop, the answer is ``NO_TRADE``. Not a
default distance, not an ATR multiple, not a fixed number of pips. The brief says
not to invent a stop merely to make the system trade, and that instruction is
implemented rather than merely acknowledged -- there is no fallback branch in this
module at all, and its absence is the feature.

Why it matters concretely: a stop is the definition of the maximum loss. A stop
this module made up would be a number nobody chose, on a trade nobody sized
against it, and the position sizer in Phase 4 would faithfully compute a volume
for it. The arithmetic would be correct and the conclusion entirely wrong.

What the upstream engine provides, verified in the Phase 0 audit:

* ``TradePlan.stop`` -- an **absolute price**, never a distance. So no conversion
  is needed. The adapter is the only place that knows which form it received, and
  it records that in ``Signal.stop_basis``.
* ``stop_basis`` -- where that price came from. Structural bases are
  ``PULLBACK_EXTREME``, ``BREAKOUT_REFERENCE``, ``PATTERN_EXTREME`` and ``SWING``;
  non-structural are ``ATR_FALLBACK`` and ``NONE``.
* ``TradePlan.has_structural_stop`` -- exactly
  ``stop_basis not in ("ATR_FALLBACK", "NONE")``.

The engine's own docstring calls an ``ATR_FALLBACK`` stop *"arithmetically sound
and structurally empty"*, and that is the distinction this module enforces. A
volatility multiple is a reasonable number; it is not a level the market
produced, and it is refused by default for that reason rather than for being
mathematically wrong.
"""

from __future__ import annotations

from decimal import Decimal

from signal_to_trade_bridge.domain.enums import Direction, RejectionReason, StopSource
from signal_to_trade_bridge.domain.models import RiskParameters, Signal, StopLoss
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved

__all__ = [
    "NON_STOP_BASES",
    "STRUCTURAL_STOP_BASES",
    "VOLATILITY_STOP_BASES",
    "is_protective",
    "is_structural_basis",
    "resolve_stop",
]

#: Bases meaning "derived from something the market actually did". Mirrors the
#: upstream engine's ``has_structural_stop``, restated rather than imported so
#: this module has no dependency on the adapter and no dependency on a package
#: that may not be installed.
STRUCTURAL_STOP_BASES: frozenset[str] = frozenset(
    {
        "PULLBACK_EXTREME",
        "BREAKOUT_REFERENCE",
        "PATTERN_EXTREME",
        "SWING",
    }
)

#: A level the market produced, but derived from volatility rather than structure.
#: Not refused because it is arithmetically wrong -- it is refused because it is
#: empty of information about where the setup actually failed.
VOLATILITY_STOP_BASES: frozenset[str] = frozenset({"ATR_FALLBACK"})

#: No stop at all.
NON_STOP_BASES: frozenset[str] = frozenset({"NONE"})

#: Below this, a stop distance is indistinguishable from a rounding artefact, and
#: a position sized on it would have a stop inside the spread. The exact value is
#: in price units, so it is not a pip count and means the same thing on a
#: 5-digit pair and on gold.
_MIN_MEANINGFUL_DISTANCE = Decimal("1e-8")


def is_structural_basis(basis: str) -> bool:
    """Whether a stop basis names a level the market produced.

    **Unknown bases are treated as non-structural.** A basis this module has
    never heard of means the upstream engine grew one, and the safe reading of an
    unrecognised provenance is "do not trust it". Treating it as structural
    because it is not on the refusal list would mean a new upstream stop type
    would be traded automatically the first time anyone produced one.
    """
    return basis.strip().upper() in STRUCTURAL_STOP_BASES


def is_protective(direction: Direction, entry: Decimal, stop: Decimal) -> bool:
    """Whether a stop is on the side of entry where it limits loss.

    A long is protected by a stop **below** entry; a short by one **above**. The
    check is here because the execution layer does not make it: the upstream
    project writes the stop into a dialog field and reads it back, and an SL above
    the ask for a BUY is not rejected there -- it is handed to the broker, which
    refuses it after the round trip.

    So this is the last point at which the mistake can be caught locally, and
    catching it here costs a comparison while catching it at the broker costs a
    rejected order and a confusing log entry on the far side.
    """
    if not direction.is_tradable:
        return False
    if direction is Direction.LONG:
        return stop < entry
    return stop > entry


def resolve_stop(signal: Signal, risk: RiskParameters) -> Resolution[StopLoss]:
    """Resolve a signal's stop loss, or refuse with a reason.

    Checked in this order, and the order is the design:

    1. **Is there a stop at all?** No price means no stop. Refused.
    2. **Is it on the correct side of entry?** Checked before the basis, because a
       stop on the wrong side is a worse problem than an empty one, and the
       message should name the actual fault.
    3. **Is the distance usable?** Zero or sub-tick means sizing would divide by
       something meaningless.
    4. **Is it structural?** Only now, once the stop is known to be a real and
       correctly placed level. A volatility fallback is refused unless configured.

    Every refusal carries what was known at the time, so a log answers "why" and
    not merely "no".
    """
    entry = signal.entry
    if entry is None:
        # Only reachable if a caller bypasses `Signal`'s own validation, which
        # requires a tradable action to have an entry. Checked anyway because
        # this function is public and a `Signal` could reach it from a
        # deserialisation path that skipped construction.
        return refused(
            RejectionReason.NO_VALID_STOP,
            "the signal has no entry price, so there is nothing for a stop to protect",
            stop_basis=signal.stop_basis,
        )

    details = {
        "entry": str(entry),
        "signal_id": signal.signal_id,
        "direction": signal.direction.value,
        "stop_basis": signal.stop_basis,
    }

    price = signal.stop_loss
    if price is None:
        return refused(
            RejectionReason.NO_VALID_STOP,
            (
                "the signal carries no stop loss, and this bridge does not invent one. "
                "The upstream engine reports an undefined level as 0.0, which the adapter "
                "preserves as absent rather than as a price."
            ),
            **details,
        )

    details["stop"] = str(price)

    if price <= 0:
        # Checked before `is_protective`, and it has to be: a zero or negative
        # price is not a stop, and passing it to the side check would compare
        # against a level that cannot exist. It would also reach `StopLoss`, whose
        # own validation raises `ValueError` -- and a refusal escaping as an
        # exception instead of a reason code is the one thing this module's design
        # forbids. A missing stop is the commonest outcome in the system, and it
        # must arrive as a value.
        return refused(
            RejectionReason.NO_VALID_STOP,
            (
                f"the signal reported a stop of {price}, which is not a tradable level. The "
                f"upstream engine uses 0.0 to mean 'no stop', so this is an absent stop rather "
                f"than a stop at {price}."
            ),
            **details,
        )

    # Computed before the side and distance checks, not after, so that *every*
    # refusal below carries it. A wrong-side stop had a perfectly ordinary
    # distance and threw it away on the way out, and the distance is the first
    # thing anyone looks at when asking why a stop was rejected.
    distance = abs(entry - price)
    details["stop_distance"] = str(distance)

    if price == entry:
        # Checked before the side test, because a stop exactly at entry is a
        # *distance* problem rather than a side problem. Reporting it as
        # "wrong side" would be misleading: the stop is not on the wrong side, it
        # is on no side at all, and an operator reading the refusal would go
        # looking for a sign error that is not there.
        return refused(
            RejectionReason.INVALID_STOP_DISTANCE,
            (
                f"the stop is at the entry of {entry}, so the stop distance is zero. A stop at "
                f"the entry price does not limit loss; it only guarantees the spread is paid."
            ),
            **details,
        )

    if not is_protective(signal.direction, entry, price):
        side = "below" if signal.direction is Direction.LONG else "above"
        return refused(
            RejectionReason.STOP_ON_WRONG_SIDE,
            (
                f"a {signal.direction.value.lower()} is protected by a stop {side} its entry, "
                f"but this stop is at {price} against an entry of {entry}. The execution "
                f"layer does not check this, so it would be handed to the broker and refused "
                f"there instead."
            ),
            **details,
        )

    if distance <= _MIN_MEANINGFUL_DISTANCE:
        return refused(
            RejectionReason.INVALID_STOP_DISTANCE,
            (
                f"the stop distance is {distance}, which is zero for any purpose the position "
                f"sizer cares about. A stop at the entry price does not limit loss; it only "
                f"guarantees the spread is paid."
            ),
            **details,
        )

    basis = signal.stop_basis.strip().upper()
    structural = is_structural_basis(basis)

    if not structural:
        if basis in NON_STOP_BASES or not basis:
            return refused(
                RejectionReason.NO_VALID_STOP,
                (
                    "the signal reported a stop price with no provenance. A price without a "
                    "stated origin is not a stop this bridge can justify acting on."
                ),
                **details,
            )

        if basis in VOLATILITY_STOP_BASES:
            if not risk.allow_volatility_fallback_stop:
                return refused(
                    RejectionReason.STRUCTURAL_STOP_REQUIRED,
                    (
                        "the signal's stop is a volatility fallback rather than a level the "
                        "market produced. The upstream engine calls such a plan 'structurally "
                        "empty'. Set BRIDGE_ALLOW_VOLATILITY_FALLBACK_STOP=true to accept one "
                        "deliberately, and expect STOP_RESOLVED to record that the stop was not "
                        "structural."
                    ),
                    **details,
                )
            source = StopSource.SIGNAL_VOLATILITY_FALLBACK
        else:
            # An unrecognised basis is refused *even when volatility fallbacks are
            # permitted*, and the message says so. The flag names one specific
            # alternative to structural geometry; it is not a general licence to
            # trade stops of unknown provenance, and a message that merely said
            # "not structural" would leave an operator thinking they had hit the
            # documented case rather than an undocumented one.
            return refused(
                RejectionReason.STRUCTURAL_STOP_REQUIRED,
                (
                    f"the signal's stop reports the basis {basis!r}, which this bridge does not "
                    f"recognise. An unrecognised provenance is not treated as structural, and "
                    f"BRIDGE_ALLOW_VOLATILITY_FALLBACK_STOP does not extend to it -- that flag "
                    f"permits ATR_FALLBACK specifically."
                ),
                unknown_basis=basis,
                **details,
            )

        stop = StopLoss(price=price, distance=distance, source=source, basis=basis)
        return resolved(
            stop,
            structural=False,
            source=stop.source.value,
            basis=basis,
            **details,
        )

    stop = StopLoss(price=price, distance=distance, source=StopSource.SIGNAL, basis=basis)
    return resolved(
        stop,
        structural=True,
        source=stop.source.value,
        basis=basis,
        **details,
    )
