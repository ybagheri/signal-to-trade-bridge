"""The MT5 data adapter, against an injected bindings module.

**No test in this file imports `MetaTrader5`, and none needs a terminal.** That is
the property the whole `MT5Feed` pattern exists for, and it is the reason the
adapter is buildable at all on a machine that has never had MetaTrader 5 installed
— which is the machine this was written on.

The doubles below are hand-written rather than generated, and they are shaped
like the bindings' named tuples: the adapter reads named fields off them, so a
double that returned a dict would test a different code path than production runs.
A double that is *too* accommodating is how an adapter passes its tests and fails
against the real thing, so the awkward cases are tested explicitly: a terminal
that answers `None`, a field that is `None`, a currency that is empty, and a
`tick_size` small enough that a naive `Decimal()` conversion would be visibly
wrong.

What is **not** here, and cannot be: anything requiring the real bindings. Those
checks are `mt5`-marked and belong to Phase 11, on a machine with a terminal.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from signal_to_trade_bridge.adapters.fake import FakeAccountProvider, FakeSymbolSpecProvider
from signal_to_trade_bridge.adapters.mt5 import (
    MT5AccountProvider,
    MT5SymbolSpecProvider,
    MT5Unavailable,
)
from signal_to_trade_bridge.adapters.mt5 import bindings as bindings_module
from signal_to_trade_bridge.domain.errors import IntegrationError
from signal_to_trade_bridge.domain.models import AccountBalance, SymbolSpec
from signal_to_trade_bridge.ports import AccountProvider, SymbolSpecProvider

#: The login every stub reports, so a test can say "the snapshot agrees with the
#: terminal" without repeating the number and risking a typo that makes the
#: cross-check pass for the wrong reason.
LOGIN = 12345678


class StubAccountInfo:
    """Shaped like the bindings' account named tuple."""

    def __init__(
        self,
        *,
        balance: float | None = 10000.0,
        equity: float | None = 10000.0,
        currency: str | None = "USD",
        login: int = LOGIN,
        name: str = "Alpari-Demo",
    ) -> None:
        self.balance = balance
        self.equity = equity
        self.currency = currency
        self.login = login
        self.name = name


class StubSymbolInfo:
    """Shaped like the bindings' symbol named tuple, field names and all."""

    def __init__(
        self,
        *,
        trade_contract_size: float | None = 100000.0,
        trade_tick_size: float | None = 0.00001,
        trade_tick_value_profit: float | None = 1.0,
        trade_tick_value_loss: float | None = 1.0,
        volume_min: float | None = 0.01,
        volume_max: float | None = 100.0,
        volume_step: float | None = 0.01,
        digits: int = 5,
        point: float | None = 0.00001,
        currency_base: str | None = "EUR",
        currency_profit: str | None = "USD",
        currency_margin: str | None = "EUR",
    ) -> None:
        self.trade_contract_size = trade_contract_size
        self.trade_tick_size = trade_tick_size
        self.trade_tick_value_profit = trade_tick_value_profit
        self.trade_tick_value_loss = trade_tick_value_loss
        self.volume_min = volume_min
        self.volume_max = volume_max
        self.volume_step = volume_step
        self.digits = digits
        self.point = point
        self.currency_base = currency_base
        self.currency_profit = currency_profit
        self.currency_margin = currency_margin


class StubBindings:
    """The four calls the adapter makes, and nothing else.

    A `MetaTrader5` module has hundreds of attributes. This one has four, on
    purpose: if the adapter ever reached for a fifth, the test would fail with an
    ``AttributeError`` rather than quietly passing against something the real
    module also happens to have.
    """

    def __init__(
        self,
        *,
        account: object | None = None,
        specs: dict[str, object] | None = None,
        init_ok: bool = True,
        account_error: Exception | None = None,
        symbol_error: Exception | None = None,
    ) -> None:
        self._account = account
        self._specs = specs or {}
        self._init_ok = init_ok
        self._account_error = account_error
        self._symbol_error = symbol_error
        self.initialised_with: str | None = None
        self.shutdown_calls = 0
        self.account_calls = 0
        self.symbol_calls: list[str] = []

    def initialize(self, path: str | None = None) -> bool:
        self.initialised_with = path
        return self._init_ok

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def account_info(self) -> object | None:
        self.account_calls += 1
        if self._account_error is not None:
            raise self._account_error
        return self._account

    def symbol_info(self, symbol: str) -> object | None:
        self.symbol_calls.append(symbol)
        if self._symbol_error is not None:
            raise self._symbol_error
        return self._specs.get(symbol.upper())


