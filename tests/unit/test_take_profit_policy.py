"""Take-profit resolution.

Two things are under test, and the second is the more important one.

The first is arithmetic: a 1:1 target sits exactly one risk distance from entry,
on the correct side, for both directions.

The second is **provenance**: which policy produced the number, and whether a
fallback said so. The brief requires that a signal-defined target is never
silently overridden, and a take profit that is numerically right but
indistinguishable from the engine's own measured move is exactly the failure that
requirement is about.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain.enums import (
    Direction,
    RejectionReason,
    SignalAction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.models import RiskParameters, Signal, StopLoss
from signal_to_trade_bridge.domain.take_profit import (
    STRUCTURAL_TARGET_BASES,
    is_favourable,
    is_structural_target_basis,
    resolve_take_profit,
    target_from_ratio,
)

#: The brief's worked example, as a stop distance: entry 100, stop 99, so one
#: unit of risk and a 1:1 target at 101.
ENTRY = Decimal("100")
STOP_DISTANCE = Decimal("1")


def _stop(direction: Direction = Direction.LONG) -> StopLoss:
    price = ENTRY - STOP_DISTANCE if direction is Direction.LONG else ENTRY + STOP_DISTANCE
    return StopLoss(price=price, distance=STOP_DISTANCE, source=StopSource.SIGNAL)


def _signal(
    *,
    direction: Direction = Direction.LONG,
    target: str | None = "101",
    basis: str = "MEASURED_MOVE",
) -> Signal:
    action = SignalAction.BUY if direction is Direction.LONG else SignalAction.SELL
    return Signal(
        signal_id="stb-test-tp",
        symbol="EURUSD",
        timeframe="H1",
        action=action,
        direction=direction,
        entry=ENTRY,
        stop_loss=(_stop(direction).price),
        stop_basis="PULLBACK_EXTREME",
        take_profit=Decimal(target) if target is not None else None,
        take_profit_basis=basis,
    )


def _risk(source: TakeProfitSource = TakeProfitSource.RR_FALLBACK, **kw: object) -> RiskParameters:
    defaults: dict[str, object] = {
        "risk_percent": Decimal("0.5"),
        "reward_risk_ratio": Decimal("1.0"),
        "take_profit_source": source,
    }
    defaults.update(kw)
    return RiskParameters(**defaults)  # type: ignore[arg-type]


class TestRatioArithmetic:
    def test_a_long_target_is_one_risk_distance_above_entry(self) -> None:
        # The brief's example: entry 100, stop 99, risk 1, target 101.
        target = target_from_ratio(Direction.LONG, ENTRY, STOP_DISTANCE, Decimal("1"))
        assert target == Decimal("101")

    def test_a_short_target_is_one_risk_distance_below_entry(self) -> None:
        # entry 100, stop 101, risk 1, target 99.
        target = target_from_ratio(Direction.SHORT, ENTRY, STOP_DISTANCE, Decimal("1"))
        assert target == Decimal("99")

    @pytest.mark.parametrize("ratio", ["0.5", "1", "1.5", "2", "3"])
    def test_the_ratio_scales_the_reward(self, ratio: str) -> None:
        target = target_from_ratio(Direction.LONG, ENTRY, STOP_DISTANCE, Decimal(ratio))
        assert target == ENTRY + STOP_DISTANCE * Decimal(ratio)

    def test_a_half_ratio_target_is_reachable(self) -> None:
        # A sub-1:1 target is a legitimate configuration. The brief asks for 1:1
        # as the starting point, not as a minimum.
        target = target_from_ratio(Direction.LONG, ENTRY, STOP_DISTANCE, Decimal("0.5"))
        assert target == Decimal("100.5")

    def test_a_non_tradable_direction_yields_nothing(self) -> None:
        # `None` rather than an exception: the caller has a refusal path already
        # and does not need a second way to fail.
        assert target_from_ratio(Direction.FLAT, ENTRY, STOP_DISTANCE, Decimal("1")) is None

    def test_a_zero_distance_yields_nothing(self) -> None:
        assert target_from_ratio(Direction.LONG, ENTRY, Decimal("0"), Decimal("1")) is None

    def test_a_zero_ratio_yields_nothing(self) -> None:
        assert target_from_ratio(Direction.LONG, ENTRY, STOP_DISTANCE, Decimal("0")) is None


class TestFavourableSide:
    def test_a_long_target_must_be_above_entry(self) -> None:
        assert is_favourable(Direction.LONG, ENTRY, Decimal("101"))
        assert not is_favourable(Direction.LONG, ENTRY, Decimal("99"))

    def test_a_short_target_must_be_below_entry(self) -> None:
        assert is_favourable(Direction.SHORT, ENTRY, Decimal("99"))
        assert not is_favourable(Direction.SHORT, ENTRY, Decimal("101"))

    def test_a_target_at_entry_is_not_favourable(self) -> None:
        assert not is_favourable(Direction.LONG, ENTRY, ENTRY)
        assert not is_favourable(Direction.SHORT, ENTRY, ENTRY)

    def test_a_flat_direction_is_never_favourable(self) -> None:
        assert not is_favourable(Direction.FLAT, ENTRY, Decimal("101"))


class TestTargetBases:
    @pytest.mark.parametrize("basis", ["MEASURED_MOVE", "FADE_ORIGIN", "SWING"])
    def test_a_level_the_market_produced_is_structural(self, basis: str) -> None:
        assert is_structural_target_basis(basis)

    @pytest.mark.parametrize("basis", ["ATR_FALLBACK", "NONE", "", "MYSTERY"])
    def test_anything_else_is_not(self, basis: str) -> None:
        assert not is_structural_target_basis(basis)

    def test_the_bases_mirror_the_stop_bases_in_shape(self) -> None:
        # Four structural stop bases, three structural target bases. They differ
        # because the upstream families differ, not because of an inconsistency
        # here -- so this asserts the count rather than pretending they match.
        assert len(STRUCTURAL_TARGET_BASES) == 3
        assert "MEASURED_MOVE" in STRUCTURAL_TARGET_BASES


class TestRRLFallbackPolicy:
    def test_a_usable_signal_target_is_preferred(self) -> None:
        resolution = resolve_take_profit(_signal(), _stop(), _risk())
        take_profit = resolution.unwrap()
        assert take_profit.source is TakeProfitSource.SIGNAL
        assert take_profit.price == Decimal("101")

    def test_the_policy_name_is_recorded(self) -> None:
        assert resolve_take_profit(_signal(), _stop(), _risk()).details["policy"] == ("RR_FALLBACK")

    def test_a_missing_signal_target_falls_back_to_the_ratio(self) -> None:
        resolution = resolve_take_profit(_signal(target=None), _stop(), _risk())
        take_profit = resolution.unwrap()
        assert take_profit.source is TakeProfitSource.RR_FALLBACK
        assert take_profit.price == Decimal("101")

    def test_the_fallback_is_never_silent(self) -> None:
        # The single most important assertion in this file. A 1:1 target that
        # looked like the engine's own measured move would be a decision nobody
        # could audit.
        resolution = resolve_take_profit(_signal(target=None), _stop(), _risk())
        assert "fallback_because" in resolution.details
        assert resolution.details["fallback_because"]

    def test_a_volatility_target_falls_back_and_says_why(self) -> None:
        resolution = resolve_take_profit(_signal(basis="ATR_FALLBACK"), _stop(), _risk())
        take_profit = resolution.unwrap()
        assert take_profit.source is TakeProfitSource.RR_FALLBACK
        assert "volatility multiple" in str(resolution.details["fallback_because"])

    def test_a_wrong_side_target_falls_back_and_says_why(self) -> None:
        # A long with a target below entry: the engine reported a target the
        # direction cannot reach. The ratio is used, and the log says the signal's
        # target was unusable rather than that it was absent.
        resolution = resolve_take_profit(_signal(target="99"), _stop(), _risk())
        assert resolution.unwrap().source is TakeProfitSource.RR_FALLBACK
        assert "not above the entry" in str(resolution.details["fallback_because"])

    def test_the_fallback_uses_the_configured_ratio(self) -> None:
        risk = _risk(TakeProfitSource.RR_FALLBACK, reward_risk_ratio=Decimal("2"))
        take_profit = resolve_take_profit(_signal(target=None), _stop(), risk).unwrap()
        assert take_profit.price == Decimal("102")
        assert take_profit.distance == Decimal("2")

    def test_the_achieved_ratio_is_reported(self) -> None:
        # The ratio the trade actually has, which can differ from the configured
        # one when a signal target was used. An intention, reported as a fact.
        resolution = resolve_take_profit(_signal(), _stop(), _risk())
        assert resolution.details["achieved_ratio"] == "1"

    def test_a_signal_target_off_the_configured_ratio_reports_the_achieved_one(self) -> None:
        risk = _risk(TakeProfitSource.RR_FALLBACK, reward_risk_ratio=Decimal("1"))
        resolution = resolve_take_profit(_signal(target="103"), _stop(), risk)
        assert resolution.details["achieved_ratio"] == "3"
        # The configured ratio is still 1.0. The achieved one is what happened.
        assert risk.reward_risk_ratio == Decimal("1")


class TestSignalPolicy:
    def test_a_usable_signal_target_is_used(self) -> None:
        take_profit = resolve_take_profit(
            _signal(), _stop(), _risk(TakeProfitSource.SIGNAL)
        ).unwrap()
        assert take_profit.source is TakeProfitSource.SIGNAL

    def test_a_missing_target_is_refused_rather_than_computed(self) -> None:
        # The strict policy's whole point: if the engine did not name a target,
        # this bridge does not invent one either.
        resolution = resolve_take_profit(
            _signal(target=None), _stop(), _risk(TakeProfitSource.SIGNAL)
        )
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT

    def test_the_message_names_the_alternative_policy(self) -> None:
        # An operator should be able to act on the refusal without reading code.
        resolution = resolve_take_profit(
            _signal(target=None), _stop(), _risk(TakeProfitSource.SIGNAL)
        )
        assert "RR_FALLBACK" in resolution.explanation

    def test_a_volatility_target_is_refused(self) -> None:
        resolution = resolve_take_profit(
            _signal(basis="ATR_FALLBACK"), _stop(), _risk(TakeProfitSource.SIGNAL)
        )
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT

    def test_a_wrong_side_target_is_refused(self) -> None:
        resolution = resolve_take_profit(
            _signal(target="99"), _stop(), _risk(TakeProfitSource.SIGNAL)
        )
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT

    def test_the_unusable_reason_is_carried_into_the_refusal(self) -> None:
        resolution = resolve_take_profit(
            _signal(target=None), _stop(), _risk(TakeProfitSource.SIGNAL)
        )
        assert "no take profit" in str(resolution.details["unusable_because"])


class TestRRDerivedPolicy:
    def test_the_ratio_wins_over_a_signal_target(self) -> None:
        # A trader who has read the engine's own caveats about its unvalidated
        # targets may want exactly 1:1 and nothing else. That is a different
        # intention from the default, and it deserves a different policy.
        take_profit = resolve_take_profit(
            _signal(target="110"), _stop(), _risk(TakeProfitSource.RR_DERIVED)
        ).unwrap()
        assert take_profit.source is TakeProfitSource.RR_DERIVED
        assert take_profit.price == Decimal("101")

    def test_the_ignored_target_is_recorded(self) -> None:
        # Never silently discarded. A log that did not say the engine asked for
        # 110 and was refused would leave a genuine disagreement invisible.
        resolution = resolve_take_profit(
            _signal(target="110"), _stop(), _risk(TakeProfitSource.RR_DERIVED)
        )
        assert resolution.details["signal_target_ignored"] == "110"

    def test_no_fallback_reason_is_recorded_because_none_happened(self) -> None:
        # Under this policy nothing is a fallback, so recording one would be a
        # lie in the log.
        resolution = resolve_take_profit(
            _signal(target="110"), _stop(), _risk(TakeProfitSource.RR_DERIVED)
        )
        assert "fallback_because" not in resolution.details


class TestNonePolicy:
    def test_no_target_is_a_success_not_a_refusal(self) -> None:
        # An operator who has deliberately disabled targets has not hit a
        # problem, and a log full of refusals for a deliberate configuration would
        # be noise that trains people to ignore the reason codes.
        resolution = resolve_take_profit(_signal(), _stop(), _risk(TakeProfitSource.NONE))
        assert resolution.ok
        assert resolution.value is None
        assert resolution.reason_code == "OK"

    def test_the_signal_target_is_ignored_entirely(self) -> None:
        resolution = resolve_take_profit(
            _signal(target="110"), _stop(), _risk(TakeProfitSource.NONE)
        )
        assert resolution.value is None

    def test_the_disabled_state_is_recorded_distinctly(self) -> None:
        resolution = resolve_take_profit(_signal(), _stop(), _risk(TakeProfitSource.NONE))
        assert resolution.details["reason_code"] == "TARGET_DISABLED"


class TestShortDirection:
    def test_a_short_resolves_below_entry(self) -> None:
        resolution = resolve_take_profit(
            _signal(direction=Direction.SHORT, target="99"),
            _stop(Direction.SHORT),
            _risk(),
        )
        take_profit = resolution.unwrap()
        assert take_profit.source is TakeProfitSource.SIGNAL
        assert take_profit.price == Decimal("99")
        assert take_profit.price < ENTRY

    def test_a_short_fallback_is_below_entry(self) -> None:
        take_profit = resolve_take_profit(
            _signal(direction=Direction.SHORT, target=None),
            _stop(Direction.SHORT),
            _risk(),
        ).unwrap()
        assert take_profit.source is TakeProfitSource.RR_FALLBACK
        assert take_profit.price == Decimal("99")

    def test_a_short_with_a_target_above_entry_falls_back(self) -> None:
        take_profit = resolve_take_profit(
            _signal(direction=Direction.SHORT, target="101"),
            _stop(Direction.SHORT),
            _risk(),
        ).unwrap()
        assert take_profit.source is TakeProfitSource.RR_FALLBACK
        assert take_profit.price == Decimal("99")


class TestMissingEntry:
    def test_a_signal_without_an_entry_is_refused(self) -> None:
        # Only reachable by bypassing `Signal`'s own validation, checked anyway
        # because this function is public and is reached from a deserialisation
        # path in a later phase.
        from signal_to_trade_bridge.domain.enums import SignalAction

        signal = Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.WAIT,
            direction=Direction.FLAT,
            entry=None,
        )
        resolution = resolve_take_profit(signal, _stop(), _risk())
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT


class TestNeverSilent:
    def test_every_resolved_target_records_its_source(self) -> None:
        # The property the whole policy set exists to guarantee: no take profit
        # can reach a decision without saying where it came from.
        for source in TakeProfitSource:
            for target, basis in (("101", "MEASURED_MOVE"), ("110", "SWING"), (None, "NONE")):
                resolution = resolve_take_profit(
                    _signal(target=target, basis=basis), _stop(), _risk(source)
                )
                if not resolution.ok:
                    # The strict `SIGNAL` policy legitimately refuses a signal
                    # with no target, and `NONE` legitimately resolves to nothing.
                    # Both are recorded, so the property still holds.
                    assert resolution.reason is not None
                    continue
                take_profit = resolution.value
                if take_profit is None:
                    continue  # TakeProfitSource.NONE, resolved to no target.
                assert take_profit.source in (
                    TakeProfitSource.SIGNAL,
                    TakeProfitSource.RR_FALLBACK,
                    TakeProfitSource.RR_DERIVED,
                ), f"{source} produced a target with no recorded source"
                assert take_profit.basis, f"{source} produced a target with no recorded basis"

    def test_the_chosen_policy_is_always_in_the_details(self) -> None:
        for source in TakeProfitSource:
            resolution = resolve_take_profit(_signal(), _stop(), _risk(source))
            assert resolution.details["policy"] == source.value
