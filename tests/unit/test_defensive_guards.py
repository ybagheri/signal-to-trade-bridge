"""Defensive guards that only a hand-built object can reach.

The models validate themselves, so most of these branches are unreachable
through the normal constructors. They are tested anyway, by building objects that
bypass construction.

The reason is not coverage. It is that a guard which has never been executed is
a guard nobody knows works, and these are the guards standing between a bad
number and a position sizer. If a future refactor weakened ``StopLoss``'s
validation -- or a signal arrived through a deserialisation path that skipped it
-- these branches are the ones that would have to hold.

Each test names what it is bypassing and why that is legitimate.
"""

from __future__ import annotations

from decimal import Decimal

from signal_to_trade_bridge.domain.enums import (
    Direction,
    RejectionReason,
    SignalAction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.models import (
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
)
from signal_to_trade_bridge.domain.stops import resolve_stop
from signal_to_trade_bridge.domain.take_profit import resolve_take_profit
from signal_to_trade_bridge.domain.validation import validate_against_spec, validate_geometry


def _risk() -> RiskParameters:
    return RiskParameters(
        risk_percent=Decimal("0.5"),
        reward_risk_ratio=Decimal("1.0"),
        take_profit_source=TakeProfitSource.RR_FALLBACK,
    )


def _bypass(cls: type, **fields: object) -> object:
    """Build a frozen slotted model without running its validation.

    The models are frozen with ``slots=True`` precisely so that they cannot be
    mutated after construction. That is the right property for a value object and
    it is exactly what stops a test from constructing an invalid one the ordinary
    way -- so this is the only way to reach the guards those tests cannot.

    **Every field has to be listed.** ``object.__new__`` leaves the slots unset, so
    omitting one produces an ``AttributeError`` the first time the model reads it
    rather than a clean failure -- which is why adding a field to a model breaks
    these tests loudly. That is the intended signal: a model with a new field has
    new guards to reach, or at least new places a bypassed object can be wrong.
    """
    instance = object.__new__(cls)
    for name, value in fields.items():
        object.__setattr__(instance, name, value)
    return instance


class TestStopWithoutAnEntry:
    def test_resolving_a_stop_for_an_entryless_signal_refuses(self) -> None:
        # `Signal` requires an entry for a tradable action, so this needs an
        # object built past its own validation -- the situation a deserialisation
        # path that skipped construction would produce.
        signal = _bypass(
            Signal,
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=None,
            stop_loss=Decimal("1.09700"),
            stop_basis="PULLBACK_EXTREME",
            evidence_score=None,
            setup_id="",
            bar_index=0,
            bar_time=None,
            source_metadata={},
        )
        resolution = resolve_stop(signal, _risk())  # type: ignore[arg-type]
        assert resolution.reason is RejectionReason.NO_VALID_STOP
        assert "no entry price" in resolution.explanation


class TestTakeProfitWithAnImpossibleRatio:
    def test_an_impossible_ratio_refuses_rather_than_dividing_by_zero(self) -> None:
        # `RiskParameters` refuses a non-positive ratio, so this needs a
        # hand-built one. The guard exists because a future refactor could relax
        # that validation, and the failure mode without this check is a
        # `ZeroDivisionError` raised from inside a trading loop.
        risk = _bypass(
            RiskParameters,
            risk_percent=Decimal("0.5"),
            reward_risk_ratio=Decimal("0"),
            take_profit_source=TakeProfitSource.RR_DERIVED,
            allow_volatility_fallback_stop=False,
            minimum_evidence_score=None,
            allow_buy=True,
            allow_sell=True,
            allowed_symbols=frozenset(),
            max_spread=None,
            max_open_positions=None,
            minimum_reward_risk_ratio=None,
        )
        signal = Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
        )
        stop = StopLoss(price=Decimal("1.09700"), distance=Decimal("0.00300"))
        resolution = resolve_take_profit(signal, stop, risk)  # type: ignore[arg-type]
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT
        assert "bug rather than a market condition" in resolution.explanation


class TestNonPositiveLevelsInGeometry:
    def _signal(self) -> Signal:
        return Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
        )

    def test_a_non_positive_stop_price_is_refused(self) -> None:
        # `StopLoss` refuses this at construction. The check here is the second
        # line, and it matters because the geometry can come from configuration
        # as well as from a signal.
        stop = _bypass(
            StopLoss,
            price=Decimal("0"),
            distance=Decimal("0.00300"),
            source=StopSource.SIGNAL,
            basis="",
        )
        resolution = validate_geometry(self._signal(), stop, None)  # type: ignore[arg-type]
        assert resolution.reason is RejectionReason.NO_VALID_STOP
        assert "not a tradable level" in resolution.explanation

    def test_a_non_positive_target_price_is_refused(self) -> None:
        stop = StopLoss(price=Decimal("1.09700"), distance=Decimal("0.00300"))
        target = _bypass(
            TakeProfit,
            price=Decimal("0"),
            distance=Decimal("0.00300"),
            source=TakeProfitSource.RR_DERIVED,
            basis="",
        )
        resolution = validate_geometry(self._signal(), stop, target)  # type: ignore[arg-type]
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT
        assert "not a tradable level" in resolution.explanation

    def test_a_zero_target_distance_is_refused(self) -> None:
        # A target at the entry is unreachable for a gain, and `TakeProfit`
        # refuses the zero distance at construction. Second line, same reason.
        stop = StopLoss(price=Decimal("1.09700"), distance=Decimal("0.00300"))
        target = _bypass(
            TakeProfit,
            price=Decimal("1.10000"),
            distance=Decimal("0"),
            source=TakeProfitSource.RR_DERIVED,
            basis="",
        )
        resolution = validate_geometry(self._signal(), stop, target)  # type: ignore[arg-type]
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT


class TestUnusableSymbolSpec:
    def test_a_zero_tick_size_is_refused(self) -> None:
        # `SymbolSpec` refuses this at construction. Without the guard here, the
        # modulus used for the off-tick check would raise `ZeroDivisionError` --
        # which is the whole reason the check exists before the arithmetic.
        spec = _bypass(
            SymbolSpec,
            symbol="EURUSD",
            contract_size=Decimal("100000"),
            tick_size=Decimal("0"),
            tick_value_profit=Decimal("1"),
            tick_value_loss=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100"),
            volume_step=Decimal("0.01"),
            digits=5,
            point=Decimal("0.00001"),
            currency="",
            currency_profit="",
            currency_margin="",
        )
        resolution = validate_against_spec(
            Decimal("1.10000"),
            Decimal("1.09700"),
            None,
            spec,  # type: ignore[arg-type]
        )
        assert resolution.reason is RejectionReason.SYMBOL_SPEC_UNAVAILABLE

    def test_an_off_tick_target_is_recorded(self) -> None:
        # The one branch here that a well-formed spec reaches, and the reason the
        # check exists: a broker will round an off-tick target, and a rounded
        # target means the executed reward is not the reward that was calculated.
        spec = SymbolSpec(
            symbol="EURUSD",
            contract_size=Decimal("100000"),
            tick_size=Decimal("0.00001"),
            tick_value_profit=Decimal("1"),
            tick_value_loss=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100"),
            volume_step=Decimal("0.01"),
            digits=5,
            point=Decimal("0.00001"),
        )
        target = TakeProfit(price=Decimal("1.103005"), distance=Decimal("0.003005"))
        resolution = validate_against_spec(Decimal("1.10000"), Decimal("1.09700"), target, spec)
        assert resolution.ok
        assert "take_profit_off_tick" in resolution.details