def _bindings() -> StubBindings:
    return StubBindings(
        account=StubAccountInfo(),
        specs={
            "EURUSD": StubSymbolInfo(),
            "XAUUSD": StubSymbolInfo(
                trade_contract_size=100.0,
                trade_tick_size=0.01,
                point=0.01,
                digits=2,
                volume_max=50.0,
                currency_base="USD",
                currency_profit="USD",
                currency_margin="USD",
            ),
        },
    )


class TestTheAdaptersSatisfyThePorts:
    def test_they_are_the_port_implementations_the_fakes_also_are(self) -> None:
        # The same check `test_risk_service.py` makes for the fakes. A real
        # adapter that did not satisfy the port would be wired into a pipeline
        # that expects something else, and the failure would be a missing method
        # at the moment an account is read.
        assert isinstance(MT5AccountProvider(_bindings()), AccountProvider)
        assert isinstance(MT5SymbolSpecProvider(_bindings()), SymbolSpecProvider)
        assert isinstance(FakeAccountProvider(), AccountProvider)
        assert isinstance(FakeSymbolSpecProvider(), SymbolSpecProvider)

    def test_an_injected_module_is_never_reinitialised(self) -> None:
        # `initialize` is a connection, and connecting twice is a bug: the second
        # call would reset the terminal's state under a live session. A caller
        # that hands over its own bindings has already connected.
        bindings = _bindings()
        provider = MT5AccountProvider(bindings)
        assert provider.balance().balance > 0
        assert bindings.initialised_with is None


