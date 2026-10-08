"""The signal source: fetching, delegating and logging.

The adapter's job is narrow: ask for bars, hand them to the engine, map what comes
back, and record what happened. Every test here uses a stub analyzer, so the suite
runs on a machine that has never cloned the upstream repository.
"""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest

from signal_to_trade_bridge.adapters.albrooks.source import (
    AlBrooksSignalSource,
    abstention_reason,
    is_tradable_signal,
)
from signal_to_trade_bridge.domain.errors import InvalidSignalError
from signal_to_trade_bridge.infrastructure.logging import configure_logging
from tests.stubs import (
    StubAnalyzer,
    StubMarketData,
    stub_abstention,
    stub_buy,
    stub_degenerate_no_bars,
    stub_sell,
)


@pytest.fixture(autouse=True)
def _logs() -> StringIO:
    """Capture the bridge's own log output for the whole module.

    Autouse, because the adapter's logging is a behaviour under test rather than
    a side effect: an abstention and a degenerate result must be distinguishable
    in the log, and that is only checkable by reading it.
    """
    stream = StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    return stream


def _source(
    result: object = None,
    *,
    bars: list[object] | None = None,
    analyzer_error: Exception | None = None,
    data_error: Exception | None = None,
    bar_count: int = 300,
) -> AlBrooksSignalSource:
    return AlBrooksSignalSource(
        StubMarketData(bars=bars, error=data_error),
        analyzer=StubAnalyzer(result, error=analyzer_error),
        bar_count=bar_count,
    )


class TestHappyPath:
    def test_returns_a_mapped_buy(self) -> None:
        signal = _source(stub_buy()).latest_signal("EURUSD", "H1")
        assert signal is not None
        assert signal.is_tradable
        assert signal.entry == Decimal("1.10000")

    def test_returns_a_mapped_sell(self) -> None:
        signal = _source(stub_sell()).latest_signal("EURUSD", "H1")
        assert signal is not None
        assert signal.direction.value == "SHORT"

    def test_upper_cases_the_symbol_before_asking_the_engine(self) -> None:
        # The broker's spelling may differ in case from what a caller typed, and
        # the key is derived from an upper-cased symbol, so normalising here is
        # what keeps the two consistent.
        source = _source(stub_buy())
        source.latest_signal("eurusd", "H1")
        assert source._analyzer.calls[0]["symbol"] == "EURUSD"

    def test_passes_the_requested_bar_count_to_the_data_source(self) -> None:
        data = StubMarketData()
        source = AlBrooksSignalSource(data, analyzer=StubAnalyzer(stub_buy()), bar_count=250)
        source.latest_signal("EURUSD", "H1")
        assert data.calls[0]["count"] == 250

    def test_is_stateless_across_calls(self) -> None:
        # Two calls over identical data must produce identical signals -- the
        # property Phase 9's idempotency rests on. If the adapter cached anything,
        # this would fail.
        source = _source(stub_buy())
        first = source.latest_signal("EURUSD", "H1")
        second = source.latest_signal("EURUSD", "H1")
        assert first is not None and second is not None
        assert first.signal_id == second.signal_id

    def test_two_different_bars_produce_two_different_signals(self) -> None:
        first = _source(stub_buy()).latest_signal("EURUSD", "H1")
        later_result = stub_buy()
        later_result.last_closed_bar = 300
        second = _source(later_result).latest_signal("EURUSD", "H1")
        assert first is not None and second is not None
        assert first.signal_id != second.signal_id


