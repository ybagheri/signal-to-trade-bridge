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


@pytest.fixture
def clean_environment() -> Iterator[None]:
    """Restore ``os.environ`` after a test that modifies it.

    Snapshot and restore rather than clearing, because a test that needs a clean
    slate should not silently remove a developer's real settings for the rest of
    the session. Restoring exactly what was there is the only version of this
    that is safe to run in a shell somebody is also using.
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