class TestTheAccount:
    def test_the_balance_becomes_a_domain_account(self) -> None:
        balance = MT5AccountProvider(_bindings()).balance()
        assert isinstance(balance, AccountBalance)
        assert balance.balance == Decimal("10000")
        assert balance.currency == "USD"
        assert balance.equity == Decimal("10000")
        assert balance.account_login == 12345678
        assert balance.server == "Alpari-Demo"

    def test_a_float_balance_becomes_an_exact_decimal(self) -> None:
        # `Decimal(0.1)` is 0.1000000000000000055511151231257827, so a percentage
        # of it inherits that error. `Decimal(str(...))` is the fix, and this is
        # the test that says it was applied.
        balance = MT5AccountProvider(
            StubBindings(account=StubAccountInfo(balance=1234.56))
        ).balance()
        assert balance.balance == Decimal("1234.56")
        assert str(balance.balance) == "1234.56"

    def test_the_currency_is_normalised(self) -> None:
        # Every currency comparison in the domain is case-insensitive, so
        # normalising once here beats relying on each comparison to remember.
        balance = MT5AccountProvider(
            StubBindings(account=StubAccountInfo(currency=" usd "))
        ).balance()
        assert balance.currency == "USD"

    def test_a_missing_account_raises_rather_than_defaulting(self) -> None:
        # The single most likely cause in production is a terminal that is running
        # but not logged in, and the two ways to get this wrong — returning a zero
        # balance, or returning a remembered one — are both catastrophic.
        provider = MT5AccountProvider(StubBindings(account=None))
        with pytest.raises(MT5Unavailable, match="no account"):
            provider.balance()

    def test_the_message_names_the_likely_cause(self) -> None:
        provider = MT5AccountProvider(StubBindings(account=None))
        with pytest.raises(MT5Unavailable, match="not logged in"):
            provider.balance()

    def test_an_exception_from_the_bindings_becomes_a_named_fault(self) -> None:
        provider = MT5AccountProvider(StubBindings(account_error=RuntimeError("connection reset")))
        with pytest.raises(MT5Unavailable, match="connection reset"):
            provider.balance()

    def test_a_missing_equity_raises_rather_than_becoming_zero(self) -> None:
        # `AccountBalance` accepts a `None` equity, so zero would be *accepted*
        # and would read as "the account is worth nothing", which is a different
        # claim from "the terminal did not say".
        provider = MT5AccountProvider(StubBindings(account=StubAccountInfo(equity=None)))
        with pytest.raises(MT5Unavailable, match="equity"):
            provider.balance()

    def test_a_missing_currency_raises(self) -> None:
        provider = MT5AccountProvider(StubBindings(account=StubAccountInfo(currency="  ")))
        with pytest.raises(MT5Unavailable, match="currency"):
            provider.balance()

    def test_a_negative_balance_is_passed_to_the_model_to_refuse(self) -> None:
        # Not clamped to zero, and not swallowed. A negative balance is not an
        # account, and the model saying so is more useful than any number here.
        provider = MT5AccountProvider(StubBindings(account=StubAccountInfo(balance=-5.0)))
        with pytest.raises(ValueError, match="balance must be positive"):
            provider.balance()

    def test_the_open_position_count_is_zero_and_that_is_a_known_gap(self) -> None:
        # `account_info()` does not carry a position count, so zero is reported.
        # It is wrong when positions are open, and the consequence is that a
        # configured concurrency gate sees zero and admits a trade.
        #
        # This test exists to make the gap *visible and named* rather than to
        # endorse it. The correct fix is `positions_get()` in this class, and it
        # must land before any configuration sets BRIDGE_MAX_OPEN_POSITIONS.
        assert MT5AccountProvider(_bindings()).balance().open_positions == 0

    def test_it_does_not_launch_the_terminal(self) -> None:
        # A `StubBindings` has no `launch` attribute at all, so an adapter that
        # tried to launch would fail with an AttributeError here. The assertion is
        # that it does not.
        assert not hasattr(_bindings(), "launch")
        assert MT5AccountProvider(_bindings()).balance().balance > 0


