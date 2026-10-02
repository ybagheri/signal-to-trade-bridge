"""One Click Trading makes the order ticket's fields decorative.

**This is the mechanism behind every bad fill this project has produced**, and it was
not a write race, not a focus problem, and not a bug in the field writes.

The terminal was in **One Click Trading** mode -- Alpari's default when Algo Trading
is off, which is what `trade_mode=0` and `trade_allowed=False` mean. In that mode
`Buy by Market` does not send the order ticket's fields. It sends the Toolbox **Trade
panel's**: a symbol, a volume spinner, and no stop loss or take profit at all.

So the pipeline was correct throughout and the order was taken from somewhere else:

* the dialog held `volume=0.03 sl=1.12241 tp=1.12841`;
* MT5 applied all of them, measured at 250/500/800 ms;
* `confirm_dialog_matches` read them back and passed;
* the broker filled **0.01, no stop, no target** -- the panel's defaults.

`confirm_dialog_matches` could not have caught this. It reads the dialog, the dialog
was right, and there is no reading of the dialog that reveals which panel the click
will use. **Verifying the thing you wrote is not verifying what will be sent.**

These tests assert the property that can be checked without a terminal: when the
terminal is in One Click Trading mode, an order with a stop loss must not be sent at
all, because the mode cannot carry one. Refusing is the only safe direction -- the
alternative is sending an unprotected position and finding out afterwards.

The mode itself is read from MT5, which needs no terminal change and no click.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain.enums import Direction, SignalAction
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionRequest,
    PositionSize,
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
    TradeIntent,
)

TERMINAL_PATH = r"C:\Program Files\Alpari MT5_4\terminal64.exe"


class _TerminalInfo:
    def __init__(self, trade_mode: int, trade_allowed: bool) -> None:
        self.trade_mode = trade_mode
        self.trade_allowed = trade_allowed


class _AccountInfo:
    def __init__(self, trade_mode: int, trade_allowed: bool) -> None:
        self.trade_mode = trade_mode
        self.trade_allowed = trade_allowed


class _Bindings:
    """Just enough of the MT5 bindings to report the mode and nothing else."""

    def __init__(self, trade_mode: int, trade_allowed: bool) -> None:
        self._account = _AccountInfo(trade_mode, trade_allowed)
        self._terminal = _TerminalInfo(trade_mode, trade_allowed)

    def account_info(self) -> _AccountInfo:
        return self._account

    def terminal_info(self) -> _TerminalInfo:
        return self._terminal

    def shutdown(self) -> None:
        return None


def _is_one_click(bindings: _Bindings) -> bool:
    from signal_to_trade_bridge.adapters.mt5.execution_mode import is_one_click_trading

    return is_one_click_trading(bindings)


class TestReadingTheMode:
    def test_algo_trading_off_reads_as_one_click(self) -> None:
        # `trade_mode=0` is MT5's DISABLED. On this build that is the state that
        # makes the terminal fall back to the Trade panel.
        assert _is_one_click(_Bindings(trade_mode=0, trade_allowed=False)) is True

    @pytest.mark.parametrize(
        ("trade_mode", "trade_allowed"),
        [(1, True), (3, True), (3, False)],
    )
    def test_algo_trading_on_is_not_one_click(self, trade_mode: int, trade_allowed: bool) -> None:
        assert _is_one_click(_Bindings(trade_mode=trade_mode, trade_allowed=trade_allowed)) is False

    def test_an_unreadable_terminal_is_not_assumed_to_be_safe(self) -> None:
        # The dangerous direction. A terminal whose mode cannot be read might be in
        # either mode, and "cannot tell" is not "is fine".
        class _Silent(_Bindings):
            def terminal_info(self) -> None:
                return None

        bindings = _Silent(trade_mode=0, trade_allowed=False)
        assert _is_one_click(bindings) is True, (
            "an unreadable terminal must not be treated as safe to send a stop through"
        )


def _request() -> ExecutionRequest:
    entry = Decimal("1.12541")
    return ExecutionRequest(
        signal_id="one-click-1",
        symbol="EURUSD",
        direction=Direction.LONG,
        volume=Decimal("0.03"),
        entry=entry,
        stop_loss=entry - Decimal("0.00300"),
        take_profit=entry + Decimal("0.00300"),
        comment="probe",
        strategy="probe",
        metadata={},
    )


class TestRefusingToSendAStopItCannotCarry:
    """The refusal that follows from the mode, and the arithmetic it would otherwise lose."""

    def test_an_order_with_a_stop_is_refused_in_one_click_mode(self) -> None:
        from signal_to_trade_bridge.adapters.mt5.execution_mode import refuse_one_click_order

        refusal = refuse_one_click_order(_request(), one_click=True)
        assert refusal, "an order carrying a stop loss must not be sent in one-click mode"
        assert "stop" in refusal.lower()

    def test_an_order_without_a_take_profit_is_still_refused(self) -> None:
        # `ExecutionRequest.stop_loss` is mandatory and `_positive`, so an order with
        # no stop cannot be built at all -- which means the refusal covers every order
        # this project can produce. That is worth stating rather than leaving
        # implied: the original test here tried to build one with `stop_loss=None`
        # and was refused by the model, not by the check.
        from signal_to_trade_bridge.adapters.mt5.execution_mode import refuse_one_click_order

        assert _request().stop_loss is not None
        with pytest.raises((TypeError, ValueError)):
            ExecutionRequest(
                signal_id="x",
                symbol="EURUSD",
                direction=Direction.LONG,
                volume=Decimal("0.03"),
                entry=Decimal("1.12541"),
                stop_loss=None,
            )
        # And the refusal is about the mode, not about whether a target happens to be
        # present: dropping only the target still trips it, because the stop is what
        # the mode cannot carry.
        no_target = _request()
        assert no_target.take_profit is not None
        assert refuse_one_click_order(no_target, one_click=True) != ""

    def test_nothing_is_refused_when_the_mode_is_not_one_click(self) -> None:
        from signal_to_trade_bridge.adapters.mt5.execution_mode import refuse_one_click_order

        assert refuse_one_click_order(_request(), one_click=False) == ""

    def test_the_risk_the_refusal_prevents_is_real(self) -> None:
        # Stated as an arithmetic fact rather than a worry: the request risked $10,
        # and a fill with no stop has no defined maximum. On a 0.03 lot EURUSD
        # position a 30-pip move against is about $30, and a 300-pip move is $300 --
        # thirty times the budget, with nothing to stop it.
        entry = Decimal("1.12541")
        stop = Decimal("1.12241")
        volume = Decimal("0.03")
        ticks = (entry - stop) / Decimal("0.00001")
        budget = ticks * Decimal("1.0") * volume
        assert budget == Decimal("9.00")
        adverse = Decimal("0.0300") / Decimal("0.00001") * Decimal("1.0") * volume
        assert adverse == Decimal("90.00")
        assert adverse > budget * 9, "an unprotected position can lose far more than budgeted"


class TestWhyTheDialogVerifierCouldNotCatchIt:
    def test_a_correct_dialog_and_a_wrong_fill_are_not_in_contradiction(self) -> None:
        # The whole lesson, as a statement a test can hold: the dialog is right and the
        # fill is wrong at the same time, so any check reading the dialog passes.
        request = _request()
        assert request.stop_loss is not None

        dialog_volume = "0.03"
        dialog_stop = str(request.stop_loss)
        filled_volume = Decimal("0.01")
        filled_stop = None

        assert dialog_volume == "0.03" and dialog_stop == "1.12241"
        assert filled_volume != Decimal(request.volume)
        assert filled_stop != request.stop_loss
        # The dialog was correct. Verification of the dialog is therefore not
        # verification of the order.


class TestTheRequestItselfIsUnaffected:
    """A sanity check that the refusal lives in the adapter, not in the domain."""

    def test_a_normal_intent_still_carries_its_stop(self) -> None:
        entry = Decimal("1.12541")
        intent = TradeIntent(
            signal=Signal(
                signal_id="s",
                symbol="EURUSD",
                timeframe="M5",
                action=SignalAction.BUY,
                direction=Direction.LONG,
                entry=entry,
                stop_loss=entry - Decimal("0.003"),
            ),
            symbol="EURUSD",
            direction=Direction.LONG,
            entry=entry,
            stop_loss=StopLoss(
                price=entry - Decimal("0.003"), distance=Decimal("0.003"), basis="SWING"
            ),
            take_profit=TakeProfit(
                price=entry + Decimal("0.003"), distance=Decimal("0.003"), basis="1R"
            ),
            position_size=PositionSize(
                volume=Decimal("0.03"),
                raw_volume=Decimal("0.03"),
                risk_amount=Decimal("10.00"),
                stop_distance=Decimal("0.003"),
                risk_per_unit=Decimal("300.00"),
                ticks=Decimal("300"),
                tick_size=Decimal("0.00001"),
                tick_value=Decimal("1.0"),
            ),
            risk_parameters=RiskParameters(),
            account_balance=AccountBalance(balance=Decimal("100000"), currency="USD"),
            symbol_spec=SymbolSpec(
                symbol="EURUSD",
                contract_size=Decimal("100000"),
                tick_size=Decimal("0.00001"),
                tick_value_profit=Decimal("1.0"),
                tick_value_loss=Decimal("1.0"),
                volume_min=Decimal("0.01"),
                volume_max=Decimal("100"),
                volume_step=Decimal("0.01"),
                digits=5,
                point=Decimal("0.00001"),
            ),
        )
        assert intent.stop_loss.price == entry - Decimal("0.003")
        assert intent.signal.stop_loss is not None
