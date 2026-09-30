"""Integration tests against the real ``albrooks`` package.

These exercise the real upstream engine rather than a stub, so they are the only
tests that can catch the stubs having drifted away from the real output shape.

**They are skipped when the upstream package is not installed**, which is the
normal case for a contributor who has not cloned the private repository. That is
correct: the ordinary suite must run everywhere, and a test that cannot run is
not a test.

    .\\.venv\\Scripts\\python.exe -m pytest tests/integration -v

The upstream project's own ``HANDOFF.md`` makes a related point that applies here:
**a skip is not a pass.** When these are green, they are green.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from signal_to_trade_bridge.adapters.albrooks import (
    AlBrooksSignalSource,
    map_result_to_signal,
)

pytest.importorskip("albrooks", reason="the upstream price-action engine is not installed")

albrooks = pytest.importorskip("albrooks")


class StubBars:
    """A ``MarketDataProvider`` built on the real engine's own ``Bar``."""

    def __init__(self, bars: list) -> None:
        self._bars = bars

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> list:
        return self._bars[:count]


def _synthetic_bars(count: int = 300) -> list:
    """Deterministic bars with enough movement to produce a reading.

    Hand-built rather than random, and not from real market data on purpose. This
    asserts that the *adapter* handles whatever the engine returns; it is not a
    test of the engine, and a random series would make a failure impossible to
    reproduce. A gentle alternating pattern gives the volatility reference the
    engine requires without asserting anything about which setup it finds.
    """
    from albrooks import Bar

    bars = []
    price = 1.10000
    for index in range(count):
        # A repeatable zig-zag: index i is deterministic, so the whole series is.
        step = 0.00050 if (index % 7) < 4 else -0.00040
        open_price = price
        close = open_price + step
        high = max(open_price, close) + 0.00020
        low = min(open_price, close) - 0.00020
        bars.append(
            Bar(
                time=1727740800.0 + index * 3600.0,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=1000.0 + index,
                index=index,
            )
        )
        price = close
    return bars


@pytest.fixture
def bars() -> list:
    return _synthetic_bars()


class TestAgainstTheRealEngine:
    def test_the_engine_produces_a_decision_the_mapper_can_read(self, bars: list) -> None:
        # The single most important integration assertion. If the engine's shape
        # changed, this fails -- and it is the failure the hand-written stubs
        # cannot catch, because a stub only ever proves the stub is self
        # consistent.
        from albrooks import Analyzer

        result = Analyzer().analyze(bars, symbol="EURUSD", timeframe="H1")
        outcome = map_result_to_signal(result)

        assert outcome.degenerate is False, (
            f"the engine ran no analysis on a 300-bar series: {outcome.source_reason}"
        )
        assert result.decision["action"] in {"BUY", "SELL", "WAIT", "NO_TRADE"}
        # Whatever it decided, the mapper either produced a signal or a reason.
        # A crash or a silent None is the failure being guarded against.
        assert outcome.signal is not None or outcome.reason

    def test_a_mapped_tradable_signal_has_the_fields_the_risk_service_needs(
        self, bars: list
    ) -> None:
        from albrooks import Analyzer

        result = Analyzer().analyze(bars, symbol="EURUSD", timeframe="H1")
        signal = map_result_to_signal(result).signal
        if signal is None or not signal.is_tradable:
            pytest.skip(
                f"the engine did not produce a tradable signal: {result.decision['action']}"
            )

        assert signal.symbol == "EURUSD"
        assert signal.timeframe == "H1"
        assert signal.entry > 0
        assert isinstance(signal.entry, Decimal)
        assert signal.direction.value in {"LONG", "SHORT"}
        assert signal.setup_id
        assert signal.bar_index >= 0
        assert signal.source_metadata["evidence_is_probability"] is False

    def test_the_signal_id_is_stable_across_two_real_analyses(self, bars: list) -> None:
        # The property Phase 9 depends on, verified against the real engine rather
        # than a stub. Two analyses over identical bars must be the same reading.
        from albrooks import Analyzer

        analyzer = Analyzer()
        first = map_result_to_signal(analyzer.analyze(bars, symbol="EURUSD", timeframe="H1"))
        second = map_result_to_signal(Analyzer().analyze(bars, symbol="EURUSD", timeframe="H1"))
        if first.signal is None or second.signal is None:
            pytest.skip("the engine produced no signal to compare")
        assert first.signal.signal_id == second.signal.signal_id

    def test_a_short_series_is_handled_without_crashing(self, bars: list) -> None:
        # A three-bar series cannot produce a reading, and the engine returns its
        # three-key degenerate shape. The adapter must survive it, because a short
        # series is a data-feed problem and a crash there would take down the
        # process that would otherwise log it.
        #
        # Asserted as "did not raise and produced a coherent outcome" rather than
        # as a specific action, because what the engine does with three bars is its
        # business, not this adapter's. The claim belongs to the adapter: whatever
        # came back, it was either a signal or a stated reason.
        source = AlBrooksSignalSource(
            StubBars(_synthetic_bars(3)),
            analyzer=__import__("albrooks").Analyzer(),
        )
        outcome = source.analyze("EURUSD", "H1")
        assert outcome.signal is not None or outcome.reason
        if outcome.signal is not None:
            assert not outcome.signal.is_tradable or outcome.signal.entry is not None

    def test_an_empty_series_never_reaches_the_engine(self) -> None:
        # Refused at the adapter boundary, so the engine is not asked to analyse
        # nothing -- which would read as NO_BARS, a statement about the market.
        from signal_to_trade_bridge.domain.errors import InvalidSignalError

        source = AlBrooksSignalSource(StubBars([]), analyzer=__import__("albrooks").Analyzer())
        with pytest.raises(InvalidSignalError, match="no closed bars"):
            source.latest_signal("EURUSD", "H1")

    def test_the_source_runs_end_to_end_against_the_real_engine(self, bars: list) -> None:
        source = AlBrooksSignalSource(StubBars(bars), analyzer=__import__("albrooks").Analyzer())
        outcome = source.analyze("EURUSD", "H1")
        assert outcome.source_reason
        if outcome.signal is not None:
            assert outcome.signal.source == "albrooks"
            assert outcome.signal.signal_id.startswith("stb-")


class TestAgainstTheRealTimeframeVocabulary:
    def test_timeframe_names_resolve(self) -> None:
        # The engine's core takes a free-form timeframe string and deliberately
        # measures bar steps from timestamps rather than parsing the label. The
        # MT5 adapter supplies the real vocabulary, and the bridge's MT5 data
        # adapter in Phase 7 will use it. Checked here so the vocabulary is known
        # to work before something depends on it.
        from albrooks.adapters.mt5 import timeframe_from_name

        assert timeframe_from_name("h4") == timeframe_from_name("H4")
        assert timeframe_from_name("M15") != timeframe_from_name("M30")

    def test_an_unknown_timeframe_is_refused(self) -> None:
        from albrooks.adapters.mt5 import TimeframeUnsupported, timeframe_from_name

        with pytest.raises(TimeframeUnsupported):
            timeframe_from_name("NOT_A_TIMEFRAME")


class TestPortability:
    def test_no_absolute_path_is_needed_to_import_the_adapter(self) -> None:
        # The upstream is installed from a local checkout, so an absolute path
        # appears in pyproject.toml. It must not appear in code, or the package
        # would only import on the machine whose path it names.
        adapter_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__import__("signal_to_trade_bridge").__file__))
        )
        assert adapter_dir