class TestTheSymbolSpecification:
    def test_the_fields_map_one_for_one(self) -> None:
        spec = MT5SymbolSpecProvider(_bindings()).spec("EURUSD")
        assert isinstance(spec, SymbolSpec)
        assert spec.symbol_normalised == "EURUSD"
        assert spec.contract_size == Decimal("100000")
        assert spec.tick_size == Decimal("0.00001")
        assert spec.tick_value_profit == Decimal("1.0")
        assert spec.tick_value_loss == Decimal("1.0")
        assert spec.volume_min == Decimal("0.01")
        assert spec.volume_max == Decimal("100.0")
        assert spec.volume_step == Decimal("0.01")
        assert spec.digits == 5
        assert spec.point == Decimal("0.00001")

    def test_the_currencies_travel_with_the_specification(self) -> None:
        # Phase 4's currency-coherence check refuses an unstated profit currency,
        # and it can only be answered if the adapter read one.
        spec = MT5SymbolSpecProvider(_bindings()).spec("EURUSD")
        assert spec.currency == "EUR"
        assert spec.currency_profit == "USD"
        assert spec.currency_margin == "EUR"

    def test_a_small_tick_size_survives_the_float_conversion(self) -> None:
        # The test that justifies `Decimal(str(value))`. `Decimal(0.00001)` is
        # 1.0000000000000000818...E-5, so a naive conversion produces a tick size
        # that is wrong in the ninth significant figure and a tick count to match.
        spec = MT5SymbolSpecProvider(_bindings()).spec("EURUSD")
        assert str(spec.tick_size) == "0.00001"
        assert spec.risk_per_unit(Decimal("0.00300")) == Decimal("300")

    def test_an_unknown_symbol_raises_rather_than_defaulting(self) -> None:
        # A default specification is an invented contract, and inventing one is
        # the one thing this project refuses to do. The fake provider refuses the
        # same way, and both are pinned.
        with pytest.raises(MT5Unavailable, match="no specification"):
            MT5SymbolSpecProvider(_bindings()).spec("NOSUCH")

    def test_the_message_names_all_three_causes(self) -> None:
        # "Unknown symbol" when the terminal is actually dead sends an operator
        # to Market Watch instead of to the process.
        with pytest.raises(MT5Unavailable) as caught:
            MT5SymbolSpecProvider(_bindings()).spec("NOSUCH")
        message = str(caught.value)
        assert "name is wrong" in message
        assert "not loaded" in message
        assert "not running" in message

    def test_the_lookup_is_case_and_whitespace_insensitive(self) -> None:
        # MT5 is case-insensitive, so a signal naming `  eurusd ` has to work. A
        # stricter adapter would pass here and fail in production.
        assert MT5SymbolSpecProvider(_bindings()).spec("  eurusd ").symbol_normalised == "EURUSD"

    def test_a_missing_field_raises_and_names_the_field(self) -> None:
        # Not a zero. A zero tick size would be caught downstream as a *sizing*
        # refusal, which points an operator at the arithmetic rather than at the
        # terminal that failed to answer.
        for field in (
            "trade_tick_size",
            "trade_tick_value_profit",
            "trade_contract_size",
            "volume_step",
        ):
            info = StubSymbolInfo()
            setattr(info, field, None)
            provider = MT5SymbolSpecProvider(StubBindings(specs={"EURUSD": info}))
            with pytest.raises(MT5Unavailable, match=field):
                provider.spec("EURUSD")

    def test_an_empty_profit_currency_is_passed_through_not_raised(self) -> None:
        # The deliberate asymmetry: an empty profit currency is a fact the domain
        # already handles by refusing the trade, so the adapter reports it and
        # stops. Refusing here too would be right for the wrong reason, and would
        # move a policy decision into a converter.
        info = StubSymbolInfo(currency_profit="")
        spec = MT5SymbolSpecProvider(StubBindings(specs={"EURUSD": info})).spec("EURUSD")
        assert spec.currency_profit == ""

    def test_an_empty_symbol_raises_before_any_call(self) -> None:
        bindings = _bindings()
        with pytest.raises(MT5Unavailable, match="symbol is required"):
            MT5SymbolSpecProvider(bindings).spec("   ")
        assert bindings.symbol_calls == []

    def test_an_exception_from_the_bindings_becomes_a_named_fault(self) -> None:
        provider = MT5SymbolSpecProvider(StubBindings(symbol_error=OSError("pipe closed")))
        with pytest.raises(MT5Unavailable, match="pipe closed"):
            provider.spec("EURUSD")

    def test_gold_maps_to_its_own_contract(self) -> None:
        # Not a special case in the code — the fields are just read. The test
        # exists so the difference is *visible* to a reader, because it is the
        # difference the tick-value sizer exists to handle.
        spec = MT5SymbolSpecProvider(_bindings()).spec("XAUUSD")
        assert spec.risk_per_unit(Decimal("3.00")) == Decimal("300")
        assert spec.symbol_normalised == "XAUUSD"


