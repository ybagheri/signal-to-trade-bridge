"""The fresh-signal handoff: engine reading -> JSON file -> `trade`.

The demo execution test refused the static `signal` fixture as expired, which
is the fixture working as designed: `signal` prints a fixed example whose
`bar_time` is a constant from the past, while real execution needs a reading
taken from current market bars. The supported producer path is

```
albrooks.adapters.mt5.MT5Feed.closed_bars(symbol, timeframe, count)
    -> AlBrooksSignalSource.latest_signal(symbol, timeframe)
    -> Signal.to_dict() written as JSON
    -> trade / check / --what-if reading that file
```

No step there may invent a price or a timestamp: the bar time travels from the
analysed bar, through the mapped signal, into the file, and back out through
`_signal_from` untouched. These tests pin that this handoff preserves the
timestamp and the identity it participates in, using stub bars and a stub
analyzer -- the bars here are synthetic fixtures exercising the plumbing, not
market data, and nothing in this file connects to a terminal.

What is deliberately NOT covered here: fetching real bars requires the
`albrooks` package and a running MT5 terminal, neither of which exists on a
test machine. That half is an operational procedure on the Windows host, not
an automated assertion.
"""

from __future__ import annotations

import json
import time
from io import StringIO
from typing import Any

from signal_to_trade_bridge.adapters.albrooks.source import AlBrooksSignalSource
from signal_to_trade_bridge.cli import main
from tests.stubs import StubAnalyzer, StubMarketData, StubResult, stub_buy


def _current_buy(now: float) -> StubResult:
    """A BUY shaped exactly like :func:`stub_buy` but stamped on a current bar.

    Same thirteen-key decision, same structural bases -- only the clock moves.
    The bar features mirror the stub's convention (analysed index flanked by
    its neighbours) so the adapter reads the analysed bar's time, not the
    last row's.
    """
    result = stub_buy()
    result.last_closed_bar = 299
    result.bar_features = [
        {"index": 298, "time": now - 3600.0},
        {"index": 299, "time": now},
        {"index": 300, "time": now + 3600.0},
    ]
    return result


def _round_trip(signal: Any) -> Any:
    """`Signal.to_dict()` through JSON and back through the `trade` parser."""
    from signal_to_trade_bridge.cli.main import _signal_from

    payload = json.loads(json.dumps(signal.to_dict()))
    return _signal_from(payload)


class TestTheTimestampSurvivesTheFile:
    def test_engine_bar_time_reaches_the_trade_parser_intact(self) -> None:
        now = float(int(time.time()))
        source = AlBrooksSignalSource(StubMarketData(), analyzer=StubAnalyzer(_current_buy(now)))
        signal = source.latest_signal("EURUSD", "H1")
        assert signal is not None and signal.is_tradable

        parsed = _round_trip(signal)
        assert parsed.bar_time == now
        assert parsed.bar_index == 299
        # Current, not merely present: a timestamp that survived the file
        # but predates the market by hours is exactly what expiry refuses.
        assert abs(time.time() - parsed.bar_time) < 3600

    def test_prices_and_identity_survive_the_file(self) -> None:
        now = float(int(time.time()))
        source = AlBrooksSignalSource(StubMarketData(), analyzer=StubAnalyzer(_current_buy(now)))
        signal = source.latest_signal("EURUSD", "H1")
        assert signal is not None

        parsed = _round_trip(signal)
        assert parsed.signal_id == signal.signal_id
        assert parsed.symbol == "EURUSD"
        assert parsed.action.value == "BUY"
        assert parsed.direction.value == "LONG"
        assert parsed.entry == signal.entry
        assert parsed.stop_loss == signal.stop_loss
        assert parsed.take_profit == signal.take_profit
        assert parsed.stop_basis == "PULLBACK_EXTREME"
        assert parsed.take_profit_basis == "SWING"
        assert parsed.is_tradable


class TestTheStaticFixtureIsNotAFreshSignal:
    def test_signal_emits_a_fixed_past_bar_time(self) -> None:
        # The `signal` command is a worked example, not a market reading: two
        # invocations print the same `bar_time`, and it lies in the past. A
        # file written from it is correctly refused as expired by real
        # execution -- anyone "fixing" expiry by refreshing this constant is
        # fabricating a timestamp, not producing a signal.
        first = StringIO()
        assert main(["signal"], out=first) == 0
        second = StringIO()
        assert main(["signal"], out=second) == 0
        first_time = json.loads(first.getvalue())["bar_time"]
        second_time = json.loads(second.getvalue())["bar_time"]
        assert first_time == second_time
        assert second_time < time.time() - 3600
