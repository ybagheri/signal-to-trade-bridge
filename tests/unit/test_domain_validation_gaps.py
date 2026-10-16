"""The constructor guards `test_domain_models.py` does not reach.

Every line here is a `raise` in a `__post_init__`: the model's refusal to exist
in an invalid state. Each test builds a valid object and changes one field, so
a failure names the guard that stopped firing rather than the factory that
built the object. `dataclasses.replace` re-runs validation, which is what
makes it the right tool: a hand-built invalid object would prove nothing about
the constructor.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain.enums import DecisionAction, Direction
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionRequest,
    ExecutionResult,
    PositionSize,
    RiskParameters,
    Signal,
    SymbolSpec,
    TradeDecision,
)


class TestAccountBalanceGuards:
    def test_rejects_a_zero_equity(self) -> None:
        # Zero equity with a positive balance is the account that has lost
        # everything it made: sizing from it would divide by a number that
        # says "nothing left".
        with pytest.raises(ValueError, match="equity must be positive"):
            AccountBalance(balance=Decimal("10000"), currency="USD", equity=Decimal("0"))


class TestSymbolSpecGuards:
    def test_rejects_a_blank_symbol(self, forex_spec: SymbolSpec) -> None:
        with pytest.raises(ValueError, match="symbol is required"):
            dataclasses.replace(forex_spec, symbol="  ")

    def test_rejects_a_negative_tick_value(self, forex_spec: SymbolSpec) -> None:
        with pytest.raises(ValueError, match="tick values cannot be negative"):
            dataclasses.replace(forex_spec, tick_value_loss=Decimal("-1"))

    def test_rejects_negative_digits(self, forex_spec: SymbolSpec) -> None:
        with pytest.raises(ValueError, match="digits cannot be negative"):
            dataclasses.replace(forex_spec, digits=-1)

    def test_a_zero_step_returns_the_volume_unchanged(self, forex_spec: SymbolSpec) -> None:
        # Unreachable through construction -- the constructor refuses a
        # non-positive step -- so the step is bypassed rather than replaced.
        # The guard exists so a future change that allowed a zero step would
        # divide by zero here; the test pins the safe behaviour, not a path.
        object.__setattr__(forex_spec, "volume_step", Decimal("0"))
        assert forex_spec.round_volume(Decimal("0.377")) == Decimal("0.377")


class TestSignalGuards:
    def test_rejects_a_blank_signal_id(self, buy_signal: Signal) -> None:
        with pytest.raises(ValueError, match="signal_id is required"):
            dataclasses.replace(buy_signal, signal_id="  ")

    def test_rejects_a_tradable_action_without_an_entry(self, buy_signal: Signal) -> None:
        # An entry-less BUY is a signal nobody can act on. Defaulting the
        # entry would push the failure into sizing as confusing arithmetic
        # rather than a clear validation error here.
        with pytest.raises(ValueError, match="requires an entry price"):
            dataclasses.replace(buy_signal, entry=None)

    def test_rejects_a_bar_index_below_minus_one(self, buy_signal: Signal) -> None:
        # -1 is the model's "no bar" and the only permitted absence: the
        # mapper and the identity guard both branch on it, so a lower value
        # would fall through every one of them.
        with pytest.raises(ValueError, match="bar_index cannot be below -1"):
            dataclasses.replace(buy_signal, bar_index=-2)


class TestRiskParametersGuards:
    def test_rejects_a_zero_max_spread(self) -> None:
        with pytest.raises(ValueError, match="max_spread must be positive"):
            RiskParameters(max_spread=Decimal("0"))

    def test_rejects_a_negative_position_limit(self) -> None:
        with pytest.raises(ValueError, match="max_open_positions cannot be negative"):
            RiskParameters(max_open_positions=-1)

    def test_rejects_an_out_of_range_evidence_floor(self) -> None:
        with pytest.raises(ValueError, match="minimum_evidence_score must be between"):
            RiskParameters(minimum_evidence_score=1.5)

    def test_risk_amount_rejects_a_dead_account(self) -> None:
        with pytest.raises(ValueError, match="balance must be positive"):
            RiskParameters().risk_amount(Decimal("0"))


class TestPositionSizeGuards:
    def _size(self) -> PositionSize:
        return PositionSize(
            volume=Decimal("0.10"),
            raw_volume=Decimal("0.10"),
            risk_amount=Decimal("50.00"),
            stop_distance=Decimal("0.00300"),
            risk_per_unit=Decimal("500.00"),
            ticks=Decimal("300"),
            tick_size=Decimal("0.00001"),
            tick_value=Decimal("1.0"),
        )

    def test_rejects_a_non_positive_raw_volume(self) -> None:
        with pytest.raises(ValueError, match="raw_volume must be positive"):
            dataclasses.replace(self._size(), raw_volume=Decimal("0"))

    def test_rejects_a_non_positive_risk_per_unit(self) -> None:
        with pytest.raises(ValueError, match="risk_per_unit must be positive"):
            dataclasses.replace(self._size(), risk_per_unit=Decimal("0"))


class TestExecutionRequestGuards:
    def _request(self) -> ExecutionRequest:
        return ExecutionRequest(
            signal_id="x",
            symbol="EURUSD",
            direction=Direction.LONG,
            volume=Decimal("0.10"),
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
        )

    def test_rejects_a_blank_signal_id(self) -> None:
        with pytest.raises(ValueError, match="signal_id is required"):
            dataclasses.replace(self._request(), signal_id="  ")

    def test_rejects_a_blank_symbol(self) -> None:
        with pytest.raises(ValueError, match="symbol is required"):
            dataclasses.replace(self._request(), symbol="  ")


class TestExecutionResultGuards:
    def test_rejects_a_blank_status(self) -> None:
        with pytest.raises(ValueError, match="status is required"):
            ExecutionResult(signal_id="x", status="  ")


class TestTradeDecisionGuards:
    def test_rejects_a_blank_signal_id(self) -> None:
        with pytest.raises(ValueError, match="signal_id is required"):
            TradeDecision(
                signal_id="  ",
                action=DecisionAction.NO_TRADE,
                reason="EVIDENCE_BELOW_MINIMUM",
            )