class TestAgainstTheFakes:
    def test_both_providers_produce_an_equivalent_account(self) -> None:
        # The point of the fakes existing: the real adapter is not a different
        # shape of thing. If the two ever disagree about `AccountBalance`, the
        # pipeline has been tested against one and run against the other.
        real = MT5AccountProvider(_bindings()).balance()
        fake = FakeAccountProvider(
            AccountBalance(balance=Decimal("10000"), currency="USD", equity=Decimal("10000"))
        ).balance()
        assert real.balance == fake.balance
        assert real.currency == fake.currency

    def test_both_providers_refuse_an_unknown_symbol(self) -> None:
        # Both raise, and neither invents a specification. The error types differ
        # -- the real one names the terminal, the fake names the registry -- so
        # the assertion is on the behaviour rather than on a shared class.
        with pytest.raises(MT5Unavailable, match="no specification"):
            MT5SymbolSpecProvider(_bindings()).spec("NOSUCH")
        with pytest.raises(IntegrationError, match="no specification is registered"):
            FakeSymbolSpecProvider().spec("NOSUCH")

    def test_both_providers_count_their_reads(self) -> None:
        # A test asserting on a fake's read count is a test the real adapter must
        # also pass, which is why the counter exists on both.
        bindings = _bindings()
        MT5AccountProvider(bindings).balance()
        assert bindings.account_calls == 1
        MT5SymbolSpecProvider(bindings).spec("EURUSD")
        assert bindings.symbol_calls == ["EURUSD"]


class TestInsideTheRealPipeline:
    """The adapter, wired into the pipeline it exists to serve.

    Everything above tests the adapter against doubles. This tests the thing that
    actually matters: that a `ProcessSignal` given the *real* providers — talking
    to a stubbed terminal — produces the same decision it would produce with the
    fakes, from facts that arrived as floats and became decimals.

    It is the test that would have caught a wrong field name, had the doubles been
    built from the adapter rather than from the bindings. It cannot catch a wrong
    field name in the bindings themselves, and that limit is stated rather than
    glossed: it is what Phase 11 exists for.
    """

    def _pipeline(self) -> object:
        from signal_to_trade_bridge.application.process_signal import ProcessSignal
        from signal_to_trade_bridge.application.risk_service import RiskService
        from signal_to_trade_bridge.configuration.config import BridgeConfig
        from signal_to_trade_bridge.domain.enums import Direction, SignalAction
        from signal_to_trade_bridge.domain.models import Signal

        bindings = _bindings()
        service = RiskService(MT5AccountProvider(bindings), MT5SymbolSpecProvider(bindings))
        signal = Signal(
            signal_id="stb-test-mt5",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
            take_profit=Decimal("1.10300"),
            stop_basis="PULLBACK_EXTREME",
            take_profit_basis="SWING",
        )
        return ProcessSignal(service, BridgeConfig()), signal

    def test_a_terminal_sourced_balance_sizes_the_trade(self) -> None:
        pipeline, signal = self._pipeline()
        decision = pipeline.process(signal)  # type: ignore[attr-defined]
        assert decision.is_dry_run
        assert decision.intent is not None
        # $50 of a $10,000 balance over a 300-tick stop, from numbers that arrived
        # as floats and were converted exactly on the way in.
        assert decision.intent.risk_amount == Decimal("50")
        assert decision.intent.volume == Decimal("0.16")
        assert decision.intent.symbol_spec.tick_size == Decimal("0.00001")

    def test_a_dead_terminal_refuses_the_trade_at_the_policy_stage(self) -> None:
        # The pipeline's fail-closed path with real providers: a concurrency limit
        # is configured, the terminal does not answer, and the gate is refused
        # rather than skipped. The same behaviour the fake providers produce.
        from signal_to_trade_bridge.application.process_signal import ProcessSignal
        from signal_to_trade_bridge.application.risk_service import RiskService
        from signal_to_trade_bridge.configuration.config import BridgeConfig
        from signal_to_trade_bridge.domain.enums import Direction, SignalAction
        from signal_to_trade_bridge.domain.models import RiskParameters, Signal

        class Dead(StubBindings):
            def account_info(self) -> object | None:
                return None

        bindings = Dead()
        service = RiskService(MT5AccountProvider(bindings), MT5SymbolSpecProvider(bindings))
        config = BridgeConfig(risk=RiskParameters(max_open_positions=3))
        pipeline = ProcessSignal(service, config)
        signal = Signal(
            signal_id="stb-test-mt5-dead",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
            stop_basis="PULLBACK_EXTREME",
        )
        decision = pipeline.process(signal)
        assert decision.diagnostics["stage"] == "policy"
        assert decision.reason == "ACCOUNT_BALANCE_UNAVAILABLE"


