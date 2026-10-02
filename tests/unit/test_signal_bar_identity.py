"""The bar in a signal's identity, recovered or refused. Phase 10.

`compute_signal_id` hashes `bar_index` and `bar_time`, and **the bar is the unit of
identity** -- that is what makes "the same reading on the same bar" recognisable as
a re-delivery rather than a new trade.

Phase 9 found the hole: the mapper falls back to `bar_index=-1` and `bar_time=None`
when the engine reports neither, and `Signal.bar_index` *defaults* to `-1` on the
model, so the two fallbacks agree and nothing looks wrong. A signal carrying both
produces a key with **no bar in it**, and two of them hash alike -- so the ledger
refuses a real second trade as a duplicate.

These tests pin the repair and, more importantly, **that it is a repair and not a
guess**: the identity is recovered from the bars the analysis actually ran on, which
is knowable at exactly one place, and refused when even those do not say.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

import pytest

from signal_to_trade_bridge.adapters.albrooks.source import _newest_bar
from signal_to_trade_bridge.domain.enums import Direction, SignalAction
from signal_to_trade_bridge.domain.models import Signal


def _signal(**overrides: object) -> Signal:
    defaults: dict[str, object] = {
        "signal_id": "stb-bar-0001",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": SignalAction.BUY,
        "direction": Direction.LONG,
        "entry": Decimal("1.10000"),
        "stop_loss": Decimal("1.09700"),
        "stop_basis": "PULLBACK_EXTREME",
        "setup_id": "pullback_h#0",
    }
    defaults.update(overrides)
    return Signal(**defaults)  # type: ignore[arg-type]


def _bars(count: int = 300, *, time: float = 1727740800.0, as_mapping: bool = False) -> Any:
    if as_mapping:
        return [{"index": i, "time": time + i} for i in range(count)]
    return [type("Bar", (), {"time": time + i})() for i in range(count)]


def _guard(signal: Signal, bars: object) -> None:
    """The guard, reached directly.

    Driven through a one-line stand-in for the source rather than by building a whole
    analyzer: the guard reads a signal and a bar series and nothing else, so a test
    that exercised the analyzer would be testing the *engine* stub instead.
    """
    from signal_to_trade_bridge.adapters.albrooks.source import AlBrooksSignalSource

    outcome = type("Outcome", (), {"signal": signal})()
    source = AlBrooksSignalSource.__new__(AlBrooksSignalSource)
    source._log = _NullLog()  # type: ignore[attr-defined]
    AlBrooksSignalSource._guard_bar_identity(source, outcome, bars)  # type: ignore[arg-type]


class _NullLog:
    def warning(self, *_args: object, **_fields: object) -> None:
        return None

    def event(self, *_args: object, **_fields: object) -> None:
        return None


class TestTheRecovery:
    def test_a_signal_with_no_bar_gets_one_from_the_bars_it_was_analysed_on(self) -> None:
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, _bars())
        assert signal.bar_index == 299
        assert signal.bar_time == pytest.approx(1727740800.0 + 299)

    def test_the_index_is_the_position_from_the_end(self) -> None:
        # The engine indexes bars that way, and a recovery that disagreed with its
        # convention would put the reading on a bar the engine never saw -- which is
        # worse than refusing.
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, _bars(50))
        assert signal.bar_index == 49

    def test_a_mapping_series_is_read_too(self) -> None:
        # The engine's own bars are dicts. A recovery that only handled objects
        # would work for this file's fixtures and for nothing real.
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, _bars(as_mapping=True))
        assert signal.bar_index == 299

    def test_a_signal_that_already_has_a_bar_is_untouched(self) -> None:
        # The guard must not overwrite a bar the engine *did* report. The engine's
        # own value is the authority; the bars are the fallback.
        signal = _signal(bar_index=42, bar_time=1234.0)
        _guard(signal, _bars())
        assert signal.bar_index == 42
        assert signal.bar_time == 1234.0

    def test_a_reported_bar_time_alone_discharges_the_guard(self) -> None:
        # The guard's condition is "`bar_index >= 0` **or** `bar_time is not None`" --
        # so a reported close time is enough on its own and the defaulted index is
        # left as it is.
        #
        # That is the right call rather than a loose one. Both fields feed the key, so
        # either one separates two readings; and *repairing* a defaulted index while
        # the engine reported a time would be mixing the engine's authority with ours
        # in one key, where the two can legitimately differ by a float rounding. When
        # the engine said something, it wins; the recovery only fills a total blank.
        signal = _signal(bar_index=-1, bar_time=999.0)
        _guard(signal, _bars())
        assert signal.bar_time == 999.0
        assert signal.bar_index == -1


class TestTheRefusal:
    def test_an_empty_series_leaves_the_signal_alone(self) -> None:
        # Nothing to recover from. The signal keeps its degenerate identity, and
        # this is the case `SIGNAL_BAR_UNKNOWN` names.
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, [])
        assert signal.bar_index == -1
        assert signal.bar_time is None

    def test_a_series_without_times_leaves_the_index_recovered(self) -> None:
        # Partial information is still information: the index alone separates
        # readings, so the time is left absent rather than invented.
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, [type("Bar", (), {"time": None})() for _ in range(10)])
        assert signal.bar_index == 9
        assert signal.bar_time is None

    def test_a_non_numeric_time_is_not_treated_as_one(self) -> None:
        # A string time would become `float("...")` and fail, or worse, be coerced
        # to zero. Either would be a bar time the market never had.
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, [{"index": 5, "time": "not a number"}])
        assert signal.bar_time is None

    def test_a_boolean_time_is_not_a_time(self) -> None:
        # `isinstance(True, int)` is true, so a naive check reads it as `1.0` and
        # the key gets a bar time of 1970.
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, [{"index": 5, "time": True}])
        assert signal.bar_time is None

    def test_a_series_that_is_not_a_series_is_refused(self) -> None:
        # The analyzer hands over whatever the data feed returned, and a feed that
        # returned a generator rather than a list must not be read as "index 0".
        signal = _signal(bar_index=-1, bar_time=None)
        _guard(signal, "not a series")
        assert signal.bar_index == -1
        assert signal.bar_time is None


class TestTheHelper:
    def test_it_reports_both_halves_or_neither(self) -> None:
        assert _newest_bar(_bars(3, time=100.0)) == (2, 102.0)
        assert _newest_bar([]) == (None, None)
        assert _newest_bar(None) == (None, None)  # type: ignore[arg-type]

    def test_a_mapping_index_wins_over_the_position(self) -> None:
        # The engine's own index is the authority when it gives one.
        series = [{"index": 77, "time": 5.0} for _ in range(3)]
        assert _newest_bar(series)[0] == 77

    def test_a_bad_index_falls_back_to_the_position(self) -> None:
        series = [{"index": "x", "time": 5.0} for _ in range(3)]
        assert _newest_bar(series)[0] == 2


class TestTheCollisionIsClosed:
    def test_two_readings_over_the_same_bars_now_differ_by_index(self) -> None:
        # The defect, restated as a positive: two readings that Phase 9 could not
        # tell apart now carry the bar the analysis ran on, so they hash differently.
        from signal_to_trade_bridge.adapters.albrooks.identity import compute_signal_id

        first = _signal(bar_index=-1, bar_time=None)
        second = _signal(bar_index=-1, bar_time=None)
        _guard(first, _bars(300, time=1000.0))
        _guard(second, _bars(300, time=2000.0))

        def key(s: Signal) -> str:
            return compute_signal_id(
                symbol=s.symbol,
                timeframe=s.timeframe,
                bar_index=s.bar_index,
                bar_time=s.bar_time,
                action=s.action,
                direction=s.direction,
                setup_id=s.setup_id,
            )

        assert key(first) != key(second)

    def test_a_re_delivery_over_the_same_bars_still_collapses(self) -> None:
        # ...and the direction the other way. A re-delivery of the *same* reading
        # must still produce the same key, or nothing would ever be recognised as a
        # duplicate and the ledger would do nothing at all.
        from signal_to_trade_bridge.adapters.albrooks.identity import compute_signal_id

        first = _signal(bar_index=-1, bar_time=None)
        second = _signal(bar_index=-1, bar_time=None)
        _guard(first, _bars(300, time=1000.0))
        _guard(second, _bars(300, time=1000.0))

        def key(s: Signal) -> str:
            return compute_signal_id(
                symbol=s.symbol,
                timeframe=s.timeframe,
                bar_index=s.bar_index,
                bar_time=s.bar_time,
                action=s.action,
                direction=s.direction,
                setup_id=s.setup_id,
            )

        assert key(first) == key(second)


def test_the_guard_is_reached_from_analyze() -> None:
    """Structural: the guard must actually be called.

    Every test above drives ``_guard_bar_identity`` directly, so without this the
    repair could be perfect and unreachable -- a function that exists, is correct,
    and is never invoked, which is indistinguishable from one that was never written.
    """
    from signal_to_trade_bridge.adapters.albrooks import source as module

    body = inspect.getsource(module.AlBrooksSignalSource.analyze)
    assert "_guard_bar_identity" in body
