"""Convert an upstream analysis result into an internal :class:`Signal`.

This is the anti-corruption layer's core. Everything the upstream engine's shape
leaks into is confined to this file: the decision dict, the string actions, the
``+1``/``-1`` direction convention, the float prices, and the ``*_basis`` strings.

Three properties of the upstream shape drive every decision here, and all three
were verified by reading its source rather than inferred:

**1. The decision dict has two different shapes.**
The normal path is ``Decision.to_dict()`` with thirteen keys. The degenerate path
-- no bars, a negative index, no volatility reference -- is built by
``Analyzer._empty`` and has **three**: ``action``, ``reason`` and a ``note``.
Every key except ``action`` is therefore read with ``.get()``. The engine's own
serialiser does exactly this, and a strict key access would raise on exactly the
input that most needs a clean no-trade.

**2. Prices are ``float``, and the engine's own zero means "undefined".**
``TradePlan`` uses ``0.0`` for an absent level rather than ``None``, and its
geometry check skips a zero level rather than comparing it. So a ``0.0`` here
means "there is no stop", not "the stop is at zero". Converting it to a Decimal
and treating it as a price would produce a stop that is wrong by the entire size
of the instrument.

**3. The stop carries its provenance, and that provenance is the stop policy.**
``stop_basis`` is ``PULLBACK_EXTREME``, ``BREAKOUT_REFERENCE``, ``PATTERN_EXTREME``
or ``SWING`` for a level the market produced, and ``ATR_FALLBACK`` or ``NONE``
for a volatility multiple or nothing at all. The engine exposes
``has_structural_stop`` as exactly ``stop_basis not in ("ATR_FALLBACK", "NONE")``
and its own docstring calls a volatility-fallback plan "arithmetically sound and
structurally empty". This module carries that provenance through as
``Signal.stop_basis`` and computes nothing from it; deciding what to trust is
Phase 4's job, and deciding it here would put a trading policy in a converter.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from signal_to_trade_bridge.adapters.albrooks.identity import compute_signal_id
from signal_to_trade_bridge.domain.enums import Direction, SignalAction
from signal_to_trade_bridge.domain.models import Signal

__all__ = [
    "MAPPED_FIELDS",
    "SOURCE_NAME",
    "AnalysisOutcome",
    "map_result_to_signal",
    "source_action",
    "source_direction",
]

#: Recorded on every mapped signal, so a decision log says where the reading came
#: from without a reader having to know the class name.
SOURCE_NAME = "albrooks"

#: The upstream decision keys this module reads. Named rather than inlined, so a
#: test can assert that the list and the code agree -- an upstream rename that
#: silently stopped being read would otherwise produce signals with missing
#: fields rather than an error.
MAPPED_FIELDS: tuple[str, ...] = (
    "action",
    "reason",
    "subject",
    "direction",
    "plan",
    "evidence",
)

#: The upstream action strings, mapped. The engine has no ``FLAT`` action; the
#: two abstentions are kept distinct because they are different claims about the
#: market, and a log that cannot say which one occurred is a log that cannot
#: explain why nothing happened.
_ACTION_MAP: dict[str, SignalAction] = {
    "BUY": SignalAction.BUY,
    "SELL": SignalAction.SELL,
    "WAIT": SignalAction.WAIT,
    "NO_TRADE": SignalAction.NO_TRADE,
}

#: Plan keys read from inside ``decision["plan"]``.
_PLAN_FIELDS: tuple[str, ...] = (
    "entry",
    "stop",
    "target",
    "stop_basis",
    "target_basis",
    "entry_basis",
)

#: Upstream stop bases that mean "derived from something the market actually
#: did". Mirrors the engine's own ``has_structural_stop``. Recorded for the reader
#: and for Phase 4; deliberately not acted on here.
STRUCTURAL_STOP_BASES: frozenset[str] = frozenset(
    {"PULLBACK_EXTREME", "BREAKOUT_REFERENCE", "PATTERN_EXTREME", "SWING"}
)

#: Upstream target bases that mean a level the market produced, as opposed to a
#: volatility multiple. Mirrors the equivalent stop distinction.
STRUCTURAL_TARGET_BASES: frozenset[str] = frozenset({"MEASURED_MOVE", "FADE_ORIGIN", "SWING"})

#: The plan-level issues that mean the geometry is unusable. The engine's own
#: ``BLOCKING_ISSUES`` set; restated rather than imported so this module can be
#: read and tested without the upstream package installed, which is the property
#: that keeps the whole suite runnable on any machine.
GEOMETRY_ISSUES: frozenset[str] = frozenset(
    {
        "NO_DIRECTION",
        "NO_ATR",
        "ENTRY_UNDEFINED",
        "STOP_UNDEFINED",
        "STOP_NOT_PROTECTIVE",
        "TARGET_UNDEFINED",
        "TARGET_NOT_AHEAD",
        "RISK_NOT_POSITIVE",
    }
)


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """What the adapter understood of one analysis result.

    A signal, or the reason there is none. Returning a reason rather than raising
    is deliberate: "the engine found no setup" and "the engine returned a plan
    with no entry" are both normal, frequent outcomes, and an adapter that raised
    for them would make the commonest case look like a failure.

    ``degenerate`` distinguishes the upstream's three-key shape from the normal
    one. It is carried so a log can say "no analysis was run" instead of "no
    setup was found" -- the engine is explicit that the first is a statement about
    the input and the second is a statement about the market, and blurring them
    would turn a broken data feed into a quiet trading day.
    """

    signal: Signal | None
    reason: str = ""
    explanation: str = ""
    degenerate: bool = False
    #: The upstream reason code, verbatim. Kept as-is because it is upstream's
    #: vocabulary and renaming it here would make cross-referencing its own
    #: documentation harder for no benefit.
    source_reason: str = ""

    def __bool__(self) -> bool:
        return self.signal is not None


def _to_decimal(value: object, *, field: str) -> Decimal | None:
    """Convert an upstream float to a Decimal, or ``None`` if it is unusable.

    Three things count as unusable, and all three are real: a missing key, a
    ``None``, and a non-finite value. ``NaN`` and ``inf`` are the interesting
    ones -- a float pipeline can produce them, ``Decimal("NaN")`` compares false
    against everything without raising, and a NaN price reaching a position sizer
    would produce a volume that is NaN, which the broker would reject after the
    risk calculation had already been reported as complete.
    """
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def _price(value: object) -> Decimal | None:
    """Convert an upstream price, treating zero as "undefined".

    ``TradePlan`` uses ``0.0`` for an absent level and its own geometry check
    skips zero levels rather than comparing them. Preserving that convention is
    the difference between "there is no stop" and "the stop is at zero", and the
    second would be a stop that is wrong by the entire size of the instrument.
    """
    number = _to_decimal(value, field="price")
    if number is None or number <= 0:
        return None
    return number


def source_action(raw: object) -> SignalAction | None:
    """Map an upstream action string to a :class:`SignalAction`.

    ``None`` for an unrecognised value rather than a default. An upstream engine
    that grew a fifth action must produce a refusal the operator can see, not a
    quiet ``NO_TRADE`` that looks like a considered decision.
    """
    if not isinstance(raw, str):
        return None
    return _ACTION_MAP.get(raw.strip().upper())


def source_direction(raw: object, action: SignalAction) -> Direction:
    """Resolve the direction, preferring the action over the numeric field.

    The action is the primary source because it is the one the engine's decision
    layer actually committed to, and the numeric ``direction`` is a field on the
    same object that is zero whenever the plan named no direction. An
    inconsistency between the two means a bug upstream, and when the two disagree
    the action wins because it is the coarser, more conservative statement.

    An abstention is always ``FLAT``, with no exceptions and no consultation of
    the numeric field. Both abstentions mean "the engine is not asking for a
    trade", and a direction attached to one would be a direction with nothing to
    trade -- which is precisely the state this project refuses to act on. The
    numeric field is not even read in that case, so a stray non-zero value
    upstream cannot turn a quiet market into a position.
    """
    if not action.is_tradable:
        return Direction.FLAT
    return action.direction


def _evidence_score(evidence: object) -> float | None:
    """Read the engine's evidence score, if it reported one.

    **Not a probability of winning.** The engine says so in its output
    (``"is_probability": false``), in its ranking documentation, and in a note
    attached to every plan. It is a concentration of evidence, and no threshold on
    it has been validated against outcomes. It is carried through unchanged so
    the risk service can apply a configured threshold, and it is never
    reinterpreted anywhere in this project.
    """
    if not isinstance(evidence, Mapping):
        return None
    value = evidence.get("value")
    if value is None:
        # Some paths carry only `ppts` (percentage points).
        ppts = evidence.get("ppts")
        value = float(ppts) / 100.0 if isinstance(ppts, (int, float)) else None
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= number <= 1.0:
        # Out of range means the upstream contract changed. Returning None makes
        # the filter skip rather than apply a nonsense bound.
        return None
    return number


def _bar_time(result: object) -> float | None:
    """The close time of the newest bar the analysis was allowed to read.

    Read from ``bar_features`` because that is where the engine's per-bar records
    live, and from the index the analysis actually used rather than the last row:
    the engine deliberately computes features only for bars ``0..last_closed``,
    so the final row of a full series is not the analysed bar.
    """
    features = getattr(result, "bar_features", None)
    if not isinstance(features, list) or not features:
        return None
    last_closed = getattr(result, "last_closed_bar", -1)
    for record in features:
        if isinstance(record, Mapping) and record.get("index") == last_closed:
            time = record.get("time")
            return float(time) if isinstance(time, (int, float)) else None
    return None


def _bar_index(result: object) -> int:
    index = getattr(result, "last_closed_bar", -1)
    return index if isinstance(index, int) else -1


def _abstention_explanation(decision: Mapping[str, Any], source_reason: str) -> str:
    """A one-line account of why the engine declined.

    Prefers the engine's own ordered prose when it supplied any, because it knows
    more about its own reasoning than this adapter could reconstruct, and joins it
    to the stable reason code so a log is both readable and queryable.
    """
    lines = decision.get("explanation")
    prose = (
        " ".join(str(line).strip() for line in lines if str(line).strip())
        if isinstance(lines, (list, tuple))
        else ""
    )
    code = source_reason or "unstated"
    return f"{code}: {prose}" if prose else code


def map_result_to_signal(result: object) -> AnalysisOutcome:
    """Convert an upstream ``AnalysisResult`` into an internal :class:`Signal`.

    Total, in the sense that it returns an :class:`AnalysisOutcome` for any input
    rather than raising. A malformed result produces a reason the operator can
    read, because a converter that raised on bad data would move the failure into
    a loop that has no way to record it.
    """
    decision = getattr(result, "decision", None)
    if not isinstance(decision, Mapping):
        return AnalysisOutcome(
            signal=None,
            reason="SIGNAL_MALFORMED",
            explanation=(
                "the analysis result carried no decision object, so there is nothing to map"
            ),
        )

    # Checked before the action, because "no action at all" and "an action this
    # bridge does not recognise" are different failures. The first means the
    # upstream shape changed; the second means it grew a fifth action. Reporting
    # a missing key as an unknown action would send an operator looking for a new
    # enum member instead of at the contract.
    if "action" not in decision:
        return AnalysisOutcome(
            signal=None,
            reason="SIGNAL_MALFORMED",
            explanation=(
                "the analysis result carried a decision with no action, which is not a shape "
                f"this bridge knows how to read. Decision keys present: {sorted(decision)}"
            ),
        )

    raw_action = decision.get("action")
    action = source_action(raw_action)
    if action is None:
        return AnalysisOutcome(
            signal=None,
            reason="SIGNAL_DIRECTION_UNKNOWN",
            explanation=(
                f"the engine reported an action this bridge does not recognise: {raw_action!r}"
            ),
            source_reason=str(decision.get("reason", "")),
        )

    source_reason = str(decision.get("reason", ""))
    symbol = str(getattr(result, "symbol", "") or "").strip()
    timeframe = str(getattr(result, "timeframe", "") or "").strip()
    bar_index = _bar_index(result)
    bar_time = _bar_time(result)
    direction = source_direction(decision.get("direction", 0), action)
    setup_id = str(decision.get("subject", "") or "").strip()
    evidence_score = _evidence_score(decision.get("evidence"))

    # The degenerate path: no analysis ran. Distinct from "no setup was found",
    # and the distinction is the engine's own -- a short or volatility-free series
    # is a fact about the input, and reporting it as a quiet market would turn a
    # broken data feed into a day of not trading.
    degenerate = "plan" not in decision and "explanation" not in decision
    if degenerate and not action.is_tradable:
        return AnalysisOutcome(
            signal=None,
            reason="SIGNAL_MALFORMED",
            explanation=(
                "the engine ran no analysis, so it reported no signal. "
                f"Upstream reason: {source_reason or 'unstated'}"
            ),
            degenerate=True,
            source_reason=source_reason,
        )

    plan = decision.get("plan")
    entry = stop = target = None
    stop_basis = target_basis = entry_basis = "NONE"
    geometry_issues: tuple[str, ...] = ()

    if isinstance(plan, Mapping):
        entry = _price(plan.get("entry"))
        stop = _price(plan.get("stop"))
        target = _price(plan.get("target"))
        stop_basis = str(plan.get("stop_basis", "NONE") or "NONE").strip().upper()
        target_basis = str(plan.get("target_basis", "NONE") or "NONE").strip().upper()
        entry_basis = str(plan.get("entry_basis", "NONE") or "NONE").strip().upper()
        issues = plan.get("issues")
        if isinstance(issues, (list, tuple)):
            geometry_issues = tuple(str(item) for item in issues)

    if not action.is_tradable:
        # An abstention is a valid signal and is mapped as one, with no plan.
        #
        # Built without touching the plan at all. The upstream engine returns
        # `plan: None` on every abstention, so there is no entry to read and
        # requiring one would make the commonest outcome in the system -- "the
        # engine found nothing to trade" -- unmapable. The risk service refuses it
        # in Phase 6, and it reaches that point carrying the upstream reason,
        # which is what makes a quiet day explicable rather than mysterious.
        return AnalysisOutcome(
            signal=Signal(
                signal_id=compute_signal_id(
                    symbol=symbol,
                    timeframe=timeframe,
                    bar_index=bar_index,
                    bar_time=bar_time,
                    action=action,
                    direction=direction,
                    setup_id=setup_id,
                ),
                symbol=symbol,
                timeframe=timeframe,
                action=action,
                direction=Direction.FLAT,
                # The last close is the only price available on an abstention, and
                # it is recorded as metadata rather than as `entry`, because an
                # abstention has no entry. Reading a last close as an entry price
                # is exactly the substitution this project refuses to make.
                stop_loss=None,
                take_profit=None,
                stop_basis="NONE",
                take_profit_basis="NONE",
                evidence_score=evidence_score,
                setup_id=setup_id,
                bar_index=bar_index,
                bar_time=bar_time,
                source=SOURCE_NAME,
                source_metadata={
                    "source_reason": source_reason,
                    "is_abstention": True,
                    "evidence_is_probability": False,
                    "vetoes": list(decision.get("vetoes") or ()),
                    "considered": dict(decision.get("considered") or {}),
                },
            ),
            source_reason=source_reason,
            explanation=_abstention_explanation(decision, source_reason),
            degenerate=degenerate,
        )

    if entry is None:
        return AnalysisOutcome(
            signal=None,
            reason="SIGNAL_ENTRY_INVALID",
            explanation=(
                f"the engine returned a {action.value} with no usable entry price. "
                "The engine reports an undefined level as 0.0, so this is a plan with "
                "no entry rather than an entry at zero."
            ),
            source_reason=source_reason,
        )

    if not symbol:
        return AnalysisOutcome(
            signal=None,
            reason="SIGNAL_SYMBOL_INVALID",
            explanation="the analysis result carried no symbol, so there is nothing to trade",
            source_reason=source_reason,
        )

    return AnalysisOutcome(
        signal=_build_tradable_signal(
            symbol=symbol,
            timeframe=timeframe,
            bar_index=bar_index,
            bar_time=bar_time,
            action=action,
            direction=direction,
            setup_id=setup_id,
            entry=entry,
            stop=stop,
            target=target,
            stop_basis=stop_basis,
            target_basis=target_basis,
            entry_basis=entry_basis,
            evidence_score=evidence_score,
            metadata={
                "source_reason": source_reason,
                "geometry_issues": list(geometry_issues),
                "has_structural_stop": stop_basis in STRUCTURAL_STOP_BASES,
                "has_structural_target": target_basis in STRUCTURAL_TARGET_BASES,
                "is_abstention": False,
                "evidence_is_probability": False,
            },
        ),
        source_reason=source_reason,
        degenerate=degenerate,
    )


def _build_tradable_signal(
    *,
    symbol: str,
    timeframe: str,
    bar_index: int,
    bar_time: float | None,
    action: SignalAction,
    direction: Direction,
    setup_id: str,
    entry: Decimal,
    stop: Decimal | None,
    target: Decimal | None,
    stop_basis: str,
    target_basis: str,
    entry_basis: str,
    evidence_score: float | None,
    metadata: dict[str, Any],
) -> Signal:
    """Assemble a signal that asks for a trade.

    Takes ``entry`` as a non-optional ``Decimal`` because the caller has already
    established it, and returning ``None`` here would give the caller two ways of
    failing for one reason. The abstention case does not come through here; it is
    built inline above, because it has no entry and no plan.
    """
    return Signal(
        signal_id=compute_signal_id(
            symbol=symbol,
            timeframe=timeframe,
            bar_index=bar_index,
            bar_time=bar_time,
            action=action,
            direction=direction,
            setup_id=setup_id,
        ),
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        direction=direction,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        stop_basis=stop_basis,
        take_profit_basis=target_basis,
        evidence_score=evidence_score,
        setup_id=setup_id,
        bar_index=bar_index,
        bar_time=bar_time,
        source=SOURCE_NAME,
        source_metadata=metadata | {"entry_basis": entry_basis},
    )
