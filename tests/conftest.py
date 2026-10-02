"""Shared test fixtures.

Everything here is deterministic. No test in this suite may depend on the current
time, on a real MetaTrader terminal, on a network, or on an environment variable
that was not set by the test itself -- with the single exception of the
configuration tests, which manage the environment explicitly through the
``clean_environment`` fixture and restore it afterwards.

The reason is not tidiness. A test that passes on the author's machine and fails on
a colleague's is worse than no test, because it teaches people to retry instead of
to investigate.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from signal_to_trade_bridge.domain.enums import (
    Direction,
    SignalAction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
)

#: The repository root, derived from this file's location.
#:
#: A module-level constant rather than a fixture, because `test_domain_isolation`
#: needs it while *building* its parametrised test list -- which happens at
#: collection time, before any fixture can be resolved. Computing it from
#: `__file__` rather than hard-coding `E:\` is what lets the suite run on a
#: laptop where the checkout is somewhere else entirely; a hard-coded path here
#: would make every other machine fail for a reason that has nothing to do with
#: the code.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project_root() -> Path:
    """The repository root, for a test that wants it as a fixture.

    A fixture rather than a bare constant because most tests that need the path
    want it injected, and only `test_domain_isolation` needs it as a constant --
    that one builds its parametrised test list at collection time, before any
    fixture can be resolved, so it imports `PROJECT_ROOT` directly.
    """
    return PROJECT_ROOT


@pytest.fixture(autouse=True)
def _no_configuration_leaks() -> Iterator[None]:
    """Guarantee that no test's configuration reaches the next test.

    **The leak was found on the day this machine armed itself**, and it is worth
    stating plainly because it is the most dangerous class of bug in this suite: a
    test that reads the configuration seeds `os.environ` from the `.env` it finds
    (`config_from_env` defaults to `apply=True`, which is the documented behaviour
    and a genuine side effect). Tests that then assert the *shipped defaults* fail --
    correctly, and for a reason three files away from the cause.

    An autouse fixture rather than remembering to ask for `clean_environment` in each
    test, because "remember to" is exactly what failed. It snapshots at the start of
    **every** test, so the restore below is by definition the correct baseline, and
    it also removes the class of bug rather than the two instances of it.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def clean_environment() -> Iterator[None]:
    """Restore ``os.environ`` after a test that modifies it.

    Now redundant with the autouse fixture above, and **kept** because it is named in
    a great many test signatures and because reading it tells you the test intends to
    care about the environment. Removing it would be a large, mechanical diff for no
    gain; the autouse fixture is the guarantee, this is the declaration of intent.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def risk_parameters() -> RiskParameters:
    """The brief's starting configuration: 0.5% risk at 1:1."""
    return RiskParameters(
        risk_percent=Decimal("0.5"),
        reward_risk_ratio=Decimal("1.0"),
        take_profit_source=TakeProfitSource.RR_FALLBACK,
    )


@pytest.fixture
def account_balance() -> AccountBalance:
    """A $10,000 account, so 0.5% is a round $50."""
    return AccountBalance(
        balance=Decimal("10000"),
        currency="USD",
        equity=Decimal("10000"),
        open_positions=0,
        account_login=12345678,
        server="Alpari-Demo",
    )


@pytest.fixture
def forex_spec() -> SymbolSpec:
    """A 5-digit EURUSD-style specification.

    ``tick_size`` 0.00001 and ``tick_value`` $1 per lot per tick, so a 0.00300
    stop is 300 ticks and risks $300 per lot. Those are round numbers on purpose:
    a test that asserts ``0.37`` lots is readable, where one asserting
    ``0.3699823...`` is not, and a test nobody can check by hand is a test nobody
    will notice failing for the right reason.
    """
    return SymbolSpec(
        symbol="EURUSD",
        contract_size=Decimal("100000"),
        tick_size=Decimal("0.00001"),
        tick_value_profit=Decimal("1.0"),
        tick_value_loss=Decimal("1.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100.0"),
        volume_step=Decimal("0.01"),
        digits=5,
        point=Decimal("0.00001"),
        currency="USD",
        currency_profit="USD",
        currency_margin="EUR",
    )


@pytest.fixture
def gold_spec() -> SymbolSpec:
    """A gold specification, which is the case a Forex-shaped sizer gets wrong.

    One lot of XAUUSD is 100 ounces, one tick is $0.01, and one tick is $1.00. So a
    $3.00 stop is 300 ticks and risks $300 per lot -- the same number as the forex
    case above, arrived at by completely different arithmetic. A formula that
    assumed a 5-digit pair and a 100000 contract size would be wrong here by two
    orders of magnitude.
    """
    return SymbolSpec(
        symbol="XAUUSD",
        contract_size=Decimal("100"),
        tick_size=Decimal("0.01"),
        tick_value_profit=Decimal("1.0"),
        tick_value_loss=Decimal("1.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50.0"),
        volume_step=Decimal("0.01"),
        digits=2,
        point=Decimal("0.01"),
        currency="USD",
        currency_profit="USD",
        currency_margin="USD",
    )