class TestTheOpenPositionCount:
    """The gap Phase 7 opened, now narrowed.

    ``account_info()`` has no position count, so this used to be hard-coded zero —
    which is a lie whenever a position is open, and the consequence lands on the
    concurrency gate: it saw zero, admitted the trade, and the limit did nothing.

    The count now comes from the indicator's snapshot when a reader is injected.
    These tests pin both halves of that, including the half that is still open.
    """

    class _FixedReader:
        """A reader stub exposing only what the provider uses: ``read()``.

        A snapshot stand-in rather than a real one, because these tests are about
        the *wiring* — how many positions reach the account balance — and the real
        parsing is pinned in ``test_mt5_position_reader.py``.
        """

        def __init__(self, count: int, account: int | None = None) -> None:
            self._count = count
            self._account = account
            self.calls = 0

        def read(self) -> Any:
            self.calls += 1
            return SimpleNamespace(count=self._count, account=self._account)

    def test_without_a_reader_the_count_is_zero(self) -> None:
        # Still the default, and still a lie when positions are open. A caller
        # that does not pass a reader has accepted that.
        assert MT5AccountProvider(_bindings()).balance().open_positions == 0

    def test_a_reader_supplies_the_real_count(self) -> None:
        reader = self._FixedReader(3, account=LOGIN)
        balance = MT5AccountProvider(_bindings(), position_reader=reader).balance()  # type: ignore[arg-type]
        assert balance.open_positions == 3
        assert reader.calls == 1

    def test_a_reader_that_cannot_answer_falls_back_to_zero(self) -> None:
        # The remaining gap, and the reason `BRIDGE_MAX_OPEN_POSITIONS` must stay
        # unset. Reporting "not known" as zero is the same lie in a narrower
        # window -- narrower only because the reader is fail-closed on staleness.
        from signal_to_trade_bridge.adapters.mt5 import MT5Unavailable

        class Broken:
            def read(self) -> Any:
                raise MT5Unavailable("snapshot is stale")

        balance = MT5AccountProvider(_bindings(), position_reader=Broken()).balance()  # type: ignore[arg-type]
        assert balance.open_positions == 0

    def test_a_snapshot_for_another_account_is_refused(self) -> None:
        # The one reader outcome that is NOT treated as "not known".
        #
        # An unreadable file means the count is unknown, and zero is a defensible
        # (if imperfect) answer to "unknown". A readable file naming a *different*
        # login means something else entirely: this count belongs to someone else.
        # Adopting it would report another account's flat book as this account's,
        # which is a zero on the gate whenever that other account is flat -- and a
        # zero that looks computed is far more convincing than one that looks like
        # a fallback.
        reader = self._FixedReader(0, account=LOGIN + 1)
        with pytest.raises(MT5Unavailable, match="was written for account"):
            MT5AccountProvider(_bindings(), position_reader=reader).balance()  # type: ignore[arg-type]

    def test_a_matching_account_is_accepted(self) -> None:
        reader = self._FixedReader(2, account=LOGIN)
        balance = MT5AccountProvider(_bindings(), position_reader=reader).balance()  # type: ignore[arg-type]
        assert balance.open_positions == 2

    def test_a_snapshot_with_no_account_number_is_not_compared(self) -> None:
        # `account` is optional in the snapshot, so an absent one cannot mismatch.
        # Treating "unstated" as "matches" is the right default here precisely
        # because the alternative -- refusing every snapshot that omits it -- would
        # refuse on a field the indicator may legitimately leave out.
        reader = self._FixedReader(4, account=None)
        balance = MT5AccountProvider(_bindings(), position_reader=reader).balance()  # type: ignore[arg-type]
        assert balance.open_positions == 4

    def test_the_real_reader_can_be_wired_in(self, tmp_path: Path) -> None:
        # The end-to-end shape: an account provider reading a real snapshot from
        # a directory, with no terminal anywhere. The reader's own tests cover the
        # parsing; this proves the wiring is possible at all.
        from signal_to_trade_bridge.adapters.mt5.positions import MT5PositionReader

        files = tmp_path / "MQL5" / "Files"
        files.mkdir(parents=True)
        (files / "auto_trade_positions_a.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "sequence": 5,
                    "complete": True,
                    "written_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "account": LOGIN,
                    "server": "Alpari-MT5-Demo",
                    "terminal_build": 6230,
                    "positions": [
                        {
                            "ticket": 1,
                            "symbol": "EURUSD",
                            "type": "BUY",
                            "volume": 0.01,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        reader = MT5PositionReader(tmp_path)
        balance = MT5AccountProvider(_bindings(), position_reader=reader).balance()
        assert balance.open_positions == 1

    def test_the_real_reader_refuses_a_snapshot_from_another_account(self, tmp_path: Path) -> None:
        # The cross-check through the real reader, not just the stub: the field is
        # parsed as the type the terminal writes, and the comparison happens on it.
        from signal_to_trade_bridge.adapters.mt5.positions import MT5PositionReader

        files = tmp_path / "MQL5" / "Files"
        files.mkdir(parents=True)
        (files / "auto_trade_positions_a.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "sequence": 5,
                    "complete": True,
                    "written_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "account": "98765432",
                    "server": "Alpari-MT5-Demo",
                    "terminal_build": 6230,
                    "positions": [],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(MT5Unavailable, match="98765432"):
            MT5AccountProvider(_bindings(), position_reader=MT5PositionReader(tmp_path)).balance()


class TestShutdown:
    def test_shutdown_releases_the_connection(self) -> None:
        bindings = _bindings()
        MT5AccountProvider(bindings).shutdown()
        assert bindings.shutdown_calls == 1
        MT5SymbolSpecProvider(bindings).shutdown()
        assert bindings.shutdown_calls == 2

    def test_a_failing_shutdown_does_not_raise(self) -> None:
        # A cleanup path must not be the thing that breaks a caller. A failure to
        # disconnect is not a trading fact.
        class Broken(StubBindings):
            def shutdown(self) -> None:
                raise OSError("already closed")

        MT5AccountProvider(Broken()).shutdown()  # does not raise


class TestTheBindingsBoundary:
    def test_meta_trader5_is_imported_in_exactly_one_place(self) -> None:
        """One import site, or the boundary is not a boundary.

        The claim the package makes is that `MetaTrader5` is reachable through one
        lazily-called function, so that every other module imports on a machine
        that has never installed the bindings — which is what keeps the suite
        runnable without MetaTrader 5. Asserted structurally, because a stray
        module-level import would pass every behavioural test here and break the
        ones in files that do not inject the module.
        """
        tree = ast.parse(inspect.getsource(bindings_module))
        sites: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                sites.extend(
                    node.lineno for alias in node.names if alias.name.split(".")[0] == "MetaTrader5"
                )
            elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == (
                "MetaTrader5"
            ):
                sites.append(node.lineno)
        assert len(sites) == 1, (
            f"bindings.py imports MetaTrader5 at lines {sites}. It must be reachable through "
            f"one lazy import so every other module imports without the bindings installed."
        )

    def test_the_import_is_inside_a_function(self) -> None:
        # A module-level import would break `import signal_to_trade_bridge` on a
        # machine with no MetaTrader 5, which is the machine most contributors
        # and every CI runner are on.
        tree = ast.parse(inspect.getsource(bindings_module))
        module_level = [
            node
            for node in tree.body
            if isinstance(node, ast.Import)
            and any(alias.name.split(".")[0] == "MetaTrader5" for alias in node.names)
        ]
        assert not module_level, "the MetaTrader5 import must not be at module level"

    def test_nothing_launches_a_terminal(self) -> None:
        # `initialize` connects; `launch` starts a process. The bridge must never
        # do the second -- a process that launches a trading terminal is a process
        # that can launch it by accident, and one that launches it without a login
        # is worse. Asserted on the AST rather than the text, because the word
        # appears in the docstring that explains the rule.
        tree = ast.parse(inspect.getsource(bindings_module))
        launched = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "launch"
        ]
        assert not launched, (
            f"bindings.py calls `launch` at line {launched[0]}. The bridge connects to a "
            f"running terminal; it never starts one."
        )

    def test_the_error_names_the_package_and_the_install_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # "No module named MetaTrader5" tells an operator nothing about what to do.
        #
        # Reached with a *hidden* module rather than by relying on the package being
        # absent. Phase 11 installed `MetaTrader5` on this machine -- correctly, since
        # the live tests need it -- and that turned this test into a test of whichever
        # failure happened to come first. A test whose subject is "the package is not
        # installed" has to be able to arrange that itself, on any machine.
        import builtins

        real_import = builtins.__import__

        def hide(name: str, *args: object, **kwargs: object) -> object:
            if name == "MetaTrader5":
                raise ImportError("No module named 'MetaTrader5'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", hide)
        with pytest.raises(MT5Unavailable) as caught:
            bindings_module.load_bindings()
        message = str(caught.value)
        assert "MetaTrader5" in message
        assert "pip install" in message

    def test_a_failed_initialisation_says_the_terminal_was_not_reachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reached with a stub installed in place of the module, because the real
        # one is not installed on the machine this was written on -- which is
        # exactly the situation the injection pattern exists to make testable.
        stub = StubBindings(init_ok=False)
        monkeypatch.setitem(sys.modules, "MetaTrader5", stub)
        with pytest.raises(MT5Unavailable) as caught:
            bindings_module.load_bindings(Path("C:/nonexistent/terminal64.exe"))
        assert "not reachable" in str(caught.value)
        # The path is echoed, so a wrong `BRIDGE_MT5_TERMINAL_PATH` is visible in
        # the message rather than being silently ignored.
        assert "nonexistent" in str(caught.value)
        assert stub.initialised_with is not None

    def test_an_initialisation_that_raises_is_wrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Exploding(StubBindings):
            def initialize(self, path: str | None = None) -> bool:
                raise OSError("terminal not found")

        monkeypatch.setitem(sys.modules, "MetaTrader5", Exploding())
        with pytest.raises(MT5Unavailable, match="terminal not found"):
            bindings_module.load_bindings()

    def test_a_successful_load_returns_the_bindings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `load_bindings` is the one function that reaches the real module, so it
        # is the one place where the claim "this module satisfies the Protocol"
        # can be checked without the package installed: a stub is installed in its
        # place and asserted against the Protocol.
        stub = _bindings()
        monkeypatch.setitem(sys.modules, "MetaTrader5", stub)
        loaded = bindings_module.load_bindings(Path("C:/terminals/terminal64.exe"))
        assert isinstance(loaded, bindings_module.MT5Bindings)
        assert loaded is stub
        assert stub.initialised_with is not None
        assert "terminal64.exe" in stub.initialised_with

    def test_the_protocols_are_runtime_checkable(self) -> None:
        # `runtime_checkable` is what makes `isinstance` above mean something. A
        # Protocol that cannot be checked is a comment.
        assert isinstance(_bindings(), bindings_module.MT5Bindings)
        assert isinstance(StubAccountInfo(), bindings_module.MT5AccountInfo)
        assert isinstance(StubSymbolInfo(), bindings_module.MT5SymbolInfo)

    def test_an_object_missing_a_declared_call_is_not_a_binding(self) -> None:
        class AlmostBindings:
            def initialize(self, path: str | None = None) -> bool:
                return True

        # `runtime_checkable` checks method *names*, so this is the boundary of
        # what it can tell us — and the reason the adapter's own code must not
        # rely on `isinstance` for more than that.
        assert not isinstance(AlmostBindings(), bindings_module.MT5Bindings)