class TestAbstentions:
    def test_an_abstention_is_returned_rather_than_raised(self) -> None:
        # "The engine found no setup" is the commonest outcome there is. Raising
        # for it would make a quiet market indistinguishable from a broken one.
        signal = _source(stub_abstention("WAIT")).latest_signal("EURUSD", "H1")
        assert signal is not None
        assert not signal.is_tradable

    def test_a_degenerate_result_is_refused_not_raised(self) -> None:
        assert _source(stub_degenerate_no_bars()).latest_signal("EURUSD", "H1") is None

    def test_is_tradable_signal_rejects_an_abstention(self) -> None:
        assert not is_tradable_signal(_source(stub_abstention()).latest_signal("EURUSD", "H1"))
        assert not is_tradable_signal(None)

    def test_abstention_reason_reports_the_upstream_code(self) -> None:
        signal = _source(stub_abstention("WAIT", "EVIDENCE_CONFLICT")).latest_signal("EURUSD", "H1")
        assert signal is not None
        assert abstention_reason(signal) == "EVIDENCE_CONFLICT"

    def test_wait_and_no_trade_report_different_reasons(self) -> None:
        wait = _source(stub_abstention("WAIT", "ALL_CANDIDATES_VETOED")).latest_signal(
            "EURUSD", "H1"
        )
        no_trade = _source(stub_abstention("NO_TRADE", "NO_CANDIDATES")).latest_signal(
            "EURUSD", "H1"
        )
        assert wait is not None and no_trade is not None
        assert abstention_reason(wait) != abstention_reason(no_trade)


class TestFailurePaths:
    def test_a_data_feed_failure_is_refused_not_propagated(self) -> None:
        # A feed that is down is not a trading decision, and a raw exception here
        # would surface as a stack trace wherever the caller happened to be.
        with pytest.raises(InvalidSignalError, match="could not read closed bars"):
            _source(data_error=ConnectionError("terminal not running")).latest_signal(
                "EURUSD", "H1"
            )

    def test_an_engine_failure_is_refused_not_propagated(self) -> None:
        # The engine is a library, not a network service, so this is a bug rather
        # than an outage -- but a trading loop should record a decision, not die
        # with a traceback from a dependency.
        with pytest.raises(InvalidSignalError, match="failed while analysing"):
            _source(analyzer_error=RuntimeError("boom")).latest_signal("EURUSD", "H1")

    def test_the_cause_is_preserved_for_debugging(self) -> None:
        with pytest.raises(InvalidSignalError) as info:
            _source(analyzer_error=RuntimeError("boom")).latest_signal("EURUSD", "H1")
        assert isinstance(info.value.__cause__, RuntimeError)

    def test_empty_bars_are_refused(self) -> None:
        # An empty series reaching the engine reads as NO_BARS, which the engine
        # itself calls a statement about the connection rather than the market.
        with pytest.raises(InvalidSignalError, match="no closed bars"):
            _source(bars=[]).latest_signal("EURUSD", "H1")

    def test_a_blank_symbol_is_refused_before_any_io(self) -> None:
        with pytest.raises(InvalidSignalError, match="symbol is required"):
            _source(stub_buy()).latest_signal("   ", "H1")

    def test_a_non_positive_bar_count_is_refused_at_construction(self) -> None:
        # Caught here rather than at first use, so a misconfiguration cannot
        # produce a source that silently analyses nothing.
        with pytest.raises(ValueError, match="bar_count must be positive"):
            AlBrooksSignalSource(StubMarketData(), analyzer=StubAnalyzer(stub_buy()), bar_count=0)

    def test_a_missing_upstream_package_says_how_to_install_it(self) -> None:
        # The error has to name the fix. "No module named albrooks" from the
        # middle of a trading loop tells an operator nothing about which of the two
        # private repositories is missing or that scripts/setup.ps1 handles it.
        from signal_to_trade_bridge.adapters.albrooks.source import _default_analyzer

        try:
            import albrooks  # noqa: F401
        except ImportError:
            # Raw, because the `.` in `setup.ps1` is a regex metacharacter and an
            # unescaped one would match any character -- so the assertion would
            # pass against `setupXps1` too, which is not the point.
            with pytest.raises(InvalidSignalError, match=r"scripts/setup\.ps1"):
                _default_analyzer()
        else:
            # albrooks is installed on this machine, so the real constructor is
            # the thing under test instead.
            assert _default_analyzer() is not None

    def test_the_real_analyzer_satisfies_the_declared_protocol(self) -> None:
        # Backs the `cast` in `_default_analyzer`. That cast exists because mypy
        # cannot see a private package CI cannot install, which means the compiler
        # cannot check the claim the cast makes -- so it is checked here, at
        # runtime, on the machines that do have the package. A protocol that has
        # drifted would fail this rather than surfacing as a confusing
        # `AttributeError` during live trading.
        import inspect

        from signal_to_trade_bridge.adapters.albrooks.source import AnalyzerLike

        if not getattr(AnalyzerLike, "_is_runtime_protocol", False):
            pytest.skip("AnalyzerLike is not a runtime-checkable Protocol")
        try:
            from albrooks.engine.analyzer import Analyzer
        except ImportError:
            pytest.skip("albrooks is not installed")
        # `runtime_checkable` only verifies method *names*, so the signature is
        # checked directly. Both of these are what the adapter depends on.
        signature = inspect.signature(Analyzer.analyze)
        assert "bars" in signature.parameters
        assert "symbol" in signature.parameters
        assert "timeframe" in signature.parameters
        assert "last_closed" in signature.parameters