@pytest.fixture
def buy_signal() -> Signal:
    """A well-formed BUY signal with a structural stop and a target."""
    return Signal(
        signal_id="stb-test-0001",
        symbol="EURUSD",
        timeframe="H1",
        action=SignalAction.BUY,
        direction=Direction.LONG,
        entry=Decimal("1.10000"),
        stop_loss=Decimal("1.09700"),
        take_profit=Decimal("1.10300"),
        stop_basis="PULLBACK_EXTREME",
        take_profit_basis="SWING",
        evidence_score=0.72,
        setup_id="pullback_h#0",
        bar_index=299,
        bar_time=1727740800.0,
        source="albrooks",
        source_metadata={"reason": "RANKED_CANDIDATE"},
    )


@pytest.fixture
def sell_signal() -> Signal:
    """A well-formed SELL signal, mirroring :func:`buy_signal`."""
    return Signal(
        signal_id="stb-test-0002",
        symbol="EURUSD",
        timeframe="H1",
        action=SignalAction.SELL,
        direction=Direction.SHORT,
        entry=Decimal("1.10000"),
        stop_loss=Decimal("1.10300"),
        take_profit=Decimal("1.09700"),
        stop_basis="SWING",
        take_profit_basis="MEASURED_MOVE",
        evidence_score=0.65,
        setup_id="breakout_pullback#1",
        bar_index=299,
        bar_time=1727740800.0,
        source="albrooks",
    )


@pytest.fixture
def long_stop() -> StopLoss:
    return StopLoss(
        price=Decimal("1.09700"),
        distance=Decimal("0.00300"),
        source=StopSource.SIGNAL,
        basis="PULLBACK_EXTREME",
    )


@pytest.fixture
def long_take_profit() -> TakeProfit:
    return TakeProfit(
        price=Decimal("1.10300"),
        distance=Decimal("0.00300"),
        source=TakeProfitSource.RR_FALLBACK,
        basis="",
    )


# --- a double for the terminal, because no test may need a real one ---------


class _StubAccountInfo:
    """MT5's ``account_info()`` shape, as plain attributes.

    A double rather than a fixture of the bridge's own ``AccountBalance``, because
    the adapter is the thing under test at this seam: a stub already in domain shape
    would pass while the mapping from MT5's field names went untested.
    """

    def __init__(self) -> None:
        self.balance = 10000.0
        self.equity = 10000.0
        self.currency = "USD"
        self.login = 53184454
        self.name = "Alpari-MT5-Demo"


class _StubSymbolInfo:
    """MT5's own field names, not the bridge's ``SymbolSpec`` field names.

    The adapter maps ``trade_contract_size`` to ``contract_size`` one for one, so a
    stub carrying the domain names would test a conversion that does not happen --
    and would hide a wiring mistake behind a stub that already agreed with us.
    """

    def __init__(self, symbol: str = "EURUSD") -> None:
        self.name = symbol
        self.trade_contract_size = 100000.0
        self.trade_tick_size = 0.00001
        self.trade_tick_value_profit = 1.0
        self.trade_tick_value_loss = 1.0
        self.volume_min = 0.01
        self.volume_max = 100.0
        self.volume_step = 0.01
        self.digits = 5
        self.point = 0.00001
        self.currency_base = "EUR"
        self.currency_profit = "USD"
        self.currency_margin = "EUR"


class StubBindings:
    """The MT5 bindings surface, with a EURUSD specification attached.

    Lives here rather than in one test module because three of them now need it, and
    a copy per module is three places for a wrong field name to hide. It is a class
    rather than a fixture because tests construct it with different symbol sets.
    """

    def __init__(self, symbols: set[str] | None = None) -> None:
        self._symbols = {"EURUSD"} if symbols is None else symbols

    def account_info(self) -> _StubAccountInfo:
        return _StubAccountInfo()

    def symbol_info(self, name: str) -> object:
        key = (name or "").strip().upper()
        if key not in self._symbols:
            return None
        return _StubSymbolInfo(key)

    def shutdown(self) -> None:
        return None


@pytest.fixture
def bridge_factory(tmp_path: Path):
    """Build a real ``Bridge`` wired to a stub terminal.

    The real composition root, not a hand-assembled pipeline: a test of the CLI's
    reporting wants the decision a *real* bridge produces, and a hand-built one would
    be exactly the stub that hides the defect the test exists to find.

    ``build_bridge`` without ``mt5_bindings`` refuses on a machine with no terminal,
    which is the correct production behaviour and makes it useless as a default for
    tests. Hence this factory, which supplies the one thing a test must not depend
    on -- an open terminal -- and nothing else.
    """
    from signal_to_trade_bridge.composition import BridgeConfig, build_bridge

    def _factory(**overrides: object):
        from dataclasses import replace

        config = replace(BridgeConfig(), log_directory=tmp_path / "logs", **overrides)
        return build_bridge(config, mt5_bindings=StubBindings())  # type: ignore[arg-type]

    return _factory
