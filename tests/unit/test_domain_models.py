"""Domain value objects: construction, validation and invariants.

The models are frozen and validate themselves, so most of what matters here is
that they *cannot* be built into an invalid state. Each test therefore asserts
both that the invalid construction fails and, where the failure could plausibly be
a mistake rather than a rule, that the message says which rule was broken.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain.enums import (
    DecisionAction,
    Direction,
    RejectionReason,
    SignalAction,
)
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionRequest,
    ExecutionResult,
    PositionSize,
    RiskBudget,
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
    TradeDecision,
    TradeIntent,
    canonical_ratio,
)


class TestDirection:
    def test_sign_follows_the_upstream_engine_convention(self) -> None:
        # albrooks uses +1 / -1 / 0 throughout, so the mapping is a cast rather
        # than a negation. A mismatch here would silently invert every trade.
        assert Direction.LONG.sign == 1
        assert Direction.SHORT.sign == -1
        assert Direction.FLAT.sign == 0

    def test_from_sign_normalises_magnitude(self) -> None:
        # TradePlan stores the normalised sign, so a detector reporting 100 and
        # one reporting 1 must produce the same direction.
        assert Direction.from_sign(100) is Direction.LONG
        assert Direction.from_sign(-100) is Direction.SHORT
        assert Direction.from_sign(0) is Direction.FLAT

    def test_from_sign_treats_zero_as_flat_not_an_error(self) -> None:
        # "The setup named no direction" is a real and common reading upstream,
        # not a malformed input.
        assert Direction.from_sign(0) is Direction.FLAT

    def test_inverted_is_its_own_inverse(self) -> None:
        for direction in (Direction.LONG, Direction.SHORT, Direction.FLAT):
            assert direction.inverted().inverted() is direction

    def test_only_directional_members_are_tradable(self) -> None:
        assert Direction.LONG.is_tradable
        assert Direction.SHORT.is_tradable
        assert not Direction.FLAT.is_tradable


class TestSignalAction:
    def test_both_abstentions_are_flat(self) -> None:
        # WAIT and NO_TRADE are different upstream claims, but neither asks for a
        # trade, and treating either as directional is the first step towards
        # trading something nobody asked for.
        assert SignalAction.WAIT.direction is Direction.FLAT
        assert SignalAction.NO_TRADE.direction is Direction.FLAT

    def test_only_buy_and_sell_are_tradable(self) -> None:
        assert SignalAction.BUY.is_tradable
        assert SignalAction.SELL.is_tradable
        assert not SignalAction.WAIT.is_tradable
        assert not SignalAction.NO_TRADE.is_tradable


class TestAccountBalance:
    def test_rejects_a_non_positive_balance(self) -> None:
        with pytest.raises(ValueError, match="balance must be positive"):
            AccountBalance(balance=Decimal("0"), currency="USD")

    def test_requires_a_currency(self) -> None:
        with pytest.raises(ValueError, match="currency is required"):
            AccountBalance(balance=Decimal("1000"), currency="  ")

    def test_rejects_a_negative_position_count(self) -> None:
        with pytest.raises(ValueError, match="open_positions cannot be negative"):
            AccountBalance(balance=Decimal("1000"), currency="USD", open_positions=-1)

    def test_sizing_uses_balance_not_equity(self) -> None:
        # The brief specifies a percentage of balance, and equity moves with open
        # positions, so using it would make a new trade's risk depend on trades
        # already running.
        account = AccountBalance(balance=Decimal("10000"), currency="USD", equity=Decimal("5000"))
        assert account.effective_balance == Decimal("10000")


class TestSymbolSpec:
    def test_rejects_a_negative_tick_size(self) -> None:
        with pytest.raises(ValueError, match="tick_size must be positive"):
            SymbolSpec(
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
            )

    def test_rejects_an_inverted_volume_range(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed volume_max"):
            SymbolSpec(
                symbol="EURUSD",
                contract_size=Decimal("100000"),
                tick_size=Decimal("0.00001"),
                tick_value_profit=Decimal("1"),
                tick_value_loss=Decimal("1"),
                volume_min=Decimal("10"),
                volume_max=Decimal("1"),
                volume_step=Decimal("0.01"),
                digits=5,
                point=Decimal("0.00001"),
            )

    def test_conservative_tick_value_is_the_worse_of_the_two(self, forex_spec: SymbolSpec) -> None:
        # `volume = risk_amount / (ticks * tick_value)`, so the *smaller* tick
        # value gives the *larger* position. A risk calculation must divide by the
        # larger value, or it sizes for the cheapest possible tick.
        #
        # Corrected in Phase 4: this asserted `2.0` (the minimum) on the reasoning
        # that "a size that is safe on paper has to be safe on the losing side".
        # The conclusion was right and the implementation was its mirror image --
        # dividing by $2 when a stop could cost $3 a tick under-sizes the position
        # by a third, which is the opposite of safe.
        asymmetric = SymbolSpec(
            symbol="SOME_CFD",
            contract_size=forex_spec.contract_size,
            tick_size=forex_spec.tick_size,
            tick_value_profit=Decimal("2.0"),
            tick_value_loss=Decimal("3.0"),
            volume_min=forex_spec.volume_min,
            volume_max=forex_spec.volume_max,
            volume_step=forex_spec.volume_step,
            digits=5,
            point=forex_spec.point,
        )
        assert asymmetric.conservative_tick_value == Decimal("3.0")

    def test_conservative_tick_value_never_divides_by_the_cheaper_tick(
        self, forex_spec: SymbolSpec
    ) -> None:
        # The same property from the other direction: whichever way the two values
        # are ordered, the one used is the larger, because a larger divisor is a
        # smaller position and an under-sized position is the safe failure.
        asymmetric = SymbolSpec(
            symbol="SOME_CFD",
            contract_size=forex_spec.contract_size,
            tick_size=forex_spec.tick_size,
            tick_value_profit=Decimal("3.0"),
            tick_value_loss=Decimal("2.0"),
            volume_min=forex_spec.volume_min,
            volume_max=forex_spec.volume_max,
            volume_step=forex_spec.volume_step,
            digits=5,
            point=forex_spec.point,
        )
        assert asymmetric.conservative_tick_value == Decimal("3.0")

    def test_a_zero_tick_value_does_not_zero_the_conservative_one(
        self, forex_spec: SymbolSpec
    ) -> None:
        # Some symbols report one of the two as zero rather than omitting it.
        # Taking a plain `min` of those would give zero and refuse every trade on
        # a perfectly tradable instrument; `max` handles it without a special case.
        one_sided = SymbolSpec(
            symbol="SOME_CFD",
            contract_size=forex_spec.contract_size,
            tick_size=forex_spec.tick_size,
            tick_value_profit=Decimal("1.0"),
            tick_value_loss=Decimal("0"),
            volume_min=forex_spec.volume_min,
            volume_max=forex_spec.volume_max,
            volume_step=forex_spec.volume_step,
            digits=5,
            point=forex_spec.point,
        )
        assert one_sided.conservative_tick_value == Decimal("1.0")

    def test_risk_per_unit_is_tick_value_based(self, forex_spec: SymbolSpec) -> None:
        # 0.00300 / 0.00001 = 300 ticks, at $1 per tick per lot = $300.
        assert forex_spec.risk_per_unit(Decimal("0.00300")) == Decimal("300")

    def test_risk_per_unit_is_zero_for_a_zero_distance(self, forex_spec: SymbolSpec) -> None:
        assert forex_spec.risk_per_unit(Decimal("0")) == Decimal(0)

    def test_risk_per_unit_works_for_gold(self, gold_spec: SymbolSpec) -> None:
        # 3.00 / 0.01 = 300 ticks, at $1 per tick per lot = $300. The same answer
        # as the forex case, reached by completely different arithmetic -- which
        # is the whole reason sizing is tick-value based rather than pip based.
        assert gold_spec.risk_per_unit(Decimal("3.00")) == Decimal("300")

    def test_round_volume_rounds_down_by_default(self, forex_spec: SymbolSpec) -> None:
        # Rounding up could push the position's risk above the budget. Rounding
        # down leaves it fractionally under, which is the safe direction.
        assert forex_spec.round_volume(Decimal("0.377")) == Decimal("0.37")

    def test_round_volume_can_round_up_when_asked(self, forex_spec: SymbolSpec) -> None:
        assert forex_spec.round_volume(Decimal("0.377"), round_down=False) == Decimal("0.38")

    def test_clamp_volume_never_raises_to_the_minimum(self, forex_spec: SymbolSpec) -> None:
        # A volume below the broker minimum is a refusal, not something to round
        # up. This method returns zero rather than volume_min, and the caller is
        # required to refuse. The test pins the behaviour so that a later
        # "helpful" change to floor it up fails here.
        assert forex_spec.clamp_volume(Decimal("0.001")) == Decimal(0)

    def test_clamp_volume_caps_at_the_maximum(self, forex_spec: SymbolSpec) -> None:
        assert forex_spec.clamp_volume(Decimal("500")) == forex_spec.volume_max


class TestSignal:
    def test_rejects_a_non_positive_entry(self, buy_signal: Signal) -> None:
        with pytest.raises(ValueError, match="entry must be positive"):
            Signal(
                signal_id="x",
                symbol="EURUSD",
                timeframe="H1",
                action=SignalAction.BUY,
                direction=Direction.LONG,
                entry=Decimal("0"),
            )

    def test_rejects_an_action_without_a_matching_direction(self) -> None:
        with pytest.raises(ValueError, match="requires a tradable direction"):
            Signal(
                signal_id="x",
                symbol="EURUSD",
                timeframe="H1",
                action=SignalAction.BUY,
                direction=Direction.FLAT,
                entry=Decimal("1.1"),
            )

    def test_rejects_an_out_of_range_evidence_score(self) -> None:
        with pytest.raises(ValueError, match="evidence_score must be between 0 and 1"):
            Signal(
                signal_id="x",
                symbol="EURUSD",
                timeframe="H1",
                action=SignalAction.BUY,
                direction=Direction.LONG,
                entry=Decimal("1.1"),
                evidence_score=1.5,
            )

    def test_accepts_an_abstention_without_a_stop(self) -> None:
        # WAIT and NO_TRADE are valid signals that simply ask for no trade. They
        # must be constructible, because a source that could not represent "no
        # trade" would have to raise on the commonest outcome there is.
        signal = Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.WAIT,
            direction=Direction.FLAT,
            entry=Decimal("1.1"),
        )
        assert not signal.is_tradable

    def test_is_frozen(self, buy_signal: Signal) -> None:
        with pytest.raises(AttributeError):
            buy_signal.symbol = "GBPUSD"  # type: ignore[misc]

    def test_serialises_without_losing_the_identity(self, buy_signal: Signal) -> None:
        payload = buy_signal.to_dict()
        assert payload["signal_id"] == buy_signal.signal_id
        assert payload["symbol"] == "EURUSD"
        assert payload["direction"] == "LONG"
        # Prices as strings, so a Decimal never becomes a lossy float on the way
        # to a JSON log line.
        assert payload["entry"] == "1.10000"


class TestRiskParameters:
    def test_defaults_match_the_brief(self) -> None:
        risk = RiskParameters()
        assert risk.risk_percent == Decimal("0.5")
        assert risk.reward_risk_ratio == Decimal("1.0")

    def test_risk_amount_is_half_a_percent_of_the_balance(self) -> None:
        risk = RiskParameters(risk_percent=Decimal("0.5"))
        assert risk.risk_amount(Decimal("10000")) == Decimal("50")

    @pytest.mark.parametrize(
        ("percent", "expected"),
        [
            (Decimal("0.25"), Decimal("25")),
            (Decimal("0.5"), Decimal("50")),
            (Decimal("0.75"), Decimal("75")),
            (Decimal("1.0"), Decimal("100")),
        ],
    )
    def test_every_configurable_percentage_works_without_a_code_change(
        self, percent: Decimal, expected: Decimal
    ) -> None:
        # The brief requires 0.25 / 0.50 / 0.75 / 1.00 to be configuration
        # changes rather than code changes. This is the test that says so.
        risk = RiskParameters(risk_percent=percent)
        assert risk.risk_amount(Decimal("10000")) == expected

    def test_rejects_a_non_positive_percentage(self) -> None:
        with pytest.raises(ValueError, match="risk_percent must be positive"):
            RiskParameters(risk_percent=Decimal("0"))

    def test_rejects_a_percentage_above_one_hundred(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed 100"):
            RiskParameters(risk_percent=Decimal("150"))

    def test_rejects_a_zero_ratio(self) -> None:
        with pytest.raises(ValueError, match="reward_risk_ratio must be positive"):
            RiskParameters(reward_risk_ratio=Decimal("0"))

    def test_rejects_a_configuration_that_refuses_everything(self) -> None:
        # Better to fail at construction than to run a bridge that can never
        # trade and looks, from the outside, like one that is working.
        with pytest.raises(ValueError, match="would refuse every signal"):
            RiskParameters(allow_buy=False, allow_sell=False)

    def test_an_empty_allowlist_means_unrestricted_by_the_bridge(self) -> None:
        risk = RiskParameters()
        assert risk.symbol_allowed("ANYTHING")

    def test_a_populated_allowlist_is_case_insensitive(self) -> None:
        risk = RiskParameters(allowed_symbols=frozenset({"EURUSD"}))
        assert risk.symbol_allowed("eurusd")
        assert not risk.symbol_allowed("GBPUSD")

    def test_reward_amount_scales_with_the_ratio(self) -> None:
        risk = RiskParameters(risk_percent=Decimal("0.5"), reward_risk_ratio=Decimal("2"))
        assert risk.reward_amount(Decimal("10000")) == Decimal("100")


class TestRiskBudget:
    def _budget(self, **overrides: object) -> RiskBudget:
        defaults: dict[str, object] = {
            "amount": Decimal("50"),
            "balance": Decimal("10000"),
            "risk_percent": Decimal("0.5"),
            "currency": "USD",
            "reward_amount": Decimal("50"),
            "reward_risk_ratio": Decimal("1.0"),
        }
        defaults.update(overrides)
        return RiskBudget(**defaults)  # type: ignore[arg-type]

    def test_keeps_every_input_the_amount_was_derived_from(self) -> None:
        # A budget on its own is an assertion rather than a record. These are the
        # numbers a reviewer re-derives by hand, so they travel with it.
        budget = self._budget()
        assert budget.balance == Decimal("10000")
        assert budget.risk_percent == Decimal("0.5")
        assert budget.reward_risk_ratio == Decimal("1.0")

    def test_the_fraction_of_the_balance_is_the_configured_fraction(self) -> None:
        assert self._budget().fraction_of_balance == Decimal("0.005")

    def test_serialises_every_field(self) -> None:
        assert self._budget().to_dict() == {
            "amount": "50",
            "balance": "10000",
            "risk_percent": "0.5",
            "currency": "USD",
            "reward_amount": "50",
            "reward_risk_ratio": "1.0",
        }

    @pytest.mark.parametrize(
        "field", ["amount", "balance", "risk_percent", "reward_amount", "reward_risk_ratio"]
    )
    @pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1")])
    def test_refuses_a_non_positive_money_field(self, field: str, bad: Decimal) -> None:
        # A budget of zero is not a small budget, it is an undefined one, and a
        # later division by it produces a nonsense volume rather than a refusal.
        with pytest.raises(ValueError):
            self._budget(**{field: bad})

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_refuses_a_blank_currency(self, blank: str) -> None:
        # The currency is what makes the tick value divisible by the budget, so an
        # absent one has to be caught where the budget is built.
        with pytest.raises(ValueError, match="currency is required"):
            self._budget(currency=blank)

    def test_the_planned_gain_must_match_the_budget_and_the_ratio(self) -> None:
        # The planned gain is *derived*. Nothing in the sizer reads it, so an
        # inconsistent pair would have passed every other test while the decision
        # log advertised a gain the bridge never aimed for.
        with pytest.raises(ValueError, match="does not match"):
            self._budget(amount=Decimal("50"), reward_amount=Decimal("999"))

    def test_a_consistent_reward_at_a_ratio_other_than_one_is_accepted(self) -> None:
        assert self._budget(
            amount=Decimal("50"), reward_risk_ratio=Decimal("2.5"), reward_amount=Decimal("125")
        ).reward_amount == Decimal("125")

    def test_the_reward_is_compared_as_a_number_not_as_text(self) -> None:
        # `50` and `50.00` are the same money. A text comparison would reject a
        # budget built by arithmetic that happens to carry a different exponent --
        # which is every budget, since `amount * ratio` preserves whatever the
        # operands had.
        assert self._budget(
            amount=Decimal("50"), reward_amount=Decimal("50.00"), reward_risk_ratio=Decimal("1")
        ).reward_amount == Decimal("50.00")

    def test_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            self._budget().amount = Decimal("999")  # type: ignore[misc]


class TestPositionSize:
    def _size(self, **overrides: object) -> PositionSize:
        defaults: dict[str, object] = {
            "volume": Decimal("0.16"),
            "raw_volume": Decimal("0.1666"),
            "risk_amount": Decimal("50"),
            "stop_distance": Decimal("0.00300"),
            "risk_per_unit": Decimal("300"),
            "ticks": Decimal("300"),
            "tick_size": Decimal("0.00001"),
            "tick_value": Decimal("1"),
        }
        defaults.update(overrides)
        return PositionSize(**defaults)  # type: ignore[arg-type]

    def test_refuses_to_ever_report_a_floor_up(self) -> None:
        # The most dangerous line in any position sizer. It is enforced in the
        # constructor rather than in a policy function so that no code path,
        # present or future, can produce a size that was raised to the broker
        # minimum.
        with pytest.raises(ValueError, match="never a floor-up"):
            self._size(clamped_to_minimum=True)

    def test_planned_loss_reflects_rounding_not_intention(self) -> None:
        # 0.16 lots at $300 per lot is $48, not the intended $50. Reporting the
        # intended figure would be reporting an intention as a fact.
        size = self._size()
        assert size.planned_loss == Decimal("48")
        assert size.risk_amount == Decimal("50")
        assert size.is_within_budget

    def test_is_within_budget_detects_an_over_budget_size(self) -> None:
        # Should be unreachable given rounding is down and clamping is down, but a
        # sizer that can exceed its own budget is exactly the failure this project
        # exists to prevent, so it is checked rather than assumed.
        size = self._size(volume=Decimal("0.20"))
        assert size.planned_loss == Decimal("60")
        assert not size.is_within_budget


class TestTradeDecision:
    def _intent(self) -> TradeIntent:
        return TradeIntent(
            signal=Signal(
                signal_id="stb-test-0001",
                symbol="EURUSD",
                timeframe="H1",
                action=SignalAction.BUY,
                direction=Direction.LONG,
                entry=Decimal("1.10000"),
            ),
            symbol="EURUSD",
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
            stop_loss=StopLoss(price=Decimal("1.09700"), distance=Decimal("0.00300")),
            take_profit=TakeProfit(price=Decimal("1.10300"), distance=Decimal("0.00300")),
            position_size=PositionSize(
                volume=Decimal("0.16"),
                raw_volume=Decimal("0.1666"),
                risk_amount=Decimal("50"),
                stop_distance=Decimal("0.00300"),
                risk_per_unit=Decimal("300"),
                ticks=Decimal("300"),
                tick_size=Decimal("0.00001"),
                tick_value=Decimal("1"),
            ),
            risk_parameters=RiskParameters(),
            account_balance=AccountBalance(balance=Decimal("10000"), currency="USD"),
            symbol_spec=SymbolSpec(
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
            ),
        )

    def test_a_no_trade_must_carry_a_reason(self) -> None:
        # The single most important invariant in the project. A refusal with no
        # reason is indistinguishable from a bug, which is the specific failure
        # mode the whole design exists to prevent.
        with pytest.raises(ValueError, match="NO_TRADE decision must carry a reason"):
            TradeDecision(signal_id="x", action=DecisionAction.NO_TRADE)

    def test_a_no_trade_with_a_reason_is_valid(self) -> None:
        decision = TradeDecision(
            signal_id="x",
            action=DecisionAction.NO_TRADE,
            reason=RejectionReason.NO_VALID_STOP.value,
            explanation="the signal carried no structural stop",
        )
        assert decision.is_no_trade

    def test_an_execute_decision_requires_an_intent(self) -> None:
        with pytest.raises(ValueError, match="requires an intent"):
            TradeDecision(signal_id="x", action=DecisionAction.EXECUTE)

    def test_a_dry_run_is_not_a_trade(self) -> None:
        # Collapsing these would let a dry run look like a fill in a log.
        decision = TradeDecision(
            signal_id="x", action=DecisionAction.DRY_RUN, intent=self._intent()
        )
        assert not decision.is_trade
        assert decision.is_dry_run

    def test_reward_to_risk_is_achieved_not_configured(self) -> None:
        # A signal-supplied target under the fallback policy does not produce
        # exactly 1:1, and reporting the configured ratio would report an
        # intention rather than a fact.
        import dataclasses

        intent = self._intent()
        assert intent.reward_to_risk == Decimal("1")
        # `replace`, not `{**intent.__dict__, ...}`: the model is frozen with
        # `slots=True`, so it has no `__dict__` to splat, and `dataclasses.replace`
        # re-runs validation on the new value, which the splat would not.
        off_ratio = dataclasses.replace(
            intent,
            take_profit=TakeProfit(price=Decimal("1.10600"), distance=Decimal("0.00600")),
        )
        assert off_ratio.reward_to_risk == Decimal("2")
        # The configured ratio is still 1.0 -- the achieved one differs, which is
        # exactly the distinction being pinned.
        assert off_ratio.risk_parameters.reward_risk_ratio == Decimal("1.0")

    def test_an_intent_without_a_take_profit_is_constructible(self) -> None:
        """`TakeProfitSource.NONE` must be representable.

        It was not, until Phase 5. `take_profit` was mandatory while the
        take-profit resolver correctly returns ``None`` for that policy, so the
        model could not describe a trade the bridge had deliberately enabled.
        Whoever wrote Phase 6 would have hit the mismatch, and the plausible way to
        resolve it -- refusing the trades an operator asked for -- would have been
        a silent policy change wearing the costume of a type error.
        """
        import dataclasses

        intent = dataclasses.replace(self._intent(), take_profit=None)
        assert intent.take_profit is None

    def test_an_intent_without_a_take_profit_has_no_ratio_and_says_so(self) -> None:
        import dataclasses

        intent = dataclasses.replace(self._intent(), take_profit=None)
        assert intent.reward_to_risk is None
        payload = intent.to_dict()
        assert payload["reward_to_risk"] is None
        assert payload["take_profit"] is None
        # `has_take_profit` exists because "the key is null" is a weaker statement
        # than "this trade has no target", and a consumer reading the record should
        # not have to infer one from the other.
        assert payload["has_take_profit"] is False

    def test_an_intent_with_a_take_profit_still_reports_true(self) -> None:
        assert self._intent().to_dict()["has_take_profit"] is True

    def test_serialising_an_intent_without_a_take_profit_does_not_divide(self) -> None:
        # `to_dict` used to evaluate `reward_to_risk` unconditionally, so the
        # `NONE` configuration would have raised rather than serialising. A record
        # that cannot be written down is not a record.
        import dataclasses

        payload = dataclasses.replace(self._intent(), take_profit=None).to_dict()
        assert payload["position_size"]["volume"] == "0.16"


class TestCanonicalRatio:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.0", "1"),
            ("1", "1"),
            ("2.50", "2.5"),
            ("0.20", "0.2"),
            ("100", "100"),
            ("0", "0"),
        ],
    )
    def test_one_number_has_one_representation(self, raw: str, expected: str) -> None:
        # The bug: the bridge wrote `"1"` for a ratio-derived target and `"1.0"`
        # for the identical ratio derived from a signal's target, so a log query
        # for one missed the other.
        assert str(canonical_ratio(Decimal(raw))) == expected

    def test_a_whole_number_does_not_become_exponent_notation(self) -> None:
        # `Decimal("100").normalize()` is `1E+2`, so normalisation alone would
        # have written a 100:1 configuration to the log as `1E+2`. That is why the
        # positive-exponent case is quantised back rather than left alone.
        assert "E" not in str(canonical_ratio(Decimal("100")))
        assert "E" not in str(canonical_ratio(Decimal("1000")))

    def test_a_long_fraction_keeps_its_precision(self) -> None:
        # Normalising a ratio must not round it. A 1/3 target stays 0.333... and
        # does not quietly become 0.33.
        assert canonical_ratio(Decimal(1) / Decimal(3)) == Decimal(1) / Decimal(3)

    def test_the_numeric_value_is_untouched(self) -> None:
        # Only the representation changes, so anything doing arithmetic on the
        # result is unaffected.
        assert canonical_ratio(Decimal("1.50")) == Decimal("1.5")
        assert canonical_ratio(Decimal("1.50") * 2) == Decimal("3")


class TestExecutionResult:
    def test_an_unrecognised_status_becomes_unknown(self) -> None:
        # Not an exception. An execution layer that invents a status is a real
        # possibility, and raising would turn a survivable surprise into a lost
        # trade record. Guessing that it meant "rejected" could cause a retry that
        # opens a second position.
        result = ExecutionResult(signal_id="x", status="SOMETHING_NEW")
        assert result.status == ExecutionResult.STATUS_UNKNOWN
        assert "unrecognised execution status" in result.message

    def test_unknown_is_never_retryable(self) -> None:
        # The single most dangerous state in the system: a final control may have
        # been used, so a retry may open a second position.
        result = ExecutionResult(signal_id="x", status=ExecutionResult.STATUS_UNKNOWN)
        assert result.is_unknown
        assert not result.is_retryable

    def test_requested_is_not_accepted_and_not_retryable(self) -> None:
        # A click is not a fill. The upstream project reports REQUESTED precisely
        # so that "used the control" and "position exists" stay distinguishable.
        result = ExecutionResult(signal_id="x", status=ExecutionResult.STATUS_REQUESTED)
        assert not result.is_accepted
        assert not result.is_retryable

    def test_only_a_clean_rejection_is_retryable(self) -> None:
        result = ExecutionResult(signal_id="x", status=ExecutionResult.STATUS_REJECTED)
        assert result.is_retryable

    def test_requires_a_signal_id(self) -> None:
        with pytest.raises(ValueError, match="signal_id is required"):
            ExecutionResult(signal_id="  ", status=ExecutionResult.STATUS_ACCEPTED)


class TestExecutionRequest:
    def test_rejects_a_flat_direction(self) -> None:
        with pytest.raises(ValueError, match="not tradable"):
            ExecutionRequest(
                signal_id="x",
                symbol="EURUSD",
                direction=Direction.FLAT,
                volume=Decimal("0.1"),
                entry=Decimal("1.1"),
                stop_loss=Decimal("1.09"),
                take_profit=Decimal("1.11"),
            )

    def test_rejects_a_non_positive_volume(self) -> None:
        with pytest.raises(ValueError, match="volume must be positive"):
            ExecutionRequest(
                signal_id="x",
                symbol="EURUSD",
                direction=Direction.LONG,
                volume=Decimal("0"),
                entry=Decimal("1.1"),
                stop_loss=Decimal("1.09"),
                take_profit=Decimal("1.11"),
            )


class TestRejectionReason:
    def test_reasons_are_stable_strings(self) -> None:
        # Stable and machine-comparable, because a refusal expressed only as log
        # prose cannot be counted, alerted on or tested for.
        assert RejectionReason.NO_VALID_STOP.value == "NO_VALID_STOP"
        assert RejectionReason.STOP_ON_WRONG_SIDE.value == "STOP_ON_WRONG_SIDE"
        assert RejectionReason.VOLUME_BELOW_BROKER_MINIMUM.value == "VOLUME_BELOW_BROKER_MINIMUM"

    def test_no_two_reasons_share_a_name(self) -> None:
        values = [member.value for member in RejectionReason]
        assert len(values) == len(set(values))