class TestObservability:
    def test_a_tradable_signal_is_logged_with_its_identity(self, _logs: StringIO) -> None:
        import json

        _source(stub_buy()).latest_signal("EURUSD", "H1")
        events = [json.loads(line) for line in _logs.getvalue().splitlines() if line.strip()]
        received = [event for event in events if event.get("event") == "SIGNAL_RECEIVED"]
        assert received
        first = received[0]
        assert first["symbol"] == "EURUSD"
        assert first["action"] == "BUY"
        assert first["stop_basis"] == "PULLBACK_EXTREME"
        assert first["evidence_is_probability"] is False

    def test_the_logged_signal_id_matches_the_returned_signal(self, _logs: StringIO) -> None:
        # Traceability: a decision log that named a different signal than the one
        # traded would be worse than no log at all.
        import json

        signal = _source(stub_buy()).latest_signal("EURUSD", "H1")
        assert signal is not None
        events = [json.loads(line) for line in _logs.getvalue().splitlines() if line.strip()]
        logged = {event.get("signal_id") for event in events}
        assert signal.signal_id in logged

    def test_an_abstention_is_logged_as_received_not_as_a_rejection(self, _logs: StringIO) -> None:
        # The engine made a considered decision. Logging it as a rejection would
        # make a normal abstention look like a fault in this bridge.
        import json

        _source(stub_abstention("WAIT")).latest_signal("EURUSD", "H1")
        events = [json.loads(line) for line in _logs.getvalue().splitlines() if line.strip()]
        assert any(event.get("event") == "SIGNAL_RECEIVED" for event in events)
        assert not any(event.get("event") == "SIGNAL_REJECTED" for event in events)

    def test_a_degenerate_result_is_logged_as_a_rejection(self, _logs: StringIO) -> None:
        # Different from an abstention: no analysis ran at all, which is a fact
        # about the input rather than about the market.
        import json

        _source(stub_degenerate_no_bars()).latest_signal("EURUSD", "H1")
        events = [json.loads(line) for line in _logs.getvalue().splitlines() if line.strip()]
        rejections = [e for e in events if e.get("event") == "SIGNAL_REJECTED"]
        assert rejections
        assert rejections[0]["source_reason"] == "NO_BARS"

    def test_a_data_failure_is_logged_before_being_raised(self, _logs: StringIO) -> None:
        import json

        with pytest.raises(InvalidSignalError):
            _source(data_error=ConnectionError("down")).latest_signal("EURUSD", "H1")
        events = [json.loads(line) for line in _logs.getvalue().splitlines() if line.strip()]
        unavailable = [e for e in events if e.get("event") == "SIGNAL_SOURCE_UNAVAILABLE"]
        assert unavailable
        assert unavailable[0]["reason"] == "MARKET_DATA_UNAVAILABLE"


