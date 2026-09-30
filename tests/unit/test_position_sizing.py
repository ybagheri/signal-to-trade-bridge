"""Position sizing.

Organised around the three rules the module exists to keep:

1. a volume below the broker minimum is a **refusal**, never a floor-up;
2. rounding to the volume step is always **down**;
3. there is **no code path that invents a balance or a tick value**.

Rule 1 has the most tests, because it is the most dangerous line in any position
sizer: flooring up to reach a broker's minimum places a position whose risk
exceeds the budget by an unbounded amount. Rule 3 has an AST test, for the same
reason the stop resolver's rule has one -- the failure is invisible at runtime,
because an invented tick value produces a confident, plausible volume.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain import sizing as module
from signal_to_trade_bridge.domain.enums import RejectionReason, StopSource
from signal_to_trade_bridge.domain.models import RiskBudget, StopLoss, SymbolSpec
from signal_to_trade_bridge.domain.sizing import check_broker_constraints, resolve_position_size


def _spec(**overrides: object) -> SymbolSpec:
    """A 5-digit EURUSD: 0.00001 ticks at $1, so a 0.00300 stop risks $300 a lot."""
    defaults: dict[str, object] = {
        "symbol": "EURUSD",
        "contract_size": "100000",
        "tick_size": "0.00001",
        "tick_value_profit": "1.0",
        "tick_value_loss": "1.0",
        "volume_min": "0.01",
        "volume_max": "100.0",
        "volume_step": "0.01",
        "digits": 5,
        "point": "0.00001",
        "currency": "EUR",
        "currency_profit": "USD",
        "currency_margin": "EUR",
    }
    defaults.update(overrides)
    return SymbolSpec(**_decimals(defaults))  # type: ignore[arg-type]


def _gold(**overrides: object) -> SymbolSpec:
    """XAUUSD: 0.01 ticks at $1, so a $3.00 stop also risks $300 a lot.

    The same risk as the forex case, reached by completely different arithmetic.
    A sizer that assumed a 5-digit pair and a 100000 contract size would be wrong
    here by two orders of magnitude, and wrong in the direction of *oversizing*.
    """
    defaults: dict[str, object] = {
        "symbol": "XAUUSD",
        "contract_size": "100",
        "tick_size": "0.01",
        "tick_value_profit": "1.0",
        "tick_value_loss": "1.0",
        "volume_min": "0.01",
        "volume_max": "50.0",
        "volume_step": "0.01",
        "digits": 2,
        "point": "0.01",
        "currency": "USD",
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    defaults.update(overrides)
    return SymbolSpec(**_decimals(defaults))  # type: ignore[arg-type]


#: The ``SymbolSpec`` fields that are numeric. Everything else is a string -- a
#: symbol name is not a number, and ``Decimal("EURUSD")`` is an error rather than
#: a conversion.
_NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        "contract_size",
        "tick_size",
        "tick_value_profit",
        "tick_value_loss",
        "volume_min",
        "volume_max",
        "volume_step",
        "point",
    }
)


def _decimals(defaults: dict[str, object]) -> dict[str, object]:
    """The numeric fields as ``Decimal``, everything else untouched.

    The builders above are written with string literals so the numbers read as the
    numbers a broker publishes -- ``"0.00001"`` rather than ``1e-05`` -- and this
    is where they become the ``Decimal`` the model requires. Converting here rather
    than at each call site means no test can accidentally hand the domain a
    ``float``, which is the one thing the ``Decimal`` decision exists to prevent.
    """
    return {
        key: Decimal(value) if key in _NUMERIC_FIELDS and isinstance(value, str) else value
        for key, value in defaults.items()
    }


def _budget(amount: str = "50", percent: str = "0.5") -> RiskBudget:
    """A $50 budget, i.e. half a percent of $10,000."""
    return RiskBudget(
        amount=Decimal(amount),
        balance=Decimal("10000"),
        risk_percent=Decimal(percent),
        currency="USD",
        reward_amount=Decimal(amount),
        reward_risk_ratio=Decimal("1.0"),
    )


def _stop(distance: str = "0.00300", price: str = "1.09700") -> StopLoss:
    return StopLoss(price=Decimal(price), distance=Decimal(distance), source=StopSource.SIGNAL)


class TestTheFormula:
    def test_fifty_dollars_over_a_three_hundred_tick_stop_is_a_sixth_of_a_lot(self) -> None:
        # 0.00300 / 0.00001 = 300 ticks, 300 * $1 = $300 a lot, $50 / $300 = 0.1666...
        size = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert size.ticks == Decimal("300")
        assert size.risk_per_unit == Decimal("300")
        assert size.raw_volume == Decimal("50") / Decimal("300")

    def test_the_rounded_volume_is_on_the_brokers_step(self) -> None:
        size = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert size.volume == Decimal("0.16")

    def test_the_planned_loss_is_the_risk_per_unit_times_the_volume(self) -> None:
        size = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert size.planned_loss == Decimal("300") * Decimal("0.16")

    def test_gold_reaches_the_same_answer_by_different_arithmetic(self) -> None:
        # $3.00 / $0.01 = 300 ticks at $1 = $300 a lot, exactly as the 30-pip
        # forex stop does. The pair of these two tests is what distinguishes a
        # tick-value sizer from a forex sizer that happens to work.
        forex = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        gold = resolve_position_size(
            _stop(distance="3.00", price="2397.00"), _budget(), _gold()
        ).unwrap()
        assert forex.risk_per_unit == gold.risk_per_unit == Decimal("300")
        assert forex.volume == gold.volume

    def test_the_contract_size_does_not_enter_the_calculation(self) -> None:
        # It already is folded into the tick value, which is quoted per lot. A
        # sizer that multiplied by it as well would double-count on every
        # instrument, and by a factor of 100000 on a forex pair.
        one = resolve_position_size(_stop(), _budget(), _spec(contract_size="1")).unwrap()
        hundred_thousand = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert one.volume == hundred_thousand.volume

    def test_every_input_is_retained_so_the_arithmetic_can_be_recomputed(self) -> None:
        # "0.16 lots" cannot be checked against anything. These can.
        size = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert size.risk_amount == Decimal("50")
        assert size.stop_distance == Decimal("0.00300")
        assert size.risk_per_unit == Decimal("300")
        assert size.ticks == Decimal("300")
        assert size.tick_size == Decimal("0.00001")
        assert size.tick_value == Decimal("1.0")

    def test_the_resolution_reports_the_steps_it_took(self) -> None:
        details = resolve_position_size(_stop(), _budget(), _spec()).details
        assert details["ticks"] == "300"
        assert Decimal(str(details["risk_per_unit"])) == Decimal("300")
        # Compared numerically: `Decimal` division runs to 28 significant digits,
        # so the string form is a long tail of 6s and asserting it would be
        # asserting a formatting choice rather than the arithmetic.
        assert Decimal(str(details["raw_volume"])) == Decimal("50") / Decimal("300")
        assert details["rounded_volume"] == "0.16"


class TestTheConservativeTickValue:
    def test_the_cheaper_of_the_two_tick_values_is_not_used(self) -> None:
        # They differ on a hedging account and on some CFDs. The volume is
        # inversely proportional to the tick value, so the *smaller* tick value
        # gives the *larger* position -- which means taking the minimum is the
        # least conservative choice there is.
        asymmetric = _spec(tick_value_profit="2.0", tick_value_loss="3.0")
        size = resolve_position_size(_stop(), _budget(), asymmetric).unwrap()
        assert size.tick_value == Decimal("3.0")
        assert size.risk_per_unit == Decimal("900")

    def test_a_stop_that_costs_three_a_tick_produces_a_third_of_the_position(
        self,
    ) -> None:
        # The concrete failure Phase 1's `min` would have produced: a $50 budget
        # over 300 ticks. Dividing by $2 a tick gives 0.08 lots, and if that stop
        # were hit the loss would be 300 * $3 * 0.08 = $72 -- a 44% overrun of a
        # budget nobody chose to exceed. The correct answer at $3 a tick is $48.
        size = resolve_position_size(
            _stop(), _budget(), _spec(tick_value_profit="2.0", tick_value_loss="3.0")
        ).unwrap()
        assert size.volume == Decimal("0.05")
        assert size.planned_loss == Decimal("45")
        assert size.is_within_budget

    def test_the_larger_value_is_used_whichever_way_the_two_are_ordered(
        self,
    ) -> None:
        # A larger divisor is a smaller position, and an under-sized position is
        # the safe failure. So the ordering of the two raw values cannot change
        # which one the sizer divides by.
        loss_heavy = _spec(tick_value_profit="2.0", tick_value_loss="3.0")
        profit_heavy = _spec(tick_value_profit="3.0", tick_value_loss="2.0")
        assert (
            resolve_position_size(_stop(), _budget(), loss_heavy).unwrap().volume
            == resolve_position_size(_stop(), _budget(), profit_heavy).unwrap().volume
        )

    def test_the_planned_loss_can_never_exceed_the_budget_on_an_asymmetric_symbol(
        self,
    ) -> None:
        # The property the correction exists to establish, stated directly rather
        # than through one fixture: whatever the tick values, what the sizer calls
        # the risk is an upper bound on the risk, so the arithmetic holds.
        for profit, loss in [("1.0", "4.0"), ("4.0", "1.0"), ("0.5", "0.5")]:
            size = resolve_position_size(
                _stop(), _budget(), _spec(tick_value_profit=profit, tick_value_loss=loss)
            ).unwrap()
            assert size.is_within_budget
            assert size.planned_loss <= size.risk_amount

    def test_a_zero_tick_value_does_not_zero_the_conservative_one(self) -> None:
        # Some symbols report no loss tick value at all rather than zero. A plain
        # minimum of those and the profit value would be zero, and the sizer would
        # refuse every trade on a perfectly tradable instrument.
        spec = _spec(tick_value_profit="1.0", tick_value_loss="0")
        assert resolve_position_size(_stop(), _budget(), spec).unwrap().tick_value == Decimal("1.0")


class TestRoundingIsDown:
    def test_the_raw_volume_is_never_rounded_up_to_the_step(self) -> None:
        # 0.1666... rounds up to 0.17, which would risk $51 against a $50 budget.
        size = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert size.raw_volume > size.volume
        assert size.rounded is True

    def test_a_size_can_never_exceed_its_own_budget(self) -> None:
        # The invariant the whole module exists to preserve.
        size = resolve_position_size(_stop(), _budget(), _spec()).unwrap()
        assert size.is_within_budget
        assert size.planned_loss < size.risk_amount

    def test_a_budget_that_exactly_fills_a_step_is_not_marked_rounded(self) -> None:
        # 0.50 lots * $300 = exactly the $150 budget, so nothing was given up.
        size = resolve_position_size(_stop(), _budget(amount="150"), _spec()).unwrap()
        assert size.volume == Decimal("0.50")
        assert size.rounded is False
        assert size.planned_loss == Decimal("150")

    def test_a_volume_exactly_on_the_step_is_not_rounded(self) -> None:
        size = resolve_position_size(_stop(), _budget(amount="300"), _spec()).unwrap()
        assert size.volume == Decimal("1.00")
        assert size.rounded is False

    def test_a_sizer_that_rounded_up_would_be_caught_by_the_budget_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard exists for the day somebody changes the rounding direction.

        Rounding up is a one-character change and it would break the central
        invariant silently -- every volume would still look like a plausible lot
        count. So the last check in the sizer refuses rather than returning a size
        whose planned loss exceeds the budget, and this test drives that branch by
        making the round-up happen for real.
        """
        monkeypatch.setattr(
            SymbolSpec,
            "round_volume",
            lambda self, volume, *, round_down=True: (
                self.volume_step
                * ((volume / self.volume_step).to_integral_value(rounding="ROUND_CEILING"))
            ),
        )
        resolution = resolve_position_size(_stop(), _budget(), _spec())
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.SIZING_FAILED
        assert "budget" in resolution.explanation

    def test_a_rounding_helper_that_stopped_rounding_is_caught_by_the_constraint_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second guard covers the other half of the same refactor.

        If ``round_volume`` were changed to return the raw volume unchanged, the
        sizer would pass a size the broker does not accept. That is the expensive
        kind of mistake -- a rejected order and a confusing log entry on the far
        side -- so the broker-constraint check runs on the sizer's own output
        rather than being assumed by it.
        """
        monkeypatch.setattr(SymbolSpec, "round_volume", lambda self, volume, **_: volume)
        resolution = resolve_position_size(_stop(), _budget(), _spec())
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.VOLUME_NOT_ON_STEP


class TestBelowTheBrokerMinimum:
    def test_a_volume_under_the_minimum_is_refused(self) -> None:
        # $0.20 over a 300-tick stop is 0.0006 lots, under a 0.01 minimum.
        resolution = resolve_position_size(_stop(), _budget(amount="0.20"), _spec())
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.VOLUME_BELOW_BROKER_MINIMUM
        assert resolution.value is None

    def test_it_is_never_floored_up_to_the_minimum(self) -> None:
        # THE test. A volume of 0.01 over a $300 stop risks $3 against a $0.20
        # budget -- fifteen times the money anybody chose to risk, decided by a
        # broker rather than by the trader.
        resolution = resolve_position_size(_stop(), _budget(amount="0.20"), _spec())
        assert resolution.value is None
        assert "rather than" in resolution.explanation
        assert "minimum of 0.01" in resolution.explanation

    def test_the_message_names_the_three_real_remedies(self) -> None:
        # A refusal an operator cannot act on gets ignored. These are the three:
        # more risk, a different instrument, or a tighter stop.
        explanation = resolve_position_size(_stop(), _budget(amount="0.20"), _spec()).explanation
        assert "BRIDGE_RISK_PERCENT" in explanation
        assert "instrument" in explanation
        assert "stop" in explanation

    def test_the_partial_arithmetic_is_kept(self) -> None:
        # "Raw 0.0006, rounded 0.00, minimum 0.01" is the difference between a
        # refusal that can be debugged and one that can only be observed.
        details = resolve_position_size(_stop(), _budget(amount="0.20"), _spec()).details
        assert Decimal(str(details["raw_volume"])) == Decimal("0.20") / Decimal("300")
        assert details["rounded_volume"] == "0.00"
        assert details["risk_amount"] == "0.20"
        assert details["ticks"] == "300"

    def test_exactly_the_minimum_is_traded(self) -> None:
        # The boundary: at the minimum the trade is allowed, at a hair below it is
        # refused. An off-by-one here would either refuse good trades or, far
        # worse, allow one that risks far too much.
        assert resolve_position_size(_stop(), _budget(amount="3.00"), _spec()).ok is True
        assert resolve_position_size(_stop(), _budget(amount="2.99"), _spec()).ok is False

    def test_a_coarse_step_makes_more_trades_untradeable(self) -> None:
        # A 0.1 step with a 0.1 minimum needs $30 before anything is tradeable
        # here, where the 0.01 step needed $3. That is a fact about the account
        # and the instrument, and it is the refusal telling the operator so.
        coarse = _spec(volume_min="0.1", volume_step="0.1")
        assert resolve_position_size(_stop(), _budget(amount="30"), coarse).ok is True
        assert resolve_position_size(_stop(), _budget(amount="29.99"), coarse).ok is False


class TestAboveTheBrokerMaximum:
    def test_a_volume_over_the_maximum_is_clamped_down_and_recorded(self) -> None:
        # Unlike a sub-minimum volume this is safe: clamping down *reduces* the
        # risk. The only thing required is that the log says so, because a
        # position at the maximum is not a position at the intended size.
        size = resolve_position_size(_stop(), _budget(amount="60000"), _spec()).unwrap()
        assert size.volume == Decimal("100.0")
        assert size.clamped_to_maximum is True
        assert size.is_within_budget

    def test_a_clamped_size_reports_a_real_risk_below_the_budget(self) -> None:
        size = resolve_position_size(_stop(), _budget(amount="60000"), _spec()).unwrap()
        assert size.planned_loss == Decimal("30000")
        assert size.risk_amount == Decimal("60000")

    def test_a_size_at_the_maximum_is_not_marked_as_clamped(self) -> None:
        # $300 a lot * 100 lots = exactly the $30,000 the budget allows.
        size = resolve_position_size(_stop(), _budget(amount="30000"), _spec()).unwrap()
        assert size.volume == Decimal("100.0")
        assert size.clamped_to_maximum is False

    def test_the_clamp_is_recorded_in_the_resolution_too(self) -> None:
        details = resolve_position_size(_stop(), _budget(amount="60000"), _spec()).details
        assert details["clamped_to_maximum"] is True


class TestUnusableSpecifications:
    def test_a_zero_stop_distance_is_refused_rather_than_dividing_by_zero(self) -> None:
        # `StopLoss` validates this at construction, so the stub is how the branch
        # is reached: a stop built by a path that skipped validation.
        class _ZeroStop:
            distance = Decimal("0")
            price = Decimal("1.09700")
            source = StopSource.SIGNAL
            basis = ""

        resolution = resolve_position_size(_ZeroStop(), _budget(), _spec())  # type: ignore[arg-type]
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.INVALID_STOP_DISTANCE

    def test_a_zero_tick_size_is_refused_rather_than_defaulted(self) -> None:
        class _NoTickSize:
            """A specification that never got its tick size.

            Stands in for a symbol the terminal has not finished loading, or an
            adapter that did not populate the field. The point is that an invented
            tick size would produce a confident and entirely wrong volume.
            """

            symbol = "EURUSD"
            symbol_normalised = "EURUSD"
            tick_size = Decimal("0")
            tick_value_profit = Decimal("1.0")
            tick_value_loss = Decimal("1.0")
            conservative_tick_value = Decimal("1.0")

        resolution = resolve_position_size(_stop(), _budget(), _NoTickSize())  # type: ignore[arg-type]
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.SYMBOL_SPEC_UNAVAILABLE

    def test_a_zero_tick_value_is_refused_rather_than_defaulted(self) -> None:
        # Both sides zero, because the sizer takes the larger and a zero on one
        # side alone is now correctly ignored. A specification with no tick value
        # at all is an unfinished symbol, not a cheap one.
        blank = _spec(tick_value_profit="0", tick_value_loss="0")
        resolution = resolve_position_size(_stop(), _budget(), blank)
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.SYMBOL_SPEC_UNAVAILABLE

    def test_a_zero_on_one_side_alone_does_not_zero_the_sizing(self) -> None:
        # The counterpart, and the reason the guard is on the conservative value
        # rather than on the raw fields.
        spec = _spec(tick_value_profit="1.0", tick_value_loss="0")
        assert resolve_position_size(_stop(), _budget(), spec).ok is True

    def test_the_message_says_the_value_comes_from_the_terminal(self) -> None:
        # So the operator knows where to look, rather than suspecting the maths.
        blank = _spec(tick_value_profit="0", tick_value_loss="0")
        explanation = resolve_position_size(_stop(), _budget(), blank).explanation
        assert "terminal" in explanation

    def test_there_is_no_fallback_that_produces_a_tick_value(self) -> None:
        """No code path invents a balance or a tick value.

        The mirror of ``test_there_is_no_fallback_that_produces_a_stop``. A stop
        invented by the bridge is a number nobody chose; a tick value invented by
        the bridge is a divisor nobody checked, and the two produce the same
        failure -- a confident volume that risks the wrong amount.

        Walks the module's AST, because the failure is invisible at runtime: an
        invented tick value yields a volume that looks entirely ordinary.
        """
        tree = ast.parse(inspect.getsource(module))

        # No numeric literal that could serve as a tick size, a tick value, a
        # volume or a money amount. The only permitted number is the zero
        # compared against in the guards.
        allowed_numbers = {0, 1}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                assert node.value in allowed_numbers, (
                    f"sizing.py contains the literal {node.value!r} at line {node.lineno}. Every "
                    f"number in this module is expected to be a comparison bound, not a level: a "
                    f"literal here is how an invented tick value or a default volume gets "
                    f"introduced."
                )

        # And the arithmetic reads its facts off the arguments rather than
        # building them. `PositionSize` is the one construction, and every one of
        # its numeric fields must come from a name that was computed above it.
        constructions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PositionSize"
        ]
        assert constructions, "expected the sizer to construct PositionSize values"
        for call in constructions:
            for keyword in ("volume", "risk_amount", "risk_per_unit", "ticks", "tick_value"):
                value = next(kw for kw in call.keywords if kw.arg == keyword)
                source = ast.unparse(value.value)
                assert source not in {"0", "1"} and "Decimal(" not in source, (
                    f"PositionSize.{keyword} came from the literal {source!r} at line "
                    f"{value.lineno}. Every field must be arithmetic the module performed on the "
                    f"balance, the stop and the specification."
                )


class TestABudgetThatDidNotComeFromTheResolver:
    def test_a_zero_budget_is_refused_by_the_constraint_check_rather_than_dividing(
        self,
    ) -> None:
        # `RiskBudget` refuses a zero amount at construction and
        # `resolve_risk_budget` cannot produce one, so this is only reachable by a
        # caller that built the budget itself -- a deserialiser, or a future
        # caller. The outcome is still correct: a zero volume is below the broker
        # minimum, which is the honest reason, and it needs no branch of its own to
        # produce it. The stub rather than a broken object because the point is
        # that the sizer survives a budget it did not build.
        class _EmptyBudget:
            amount = Decimal("0")
            balance = Decimal("10000")
            risk_percent = Decimal("0.5")
            currency = "USD"

        resolution = resolve_position_size(_stop(), _EmptyBudget(), _spec())  # type: ignore[arg-type]
        assert resolution.ok is False
        assert resolution.reason in {
            RejectionReason.INVALID_VOLUME,
            RejectionReason.VOLUME_BELOW_BROKER_MINIMUM,
        }
        assert resolution.value is None


class TestBrokerConstraints:
    def test_a_tradeable_volume_passes(self) -> None:
        assert check_broker_constraints(Decimal("0.16"), _spec()).ok

    def test_a_zero_volume_is_refused(self) -> None:
        resolution = check_broker_constraints(Decimal("0"), _spec())
        assert resolution.reason is RejectionReason.INVALID_VOLUME

    def test_a_negative_volume_is_refused(self) -> None:
        assert (
            check_broker_constraints(Decimal("-1"), _spec()).reason
            is RejectionReason.INVALID_VOLUME
        )

    def test_the_zero_message_says_zero_is_not_a_way_to_decline(self) -> None:
        # "No position" and "a position of nothing" are different answers, and a
        # zero volume must not be readable as a decision.
        assert "not a way to decline" in check_broker_constraints(Decimal("0"), _spec()).explanation

    def test_a_sub_minimum_volume_is_refused_with_its_own_code(self) -> None:
        resolution = check_broker_constraints(Decimal("0.009"), _spec())
        assert resolution.reason is RejectionReason.VOLUME_BELOW_BROKER_MINIMUM

    def test_an_above_maximum_volume_is_refused_with_its_own_code(self) -> None:
        resolution = check_broker_constraints(Decimal("100.01"), _spec())
        assert resolution.reason is RejectionReason.VOLUME_ABOVE_BROKER_MAXIMUM

    def test_the_above_maximum_message_says_clamping_down_would_be_safe(self) -> None:
        # The asymmetry is deliberate and an operator needs to know it: below the
        # minimum is fatal, above the maximum is merely wasteful.
        explanation = check_broker_constraints(Decimal("1000"), _spec()).explanation
        assert "reduces the risk" in explanation

    def test_a_volume_off_the_step_is_refused_with_its_own_code(self) -> None:
        # The only producer of this code anywhere in the bridge.
        resolution = check_broker_constraints(Decimal("0.165"), _spec())
        assert resolution.reason is RejectionReason.VOLUME_NOT_ON_STEP

    def test_the_off_step_message_says_where_the_volume_came_from(self) -> None:
        # Because reaching here means a volume came from somewhere that did not
        # round, and that is a bug worth finding rather than a broker quirk.
        assert (
            "rounds down to the step"
            in check_broker_constraints(Decimal("0.165"), _spec()).explanation
        )

    def test_the_step_check_survives_repeated_binary_values(self) -> None:
        # 0.1 + 0.2 is the classic case: a step of 0.1 and a volume built by
        # repeated addition is not a whole number of steps by exact arithmetic.
        # The check has to agree with `round_volume`, or the sizer would produce
        # a volume and then refuse it.
        spec = _spec(volume_step="0.1", volume_min="0.1")
        accumulated = Decimal("0")
        for _ in range(3):
            accumulated += spec.volume_step
        assert check_broker_constraints(accumulated, spec).ok

    def test_every_refusal_carries_the_constraints_it_judged_against(self) -> None:
        details = check_broker_constraints(Decimal("0.009"), _spec()).details
        assert details["volume_min"] == "0.01"
        assert details["volume_max"] == "100.0"
        assert details["volume_step"] == "0.01"
