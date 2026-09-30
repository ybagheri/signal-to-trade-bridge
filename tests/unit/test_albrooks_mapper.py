"""Mapping an upstream analysis result into an internal signal.

The tests here are built against hand-written stubs shaped like the engine's real
output, taken verbatim from the architecture audit. Each one pins a specific
property of that shape, so a change upstream fails here rather than silently
producing a signal with a missing field.

The two that matter most:

* the degenerate path returns a **three-key** decision, not thirteen
* the engine reports an undefined price as ``0.0``, not ``None``
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from signal_to_trade_bridge.adapters.albrooks.mapper import (
    GEOMETRY_ISSUES,
    MAPPED_FIELDS,
    STRUCTURAL_STOP_BASES,
    map_result_to_signal,
    source_action,
    source_direction,
)
from signal_to_trade_bridge.domain.enums import Direction, SignalAction
from tests.stubs import (
    BAR_TIME,
    StubResult,
    stub_abstention,
    stub_buy,
    stub_degenerate_no_atr,
    stub_degenerate_no_bars,
    stub_sell,
)


class TestActionMapping:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("BUY", SignalAction.BUY),
            ("SELL", SignalAction.SELL),
            ("WAIT", SignalAction.WAIT),
            ("NO_TRADE", SignalAction.NO_TRADE),
        ],
    )
    def test_every_upstream_action_maps(self, raw: str, expected: SignalAction) -> None:
        assert source_action(raw) is expected

    def test_mapping_is_case_insensitive(self) -> None:
        assert source_action("buy") is SignalAction.BUY

    def test_an_unknown_action_returns_none_rather_than_a_default(self) -> None:
        # Defaulting to NO_TRADE would make a fifth upstream action look like a
        # considered decision. The caller has to see that something arrived it
        # does not understand.
        assert source_action("SCALP") is None
        assert source_action(None) is None
        assert source_action(42) is None

    def test_an_unknown_action_produces_a_refusal(self) -> None:
        result = StubResult(symbol="EURUSD", timeframe="H1", decision={"action": "SCALP"})
        outcome = map_result_to_signal(result)
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_DIRECTION_UNKNOWN"
        assert "SCALP" in outcome.explanation


class TestDirectionMapping:
    def test_buy_maps_to_long(self) -> None:
        assert source_direction(1, SignalAction.BUY) is Direction.LONG

    def test_sell_maps_to_short(self) -> None:
        assert source_direction(-1, SignalAction.SELL) is Direction.SHORT

    def test_the_action_wins_when_the_two_disagree(self) -> None:
        # An upstream inconsistency between `action` and `direction` is a bug
        # there. When they disagree the action wins, because it is the coarser and
        # more conservative of the two statements -- and a long taken from a SELL
        # because of a stale direction field is the worst available outcome.
        assert source_direction(-1, SignalAction.BUY) is Direction.LONG
        assert source_direction(1, SignalAction.SELL) is Direction.SHORT

    def test_an_abstention_with_a_direction_is_flat(self) -> None:
        assert source_direction(1, SignalAction.WAIT) is Direction.FLAT

    def test_a_non_numeric_direction_is_flat_not_an_error(self) -> None:
        assert source_direction("up", SignalAction.NO_TRADE) is Direction.FLAT

    def test_a_magnitude_other_than_one_is_normalised(self) -> None:
        # The engine's `TradePlan` normalises direction to its sign, so a detector
        # reporting 100 and one reporting 1 must produce the same direction. The
        # mapper never reads the numeric field, so the normalisation happens in
        # `Direction.from_sign` upstream of it -- and the property that matters
        # here is that the action alone determines the direction.
        assert Direction.from_sign(100) is Direction.from_sign(1)
        assert source_direction(100, SignalAction.BUY) is Direction.LONG
        assert source_direction(-100, SignalAction.SELL) is Direction.SHORT

    def test_a_stray_direction_on_an_abstention_is_ignored(self) -> None:
        # Not read at all. Both abstentions mean "not asking for a trade", and a
        # non-zero direction attached to one would be a direction with nothing to
        # trade -- so an upstream inconsistency here must not produce a position.
        assert source_direction(1, SignalAction.WAIT) is Direction.FLAT
        assert source_direction(-1, SignalAction.NO_TRADE) is Direction.FLAT


class TestNormalBuyMapping:
    def test_maps_a_ranked_buy(self) -> None:
        outcome = map_result_to_signal(stub_buy())
        assert outcome.signal is not None
        signal = outcome.signal
        assert signal.action is SignalAction.BUY
        assert signal.direction is Direction.LONG
        assert signal.is_tradable
        assert signal.entry == Decimal("1.10000")
        assert signal.stop_loss == Decimal("1.09700")
        assert signal.take_profit == Decimal("1.10300")

    def test_carries_the_stop_provenance(self) -> None:
        # The stop policy in Phase 4 depends on this. An adapter that dropped it
        # would leave a volatility fallback indistinguishable from a structural
        # level, and the engine is explicit that they are not the same thing.
        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert signal.stop_basis == "PULLBACK_EXTREME"
        assert signal.take_profit_basis == "SWING"

    def test_flags_a_volatility_fallback_stop_as_non_structural(self) -> None:
        signal = map_result_to_signal(stub_buy(stop_basis="ATR_FALLBACK")).signal
        assert signal is not None
        assert signal.source_metadata["has_structural_stop"] is False

    def test_flags_a_measured_move_target_as_structural(self) -> None:
        signal = map_result_to_signal(stub_buy(target_basis="MEASURED_MOVE")).signal
        assert signal is not None
        assert signal.source_metadata["has_structural_target"] is True

    def test_maps_a_sell_with_mirrored_prices(self) -> None:
        signal = map_result_to_signal(stub_sell()).signal
        assert signal is not None
        assert signal.action is SignalAction.SELL
        assert signal.direction is Direction.SHORT
        # SELL: entry 1.10000, stop 1.10300 above, target 1.09700 below.
        assert signal.stop_loss > signal.entry
        assert signal.take_profit is not None
        assert signal.take_profit < signal.entry

    def test_carries_the_setup_identifier(self) -> None:
        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert signal.setup_id == "pullback_h#0"

    def test_records_the_bar_the_analysis_actually_used(self) -> None:
        # Not `bar_features[-1]`. The engine deliberately computes features only
        # for bars 0..last_closed, so the last row of a full series is a *later*
        # bar than the one that was analysed. The stub reproduces that with a
        # trailing row at index 300, and this test is what catches an adapter that
        # takes the last element.
        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert signal.bar_index == 299
        assert signal.bar_time == BAR_TIME

    def test_carries_the_upstream_reason_verbatim(self) -> None:
        # Renamed upstream reasons make cross-referencing its own documentation
        # harder for no benefit.
        outcome = map_result_to_signal(stub_buy())
        assert outcome.source_reason == "RANKED_CANDIDATE"
        assert outcome.signal is not None
        assert outcome.signal.source_metadata["source_reason"] == "RANKED_CANDIDATE"

    def test_the_evidence_score_is_carried_and_labelled_not_a_probability(self) -> None:
        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert signal.evidence_score == pytest.approx(0.72)
        # The engine states in three places that this is not a win probability,
        # and the flag travels with the number so a log reader is told too.
        assert signal.source_metadata["evidence_is_probability"] is False

    def test_an_out_of_range_evidence_score_is_dropped_rather_than_clamped(self) -> None:
        # The filter then skips instead of applying a nonsense bound.
        result = stub_buy()
        result.decision["evidence"] = {"value": 1.5}
        assert map_result_to_signal(result).signal is not None
        assert map_result_to_signal(result).signal.evidence_score is None

    def test_evidence_from_percentage_points_is_converted(self) -> None:
        result = stub_buy()
        result.decision["evidence"] = {"ppts": 65.0}
        signal = map_result_to_signal(result).signal
        assert signal is not None
        assert signal.evidence_score == pytest.approx(0.65)


class TestAbstentionMapping:
    @pytest.mark.parametrize("action", ["WAIT", "NO_TRADE"])
    def test_an_abstention_maps_to_a_non_tradable_signal(self, action: str) -> None:
        # A valid signal that asks for no trade. The risk service refuses it, and
        # it reaches that point carrying the upstream reason, which is what makes
        # a quiet day explicable rather than mysterious.
        signal = map_result_to_signal(stub_abstention(action)).signal
        assert signal is not None
        assert signal.action is SignalAction(action)
        assert signal.direction is Direction.FLAT
        assert not signal.is_tradable

    def test_wait_and_no_trade_stay_distinct(self) -> None:
        # One is a condition that was evaluated and not met; the other is a
        # refusal to answer. A log that flattened them could not say which
        # happened, and they call for different follow-up.
        wait = map_result_to_signal(stub_abstention("WAIT")).signal
        no_trade = map_result_to_signal(stub_abstention("NO_TRADE")).signal
        assert wait is not None and no_trade is not None
        assert wait.action is not no_trade.action

    def test_a_missing_plan_does_not_raise(self) -> None:
        # `plan` is None on every abstention in the real engine.
        assert map_result_to_signal(stub_abstention()).signal is not None

    def test_the_upstream_reason_is_preserved_on_an_abstention(self) -> None:
        signal = map_result_to_signal(stub_abstention("WAIT", "EVIDENCE_CONFLICT")).signal
        assert signal is not None
        assert signal.source_metadata["source_reason"] == "EVIDENCE_CONFLICT"


class TestDegeneratePath:
    def test_the_three_key_shape_is_handled(self) -> None:
        # The single most important case in this file. `Analyzer._empty` returns
        # exactly three keys, and an adapter reading `decision["plan"]` directly
        # would raise on precisely the input that most needs a clean no-trade.
        outcome = map_result_to_signal(stub_degenerate_no_bars())
        assert outcome.signal is None
        assert outcome.degenerate is True
        assert outcome.source_reason == "NO_BARS"

    def test_atr_unavailable_is_also_degenerate(self) -> None:
        outcome = map_result_to_signal(stub_degenerate_no_atr())
        assert outcome.signal is None
        assert outcome.degenerate is True
        assert outcome.source_reason == "ATR_UNAVAILABLE"

    def test_a_degenerate_result_is_distinguishable_from_a_quiet_market(self) -> None:
        # The engine is explicit that NO_BARS is a fact about the *input* and
        # ALL_CANDIDATES_VETOED is a fact about the *market*. Reporting the first
        # as the second would turn a broken data feed into a quiet trading day.
        degenerate = map_result_to_signal(stub_degenerate_no_bars())
        abstention = map_result_to_signal(stub_abstention("WAIT"))
        assert degenerate.degenerate is True
        assert abstention.degenerate is False

    def test_a_result_with_no_decision_is_refused(self) -> None:
        outcome = map_result_to_signal(StubResult(symbol="EURUSD", timeframe="H1"))
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_MALFORMED"


class TestInvalidPrices:
    def test_a_zero_stop_means_no_stop_not_a_stop_at_zero(self) -> None:
        # TradePlan uses 0.0 for an absent level and its own geometry check skips
        # zeros rather than comparing them. A stop at zero would be wrong by the
        # entire size of the instrument, and would produce an enormous position
        # rather than an error.
        signal = map_result_to_signal(stub_buy(entry=1.10000, stop=0.0, target=1.10300)).signal
        assert signal is not None
        assert signal.stop_loss is None

    def test_a_zero_target_means_no_target(self) -> None:
        signal = map_result_to_signal(stub_buy(entry=1.10000, stop=1.09700, target=0.0)).signal
        assert signal is not None
        assert signal.take_profit is None

    def test_a_zero_entry_is_refused(self) -> None:
        # Unlike a missing stop, there is no signal at all without an entry: an
        # entry of 0.0 means the engine's plan was empty, and a signal that maps
        # it to a real trade would be trading nothing.
        outcome = map_result_to_signal(stub_buy(entry=0.0, stop=1.09700, target=1.10300))
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_ENTRY_INVALID"
        assert "no usable entry" in outcome.explanation

    def test_a_missing_entry_is_refused(self) -> None:
        result = stub_buy()
        del result.decision["plan"]["entry"]
        assert map_result_to_signal(result).reason == "SIGNAL_ENTRY_INVALID"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_price_is_treated_as_absent(self, bad: float) -> None:
        # A float pipeline can produce NaN, and `Decimal("NaN")` compares false
        # against everything *without raising*. A NaN price reaching a position
        # sizer produces a NaN volume, and the risk calculation reports itself
        # complete before the broker rejects it.
        signal = map_result_to_signal(stub_buy(stop=bad)).signal
        assert signal is not None
        assert signal.stop_loss is None

    def test_a_non_numeric_price_is_treated_as_absent(self) -> None:
        # The engine is a typed library, so this is defensive rather than
        # expected. It is tested because a price that failed to convert has to
        # become "absent" and be refused downstream, not become a zero that later
        # arithmetic treats as a real level.
        result = stub_buy()
        result.decision["plan"]["stop"] = "not a number"
        signal = map_result_to_signal(result).signal
        assert signal is not None
        assert signal.stop_loss is None

    def test_a_missing_symbol_is_refused(self) -> None:
        outcome = map_result_to_signal(stub_buy(symbol="   "))
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_SYMBOL_INVALID"

    def test_a_negative_entry_is_refused(self) -> None:
        # Treated as absent, because a negative price is not a tradeable level and
        # converting it to a positive distance would produce a plausible-looking
        # size for an impossible instrument.
        outcome = map_result_to_signal(stub_buy(entry=-1.1, stop=-1.2, target=-1.0))
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_ENTRY_INVALID"


class TestDecimalConversion:
    def test_prices_arrive_as_exact_decimals(self) -> None:
        # The upstream uses float throughout. A price that float cannot represent
        # exactly must still convert without the rounding that `Decimal(float)`
        # would introduce -- hence the conversion via `str`, not via `float`.
        signal = map_result_to_signal(stub_buy(entry=1.1, stop=1.097, target=1.103)).signal
        assert signal is not None
        assert signal.entry == Decimal("1.1")
        assert str(signal.entry) == "1.1"

    def test_a_long_price_keeps_every_digit_the_engine_produced(self) -> None:
        signal = map_result_to_signal(
            stub_buy(entry=12345.678901, stop=12345.678901, target=12345.678901)
        ).signal
        assert signal is not None
        assert signal.entry == Decimal("12345.678901")


class TestConstants:
    def test_every_mapped_field_is_a_decision_key_the_engine_produces(self) -> None:
        # Pinned so an upstream rename that silently stopped being read shows up
        # here rather than as a signal with a missing field.
        assert set(MAPPED_FIELDS) <= {
            "action",
            "reason",
            "subject",
            "direction",
            "plan",
            "evidence",
            "explanation",
            "vetoes",
            "considered",
            "ranking_basis",
            "sides",
            "is_actionable",
            "is_probability",
        }

    def test_structural_stop_bases_exclude_the_volatility_fallback(self) -> None:
        # Mirrors the engine's own `has_structural_stop`, which is
        # `stop_basis not in ("ATR_FALLBACK", "NONE")`.
        assert "ATR_FALLBACK" not in STRUCTURAL_STOP_BASES
        assert "NONE" not in STRUCTURAL_STOP_BASES
        assert "PULLBACK_EXTREME" in STRUCTURAL_STOP_BASES
        assert "SWING" in STRUCTURAL_STOP_BASES

    def test_geometry_issues_match_the_engine_blocking_set(self) -> None:
        assert len(GEOMETRY_ISSUES) == 8
        assert "STOP_NOT_PROTECTIVE" in GEOMETRY_ISSUES
        assert "STOP_UNDEFINED" in GEOMETRY_ISSUES
        assert "TARGET_NOT_AHEAD" in GEOMETRY_ISSUES