class _FakeFrozenSeries:
    """Shaped like the engine's `FrozenSeries`: a non-iterable transport type.

    Carries the analyzable bars on `.series` -- the attribute the engine's own
    documented flow hands to the analyzer (`AnalysisSession().on_bars(
    frozen.series, freeze=frozen.freeze)`). Deliberately defines no `__iter__`
    and no `__len__`, so handing it to anything that iterates raises
    `TypeError: '...' object is not iterable`, exactly like the real one.
    """

    def __init__(self, series: list[object]) -> None:
        self.series = series


class _StrictAnalyzer:
    """Mimics the current engine boundary: it wraps input in `BarSeries`.

    `BarSeries(bars, ...)` iterates its input, so a `FrozenSeries` handed in
    directly raises `TypeError` -- the failure this class reproduces. The
    ordinary `StubAnalyzer` never iterates, so it cannot catch this; a stub
    that only proves itself consistent is how the breakage shipped.
    """

    def __init__(self, result: object) -> None:
        self._result = result
        self.received: object = None

    def analyze(
        self,
        bars: object,
        symbol: str = "GENERIC",
        timeframe: str = "UNKNOWN",
        last_closed: int | None = None,
    ) -> object:
        self.received = bars
        tuple(bars)  # type: ignore[arg-type] -- what BarSeries construction does
        return self._result


class TestFrozenSeriesUnwrap:
    """`MT5Feed.closed_bars` returns `FrozenSeries`; the analyzer takes series."""

    def test_the_analyzer_receives_the_inner_series_not_the_wrapper(self) -> None:
        inner = [{"close": 1.1}, {"close": 1.2}]
        analyzer = _StrictAnalyzer(stub_buy())
        source = AlBrooksSignalSource(
            StubMarketData(bars=_FakeFrozenSeries(inner)),  # type: ignore[arg-type]
            analyzer=analyzer,  # type: ignore[arg-type]
        )
        signal = source.latest_signal("EURUSD", "H1")
        assert signal is not None and signal.is_tradable
        assert analyzer.received is inner

    def test_plain_lists_still_pass_through_by_identity(self) -> None:
        # The unwrap must not touch what already worked: a list provider's
        # bars reach the analyzer as the identical object.
        inner = [{"close": 1.1}, {"close": 1.2}]
        analyzer = _StrictAnalyzer(stub_buy())
        source = AlBrooksSignalSource(
            StubMarketData(bars=inner),
            analyzer=analyzer,  # type: ignore[arg-type]
        )
        source.latest_signal("EURUSD", "H1")
        assert analyzer.received is inner

    def test_a_frozen_series_keeps_real_bar_time_prices_and_identity(self) -> None:
        # End to end through the previously failing path: current bar time in,
        # current bar time out, with prices and the deterministic identity
        # intact. Nothing invented -- the bars below stand in for MT5 output
        # the way every stub in `tests/stubs.py` stands in for engine output.
        import time

        now = float(int(time.time()))
        result = stub_buy()
        result.last_closed_bar = 299
        result.bar_features = [
            {"index": 298, "time": now - 3600.0},
            {"index": 299, "time": now},
            {"index": 300, "time": now + 3600.0},
        ]
        analyzer = _StrictAnalyzer(result)
        source = AlBrooksSignalSource(
            StubMarketData(bars=_FakeFrozenSeries([{"close": 1.1}])),
            analyzer=analyzer,  # type: ignore[arg-type]
        )
        first = source.latest_signal("EURUSD", "H1")
        second = source.latest_signal("EURUSD", "H1")
        assert first is not None and first.is_tradable
        assert first.bar_time == now
        assert first.entry == Decimal("1.10000")
        assert first.stop_loss == Decimal("1.09700")
        assert first.take_profit == Decimal("1.10300")
        assert second is not None and second.signal_id == first.signal_id
