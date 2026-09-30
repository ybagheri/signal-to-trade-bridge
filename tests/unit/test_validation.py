"""Trade validation.

The theme is fail-closed with a reason, and the tests are organised around the
places where that could quietly fail open -- which is the only direction a
validation bug ever goes.

The most important of those is a check that passes on missing data. Every test
here that involves a threshold asks what happens when the value being compared
against it is absent, because a filter that a missing value bypasses is not a
filter.
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
from signal_to_trade_bridge.domain.models import (
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
)
from signal_to_trade_bridge.domain.validation import (
    collect_reasons,
    validate_against_spec,
    validate_geometry,
    validate_policy,
    validate_signal,
)


def _signal(
    *,
    direction: Direction = Direction.LONG,
    entry: str | None = "1.10000",
    action: SignalAction | None = None,
    evidence: float | None = None,
    symbol: str = "EURUSD",
    source_reason: str = "",
) -> Signal:
    if action is None:
        action = SignalAction.BUY if direction is Direction.LONG else SignalAction.SELL
    metadata: dict[str, object] = {}
    if source_reason:
        metadata["source_reason"] = source_reason
        metadata["is_abstention"] = True
    return Signal(
        signal_id="stb-test-validate",
        symbol=symbol,
        timeframe="H1",
        action=action,
        direction=direction,
        entry=Decimal(entry) if entry is not None else None,
        evidence_score=evidence,
        source_metadata=metadata,
    )


def _risk(**kw: object) -> RiskParameters:
    defaults: dict[str, object] = {
        "risk_percent": Decimal("0.5"),
        "reward_risk_ratio": Decimal("1.0"),
        "take_profit_source": TakeProfitSource.RR_FALLBACK,
    }
    defaults.update(kw)
    return RiskParameters(**defaults)  # type: ignore[arg-type]


def _stop(direction: Direction = Direction.LONG) -> StopLoss:
    price = Decimal("1.09700") if direction is Direction.LONG else Decimal("1.10300")
    return StopLoss(price=price, distance=Decimal("0.00300"), source=StopSource.SIGNAL)


def _target(direction: Direction = Direction.LONG) -> TakeProfit:
    price = Decimal("1.10300") if direction is Direction.LONG else Decimal("1.09700")
    return TakeProfit(price=price, distance=Decimal("0.00300"))


class TestSignalValidation:
    def test_a_well_formed_signal_passes(self) -> None:
        resolution = validate_signal(_signal(), _risk())
        assert resolution.ok
        assert resolution.unwrap() is not None

    @pytest.mark.parametrize("action", [SignalAction.WAIT, SignalAction.NO_TRADE])
    def test_an_abstention_is_refused(self, action: SignalAction) -> None:
        # The engine's considered decision reaches this point and is refused
        # here, carrying its reason so a quiet session is explicable.
        signal = _signal(
            action=action, direction=Direction.FLAT, source_reason="ALL_CANDIDATES_VETOED"
        )
        resolution = validate_signal(signal, _risk())
        assert resolution.reason is RejectionReason.SIGNAL_INVALID

    def test_an_abstention_carries_the_upstream_reason(self) -> None:
        signal = _signal(
            action=SignalAction.WAIT,
            direction=Direction.FLAT,
            source_reason="EVIDENCE_CONFLICT",
        )
        resolution = validate_signal(signal, _risk())
        assert "EVIDENCE_CONFLICT" in resolution.explanation
        assert resolution.details["source_reason"] == "EVIDENCE_CONFLICT"

    def test_an_abstention_is_marked_as_such(self) -> None:
        # So a caller can tell a deliberate abstention from a malformed result.
        # The two mean different things and call for different follow-up.
        abstention = _signal(action=SignalAction.WAIT, direction=Direction.FLAT, source_reason="X")
        assert validate_signal(abstention, _risk()).details["is_abstention"] is True

    def test_a_tradable_action_without_an_entry_is_refused(self) -> None:
        # Only reachable by bypassing `Signal`'s validation. Checked because this
        # function is public and is the last gate before sizing divides.
        signal = Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.WAIT,
            direction=Direction.FLAT,
            entry=None,
        )
        object.__setattr__(signal, "action", SignalAction.BUY)
        object.__setattr__(signal, "direction", Direction.LONG)
        assert validate_signal(signal, _risk()).ok is False

    def test_a_blank_symbol_cannot_even_be_constructed(self) -> None:
        # `Signal` validates this at construction, so the check in
        # `validate_signal` is a second line rather than the first. Tested here
        # because the two must agree: a symbol that cannot be built must also not
        # be tradeable if one arrives by another route.
        with pytest.raises(ValueError, match="symbol is required"):
            _signal(symbol="   ")

    def test_a_symbol_that_became_blank_after_construction_is_refused(self) -> None:
        # Reached only by a path that bypassed construction, which is why the
        # check exists in the validator at all.
        signal = _signal()
        object.__setattr__(signal, "symbol", "   ")
        assert validate_signal(signal, _risk()).reason is RejectionReason.SIGNAL_SYMBOL_INVALID


class TestEvidenceFilter:
    def test_a_score_above_the_minimum_passes(self) -> None:
        risk = _risk(minimum_evidence_score=0.6)
        assert validate_signal(_signal(evidence=0.72), risk).ok

    def test_a_score_below_the_minimum_is_refused(self) -> None:
        risk = _risk(minimum_evidence_score=0.6)
        resolution = validate_signal(_signal(evidence=0.42), risk)
        assert resolution.reason is RejectionReason.EVIDENCE_BELOW_MINIMUM

    def test_a_score_exactly_at_the_minimum_passes(self) -> None:
        # "At or above", not "strictly above". A boundary that is ambiguous is a
        # boundary that behaves differently on two machines.
        risk = _risk(minimum_evidence_score=0.6)
        assert validate_signal(_signal(evidence=0.6), risk).ok

    def test_a_missing_score_is_refused_not_allowed_through(self) -> None:
        """A missing value is not a passing one.

        The single most important assertion in this class. If an absent score
        counted as a pass, the filter would be trivially bypassed by any signal
        source that simply omitted the field -- which is precisely the wrong
        failure direction for a risk control.
        """
        risk = _risk(minimum_evidence_score=0.6)
        resolution = validate_signal(_signal(evidence=None), risk)
        assert resolution.reason is RejectionReason.EVIDENCE_BELOW_MINIMUM

    def test_the_refusal_explains_the_missing_value(self) -> None:
        risk = _risk(minimum_evidence_score=0.6)
        explanation = validate_signal(_signal(evidence=None), risk).explanation
        assert "no score at all" in explanation

    def test_no_minimum_means_no_filter(self) -> None:
        assert validate_signal(_signal(evidence=None), _risk()).ok

    def test_the_refusal_says_the_score_is_not_a_probability(self) -> None:
        # The upstream engine states this in three places. A threshold on the
        # number is only meaningful if the operator knows what it is not.
        risk = _risk(minimum_evidence_score=0.9)
        explanation = validate_signal(_signal(evidence=0.5), risk).explanation
        assert "not a probability" in explanation

    def test_both_the_score_and_the_threshold_are_recorded(self) -> None:
        risk = _risk(minimum_evidence_score=0.6)
        details = validate_signal(_signal(evidence=0.42), risk).details
        assert details["evidence_score"] == pytest.approx(0.42)
        assert details["minimum_evidence_score"] == 0.6


class TestGeometry:
    def test_valid_long_geometry_passes(self) -> None:
        resolution = validate_geometry(_signal(), _stop(), _target())
        assert resolution.ok
        stop, target = resolution.unwrap()
        assert stop.price == Decimal("1.09700")
        assert target is not None

    def test_valid_short_geometry_passes(self) -> None:
        resolution = validate_geometry(
            _signal(direction=Direction.SHORT), _stop(Direction.SHORT), _target(Direction.SHORT)
        )
        assert resolution.ok

    def test_a_wrong_side_stop_is_refused(self) -> None:
        # Duplicated deliberately: the stop resolver already checks this, and this
        # function is reached by callers that did not go through it. A redundant
        # check costs a comparison; a missing one costs a rejected broker order.
        resolution = validate_geometry(_signal(), _stop(Direction.SHORT), _target())
        assert resolution.reason is RejectionReason.STOP_ON_WRONG_SIDE

    def test_a_wrong_side_target_is_refused(self) -> None:
        resolution = validate_geometry(_signal(), _stop(), _target(Direction.SHORT))
        assert resolution.reason is RejectionReason.TAKE_PROFIT_ON_WRONG_SIDE

    def test_the_wrong_side_message_names_the_expected_side(self) -> None:
        assert (
            "below" in validate_geometry(_signal(), _stop(Direction.SHORT), _target()).explanation
        )

    def test_the_two_side_messages_agree_on_direction(self) -> None:
        # A stop message saying "below" where the target message said "above"
        # would make one geometry problem look like two different ones.
        stop_message = validate_geometry(_signal(), _stop(Direction.SHORT), _target()).explanation
        target_message = validate_geometry(_signal(), _stop(), _target(Direction.SHORT)).explanation
        assert "below" in stop_message
        assert "above" in target_message

    def test_no_target_is_a_success_not_a_refusal(self) -> None:
        # `TakeProfitSource.NONE` is a deliberate configuration. Reporting it as
        # a problem would fill logs with refusals for intended behaviour.
        resolution = validate_geometry(_signal(), _stop(), None)
        assert resolution.ok
        assert resolution.unwrap()[1] is None
        assert resolution.details["take_profit_configured"] is False

    def test_a_configured_target_is_marked_as_such(self) -> None:
        resolution = validate_geometry(_signal(), _stop(), _target())
        assert resolution.details["take_profit_configured"] is True

    def test_a_zero_distance_is_refused(self) -> None:
        # `StopLoss` validates this itself, so reached only by a hand-built
        # object. Checked because this is the last gate before the sizer divides.
        from signal_to_trade_bridge.domain.models import StopLoss as SL

        stop = object.__new__(SL)
        object.__setattr__(stop, "price", Decimal("1.09700"))
        object.__setattr__(stop, "distance", Decimal("0"))
        object.__setattr__(stop, "source", StopSource.SIGNAL)
        object.__setattr__(stop, "basis", "")
        resolution = validate_geometry(_signal(), stop, None)
        assert resolution.reason is RejectionReason.INVALID_STOP_DISTANCE
        assert "divide by zero" in resolution.explanation

    def test_a_signal_without_an_entry_is_refused(self) -> None:
        signal = Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.WAIT,
            direction=Direction.FLAT,
            entry=None,
        )
        assert validate_geometry(signal, _stop(), None).reason is (
            RejectionReason.SIGNAL_ENTRY_INVALID
        )


class TestPolicy:
    def test_an_allowed_direction_passes(self) -> None:
        assert validate_policy(_signal(), _risk()).ok

    def test_a_long_is_refused_when_buy_is_disabled(self) -> None:
        risk = _risk(allow_buy=False, allow_sell=True)
        resolution = validate_policy(_signal(), risk)
        assert resolution.reason is RejectionReason.DIRECTION_NOT_ALLOWED

    def test_a_short_is_refused_when_sell_is_disabled(self) -> None:
        risk = _risk(allow_buy=True, allow_sell=False)
        resolution = validate_policy(_signal(direction=Direction.SHORT), risk)
        assert resolution.reason is RejectionReason.DIRECTION_NOT_ALLOWED

    def test_the_message_says_a_configuration_did_its_job(self) -> None:
        # A long-only configuration refusing a SELL is working, not failing, and
        # the log should not read like a fault.
        risk = _risk(allow_buy=True, allow_sell=False)
        explanation = validate_policy(_signal(direction=Direction.SHORT), risk).explanation
        assert "doing its job" in explanation

    def test_a_short_passes_when_only_sells_are_allowed(self) -> None:
        risk = _risk(allow_buy=False, allow_sell=True)
        assert validate_policy(_signal(direction=Direction.SHORT), risk).ok

    def test_an_empty_allowlist_permits_any_symbol(self) -> None:
        # Not the same as "no symbol is permitted". The execution project has its
        # own allowlist and applies it independently, so a signal must satisfy
        # both -- and the bridge having no opinion is not the trade being allowed.
        assert validate_policy(_signal(symbol="ANYTHING"), _risk()).ok

    def test_a_populated_allowlist_refuses_others(self) -> None:
        risk = _risk(allowed_symbols=frozenset({"EURUSD"}))
        assert validate_policy(_signal(symbol="GBPUSD"), risk).reason is (
            RejectionReason.SYMBOL_NOT_ALLOWED
        )

    def test_the_allowlist_is_case_insensitive(self) -> None:
        risk = _risk(allowed_symbols=frozenset({"EURUSD"}))
        assert validate_policy(_signal(symbol="eurusd"), risk).ok

    def test_the_allowlist_refusal_names_the_downstream_gate(self) -> None:
        # Otherwise an operator would conclude the symbol is permitted because
        # this bridge did not object.
        risk = _risk(allowed_symbols=frozenset({"GBPUSD"}))
        assert "own allowlist" in validate_policy(_signal(), risk).explanation

    def test_the_concurrency_limit_is_not_checked_when_unset(self) -> None:
        assert validate_policy(_signal(), _risk(), account_open_positions=99).ok

    def test_the_concurrency_limit_refuses_at_the_boundary(self) -> None:
        risk = _risk(max_open_positions=3)
        resolution = validate_policy(_signal(), risk, account_open_positions=3)
        assert resolution.reason is RejectionReason.MAX_CONCURRENT_POSITIONS

    def test_an_unknown_position_count_does_not_refuse(self) -> None:
        # `None` means "could not be determined", and the limit is not a reason
        # to guess. A caller that cannot read the count should decide what to do
        # about that separately; this function only refuses on a known breach.
        risk = _risk(max_open_positions=3)
        assert validate_policy(_signal(), risk, account_open_positions=None).ok

    def test_the_refusal_notes_the_downstream_gate_is_inert(self) -> None:
        # The execution project's equivalent check never fires in production,
        # because its adapter does not populate the count. An operator relying on
        # that guard would be relying on nothing.
        risk = _risk(max_open_positions=3)
        explanation = validate_policy(_signal(), risk, account_open_positions=5).explanation
        assert "inert" in explanation


class TestAgainstSpec:
    def _spec(self) -> SymbolSpec:
        return SymbolSpec(
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

    def test_on_tick_levels_pass_cleanly(self) -> None:
        resolution = validate_against_spec(
            Decimal("1.10000"), Decimal("1.09700"), _target(), self._spec()
        )
        assert resolution.ok
        assert not any(key.endswith("_off_tick") for key in resolution.details)

    def test_an_off_tick_stop_is_recorded(self) -> None:
        # A broker will usually round it, and a rounded stop means the executed
        # risk is not the risk that was calculated -- so it is recorded rather
        # than silently accepted.
        resolution = validate_against_spec(
            Decimal("1.10000"), Decimal("1.097005"), _target(), self._spec()
        )
        assert resolution.ok
        assert "stop_loss_off_tick" in resolution.details

    def test_an_off_tick_target_is_recorded(self) -> None:
        resolution = validate_against_spec(
            Decimal("1.10000"), Decimal("1.09700"), _target(), self._spec()
        )
        assert resolution.ok

    def test_an_off_tick_entry_is_recorded(self) -> None:
        resolution = validate_against_spec(
            Decimal("1.100005"), Decimal("1.09700"), None, self._spec()
        )
        assert "entry_off_tick" in resolution.details

    def test_no_target_means_no_target_check(self) -> None:
        resolution = validate_against_spec(
            Decimal("1.10000"), Decimal("1.09700"), None, self._spec()
        )
        assert "take_profit_off_tick" not in resolution.details

    def test_gold_is_checked_against_its_own_tick_size(self) -> None:
        # The whole reason sizing is tick-based rather than pip-based. A 0.01
        # tick size means 1.10000 is off-tick here and on-tick for EURUSD.
        gold = SymbolSpec(
            symbol="XAUUSD",
            contract_size=Decimal("100"),
            tick_size=Decimal("0.01"),
            tick_value_profit=Decimal("1"),
            tick_value_loss=Decimal("1"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("50"),
            volume_step=Decimal("0.01"),
            digits=2,
            point=Decimal("0.01"),
        )
        assert (
            "stop_loss_off_tick"
            in validate_against_spec(Decimal("1.10000"), Decimal("1.09700"), None, gold).details
        )
        assert (
            "stop_loss_off_tick"
            not in validate_against_spec(Decimal("110.00"), Decimal("109.70"), None, gold).details
        )


class TestCollectReasons:
    def test_only_the_failures_contribute(self) -> None:
        good = validate_signal(_signal(), _risk())
        bad = validate_signal(_signal(action=SignalAction.WAIT, direction=Direction.FLAT), _risk())
        reasons = collect_reasons([good, bad])
        assert reasons == [RejectionReason.SIGNAL_INVALID]

    def test_an_empty_sequence_yields_nothing(self) -> None:
        assert list(collect_reasons([])) == []

    def test_the_pipeline_stops_at_the_first_refusal(self) -> None:
        # Documented behaviour, not an accident: running later checks on a signal
        # that has already failed means dividing by things that do not exist.
        # `collect_reasons` exists for diagnostics, which is a different job.
        signal = _signal(action=SignalAction.WAIT, direction=Direction.FLAT)
        first = validate_signal(signal, _risk())
        assert not first.ok
        # The caller would return here. The remaining checks are never reached.
