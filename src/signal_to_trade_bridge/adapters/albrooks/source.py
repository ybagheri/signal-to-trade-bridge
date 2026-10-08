"""The Al Brooks price-action engine, as a signal source.

This is the first of the two adapters, and it is where the anti-corruption layer
is either real or notional. Everything upstream-shaped -- the ``Analyzer`` class,
the decision dict, the string actions -- is confined to this sub-package.
:class:`AlBrooksSignalSource` is the only name the rest of the project imports,
and its return type is the bridge's own :class:`Signal`.

The engine is a *library*, not a service: there is no process to talk to, no port
to open and no callback to register. It has one entry point,
``Analyzer.analyze(bars, symbol, timeframe, last_closed) -> AnalysisResult``, and
the analysis happens synchronously inside that call. So "connecting" to it is not
a thing, and this adapter's job is narrower than the execution adapter's will be:
translate a result, and do it without ever raising for ordinary outcomes.

Bars come from a :class:`~signal_to_trade_bridge.ports.MarketDataProvider`, so
this module has no opinion about where they come from. Phase 2 tests inject a
stub analyzer; the MT5 data adapter arrives in Phase 7.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, cast

from signal_to_trade_bridge.adapters.albrooks.identity import compute_signal_id
from signal_to_trade_bridge.adapters.albrooks.mapper import (
    AnalysisOutcome,
    map_result_to_signal,
)
from signal_to_trade_bridge.domain.enums import Direction, SignalAction
from signal_to_trade_bridge.domain.errors import InvalidSignalError
from signal_to_trade_bridge.domain.models import Signal
from signal_to_trade_bridge.infrastructure.logging import Event, get_logger

if TYPE_CHECKING:
    from signal_to_trade_bridge.ports import MarketDataProvider

__all__ = ["AlBrooksSignalSource", "AnalyzerLike"]


class AnalyzerLike(Protocol):
    """The one upstream method this adapter calls.

    Declared as a Protocol rather than importing the concrete ``Analyzer`` so the
    tests can inject a stub and exercise every branch of the mapping without the
    upstream package installed. That is not a convenience: the upstream projects
    are private repositories, and a test suite that could only run on a machine
    with both cloned would not be a safety net, it would be a souvenir.
    """

    def analyze(
        self,
        bars: Sequence[Any],
        symbol: str = "GENERIC",
        timeframe: str = "UNKNOWN",
        last_closed: int | None = None,
    ) -> Any: ...


def _default_analyzer() -> AnalyzerLike:
    """Construct the real upstream analyzer, importing it lazily.

    Lazy because it is the only reason this package would need ``albrooks``
    installed. Keeping the import inside a function means ``import
    signal_to_trade_bridge.adapters.albrooks`` succeeds on a machine that has
    never heard of the engine, and a caller who only wants the mapping functions
    can have them.
    """
    try:
        from albrooks.engine.analyzer import Analyzer
    except ImportError as exc:  # pragma: no cover - exercised only without albrooks
        raise InvalidSignalError(
            "the albrooks package is not installed. Both upstream projects are private "
            "repositories and cannot be installed from an index; run scripts/setup.ps1, "
            "which installs it from a local checkout."
        ) from exc
    # An explicit cast rather than a bare return. `albrooks` is a private package
    # that CI cannot install, so mypy sees it as untyped and the constructor
    # resolves to `Any`. Returning that directly would make every downstream use
    # of the analyzer unchecked -- the whole adapter would be silently untyped past
    # this line. The cast states the claim this module actually relies on: that
    # `Analyzer.analyze` has the signature `AnalyzerLike` declares. A test asserts
    # it at runtime, so the claim is verified rather than merely asserted.
    return cast("AnalyzerLike", Analyzer())


class AlBrooksSignalSource:
    """Reads a price-action analysis and presents it as a bridge :class:`Signal`.

    Implements ``SignalSource``: one method, returning ``None`` when there is
    nothing to say.

    Stateless by construction. The upstream analyzer is passed in or built once
    and reused, because it holds a detector registry and rebuilding one per bar
    would rebuild eleven detectors per bar for no benefit. There is no mutable
    state here at all, which is what makes two consecutive calls over identical
    data produce identical signals -- the property Phase 9's idempotency depends
    on.
    """

    def __init__(
        self,
        market_data: MarketDataProvider,
        *,
        analyzer: AnalyzerLike | None = None,
        bar_count: int = 300,
        logger: Any | None = None,
    ) -> None:
        if bar_count <= 0:
            raise ValueError(f"bar_count must be positive, got {bar_count}")
        self._market_data = market_data
        self._analyzer = analyzer
        self._bar_count = bar_count
        self._log = logger or get_logger("adapters.albrooks")

    def _resolve_analyzer(self) -> AnalyzerLike:
        if self._analyzer is None:
            self._analyzer = _default_analyzer()
        return self._analyzer

    def analyze(self, symbol: str, timeframe: str) -> AnalysisOutcome:
        """Run the engine and map the result.

        Split out from :meth:`latest_signal` so a caller that wants the upstream
        reason codes and the full mapped metadata -- a diagnostic, or a Phase 11
        validation -- can have them without re-deriving anything.
        """
        normal_symbol = symbol.strip().upper()
        if not normal_symbol:
            raise InvalidSignalError("a symbol is required to analyse")

        try:
            bars = self._market_data.closed_bars(normal_symbol, timeframe, self._bar_count)
        except Exception as exc:
            # A data feed that is down is not a trading decision, and letting the
            # exception escape would put a stack trace wherever the caller
            # happened to call from. Refused, with the cause named.
            self._log.error(
                Event.SIGNAL_SOURCE_UNAVAILABLE,
                symbol=normal_symbol,
                timeframe=timeframe,
                reason="MARKET_DATA_UNAVAILABLE",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise InvalidSignalError(
                f"could not read closed bars for {normal_symbol} {timeframe}: {exc}"
            ) from exc

        if not bars:
            self._log.warning(
                Event.SIGNAL_SOURCE_UNAVAILABLE,
                symbol=normal_symbol,
                timeframe=timeframe,
                reason="MARKET_DATA_UNAVAILABLE",
                detail="the data source returned no bars",
            )
            raise InvalidSignalError(f"no closed bars available for {normal_symbol} {timeframe}")

        try:
            result = self._resolve_analyzer().analyze(
                _analyzable_bars(bars), symbol=normal_symbol, timeframe=timeframe
            )
        except InvalidSignalError:
            raise
        except Exception as exc:
            # The engine is a library, not a network service, so an exception here
            # is a bug rather than an outage. Still refused rather than propagated
            # raw: a trading loop should record a decision, not die with a
            # traceback from a dependency.
            self._log.error(
                Event.SIGNAL_SOURCE_UNAVAILABLE,
                symbol=normal_symbol,
                timeframe=timeframe,
                reason="INTEGRATION_ERROR",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise InvalidSignalError(
                f"the price-action engine failed while analysing {normal_symbol} "
                f"{timeframe}: {type(exc).__name__}: {exc}"
            ) from exc

        outcome = map_result_to_signal(result)
        self._guard_bar_identity(outcome, _analyzable_bars(bars))
        self._log_outcome(normal_symbol, timeframe, outcome)
        return outcome

    def _guard_bar_identity(self, outcome: AnalysisOutcome, bars: object) -> None:
        """Repair a signal whose identity is missing its bar. Phase 10.

        ``compute_signal_id`` hashes ``bar_index`` and ``bar_time``, and **the bar
        is the unit of identity** -- that is what makes "the same reading on the
        same bar" recognisable as a re-delivery rather than a new trade. The mapper
        falls back to ``bar_index=-1`` and ``bar_time=None`` when the engine's result
        carries neither, and ``Signal.bar_index`` *defaults* to ``-1`` on the model
        itself, so the two fallbacks agree and nothing anywhere looks wrong.

        The consequence is a silent one. With no bar in the key, two readings hash
        to the same value; the ledger therefore treats the second as a duplicate and
        refuses it -- **a genuine new trade, suppressed by a mechanism designed to
        suppress re-deliveries.** Different symbols and different setups still
        separate, and either field alone is enough, so it takes both being absent.

        **Repaired, and here rather than downstream.** The bar is knowable at exactly
        one place -- the adapter that read the bars and asked the engine to analyse
        them -- and here the fallback bars are still in hand.

        Two cases, and they recover from different places:

        * **Both missing.** The engine reported no bar at all, so the index comes
          from the series position and the time from its last element -- the newest
          bar this adapter actually supplied, which under the default analysis
          (``last_closed`` unset, i.e. the newest bar) is the bar the reading was
          made on, not a guess.
        * **Only the time missing.** The engine reported an index but no time
          (e.g. a result whose per-bar records carry no timestamp). The time is
          then read from the bar *at that reported index*, never from the newest
          bar: the signal was made on the indexed bar, and dating it with another
          bar's time would be a fabrication, not a repair.

        A time the engine did report is never overwritten, and a reported time
        alone (with a defaulted index) still discharges the guard: either field
        separates two readings, and mixing the engine's authority with ours in one
        key is worse than leaving the default.

        Whenever a field is repaired, ``signal_id`` is recomputed from the repaired
        fields. The id is a hash of the reading including its bar, so filling the
        bar without re-hashing would leave an identity that still has no bar in
        it -- the exact collision this guard exists to close.
        """
        signal = outcome.signal
        if signal is None:
            return
        if signal.bar_index >= 0 and signal.bar_time is not None:
            return
        if signal.bar_time is not None:
            return

        # From here on the time is missing. Recovered from the bars, not invented.
        if signal.bar_index >= 0:
            recovered = _bar_time_at(bars, signal.bar_index)
            if recovered is None:
                self._log.warning(
                    Event.SIGNAL_REJECTED,
                    symbol=signal.symbol,
                    signal_id=signal.signal_id,
                    reason="SIGNAL_BAR_UNKNOWN",
                    explanation=(
                        f"the engine reported bar index {signal.bar_index} but no bar time, "
                        "and the bar series supplied to the engine carries no readable time "
                        "at that index either, so this reading cannot be dated to its bar. "
                        "Its identity was left untouched rather than dated with another "
                        "bar's time."
                    ),
                )
                return
            self._log.warning(
                Event.SIGNAL_REJECTED,
                symbol=signal.symbol,
                signal_id=signal.signal_id,
                reason="SIGNAL_BAR_RECOVERED",
                bar_index=signal.bar_index,
                bar_time=recovered,
                explanation=(
                    "the engine reported a bar index but no bar time, so the time was "
                    "recovered from the bar at that index in the series the analysis "
                    "actually ran on -- the bar the reading was made on, not the newest "
                    "bar."
                ),
            )
            object.__setattr__(signal, "bar_time", recovered)
            self._rehash(signal)
            return

        # Both missing: `_bar_index` wants the engine's own attribute, which is
        # what is missing, so the index comes from the series position and the
        # time from its last element.
        index, close_time = _newest_bar(bars)
        if index is None and close_time is None:
            self._log.warning(
                Event.SIGNAL_REJECTED,
                symbol=signal.symbol,
                signal_id=signal.signal_id,
                reason="SIGNAL_BAR_UNKNOWN",
                explanation=(
                    "the engine reported neither a bar index nor a bar time, and the bar "
                    "series supplied to the engine does not carry them either, so this "
                    "reading has no bar in its identity. Two such readings hash to the same "
                    "key, and the ledger would refuse the second as a duplicate -- "
                    "suppressing a real trade with a mechanism meant to suppress "
                    "re-deliveries."
                ),
            )
            return

        self._log.warning(
            Event.SIGNAL_REJECTED,
            symbol=signal.symbol,
            signal_id=signal.signal_id,
            reason="SIGNAL_BAR_RECOVERED",
            bar_index=index,
            bar_time=close_time,
            explanation=(
                "the engine reported no bar, so the identity was recovered from the bar "
                "series the analysis actually ran on. Without it two readings of this "
                "symbol would share one key and the second would be refused as a "
                "duplicate."
            ),
        )
        if index is not None:
            object.__setattr__(signal, "bar_index", index)
        if close_time is not None:
            object.__setattr__(signal, "bar_time", close_time)
        self._rehash(signal)

    @staticmethod
    def _rehash(signal: Signal) -> None:
        """Recompute ``signal_id`` from the (repaired) identity fields.

        The id is a deterministic hash of symbol, timeframe, bar, action,
        direction and setup. Repairing the bar without re-hashing would leave the
        ledger key exactly as bar-less as before -- the collision this guard
        exists to close, preserved in the one field the ledger actually reads.
        """
        object.__setattr__(
            signal,
            "signal_id",
            compute_signal_id(
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                bar_index=signal.bar_index,
                bar_time=signal.bar_time,
                action=signal.action,
                direction=signal.direction,
                setup_id=signal.setup_id,
            ),
        )

    def latest_signal(self, symbol: str, timeframe: str) -> Signal | None:
        """The most recent signal, or ``None`` when there is nothing to trade.

        ``None`` is a normal answer, not a failure. "The engine found no setup" is
        the commonest outcome there is, and a source that raised for it would make
        a quiet market indistinguishable from a broken one.
        """
        return self.analyze(symbol, timeframe).signal

    def _log_outcome(self, symbol: str, timeframe: str, outcome: AnalysisOutcome) -> None:
        """Record what the mapping produced, and why when it produced nothing.

        The abstention and the malformed result are logged at different levels
        and with different reasons, because they mean different things. ``WAIT``
        is a considered decision by the engine; a degenerate result means no
        analysis ran at all, which is a fact about the input.
        """
        if outcome.degenerate:
            self._log.warning(
                Event.SIGNAL_REJECTED,
                symbol=symbol,
                timeframe=timeframe,
                reason=outcome.reason,
                source_reason=outcome.source_reason,
                explanation=outcome.explanation,
            )
            return

        signal = outcome.signal
        if signal is None:
            self._log.warning(
                Event.SIGNAL_REJECTED,
                symbol=symbol,
                timeframe=timeframe,
                reason=outcome.reason,
                source_reason=outcome.source_reason,
                explanation=outcome.explanation,
            )
            return

        self._log.event(
            Event.SIGNAL_RECEIVED,
            signal_id=signal.signal_id,
            symbol=symbol,
            timeframe=timeframe,
            action=signal.action.value,
            direction=signal.direction.value,
            is_tradable=signal.is_tradable,
            entry=str(signal.entry),
            stop_loss=str(signal.stop_loss) if signal.stop_loss is not None else None,
            take_profit=str(signal.take_profit) if signal.take_profit is not None else None,
            stop_basis=signal.stop_basis,
            take_profit_basis=signal.take_profit_basis,
            setup_id=signal.setup_id,
            bar_index=signal.bar_index,
            evidence_score=signal.evidence_score,
            evidence_is_probability=False,
            source_reason=outcome.source_reason,
        )
        if not signal.is_tradable:
            self._log.event(
                Event.SIGNAL_RECEIVED,
                signal_id=signal.signal_id,
                symbol=symbol,
                action=signal.action.value,
                note="an abstention, not a trade; the engine declined to answer",
                source_reason=outcome.source_reason,
            )


def is_tradable_signal(signal: Signal | None) -> bool:
    """Whether a signal asks for a trade.

    A tiny helper rather than a method, because the check appears at three call
    sites and each of them would otherwise re-derive it slightly differently.
    """
    return signal is not None and signal.action.is_tradable and signal.direction.is_tradable


def abstention_reason(signal: Signal) -> str:
    """Why a signal is not a trade, in words.

    Returns the upstream reason when the signal carries one, because the engine
    knows more about why it abstained than this project can reconstruct. The
    ``WAIT`` and ``NO_TRADE`` distinction is preserved verbatim: one is a
    condition that was evaluated and not met, the other is a refusal to answer,
    and a log that flattened them would make a quiet day unexplainable.
    """
    if signal.action is SignalAction.NO_TRADE:
        return str(signal.source_metadata.get("source_reason") or "the engine declined to answer")
    if signal.action is SignalAction.WAIT:
        return str(
            signal.source_metadata.get("source_reason")
            or "a condition the engine states is not met"
        )
    if signal.direction is Direction.FLAT:
        return "the signal names a tradable action but no direction"
    return ""


def _analyzable_bars(bars: object) -> object:
    """The bars in the shape the analyzer accepts.

    ``MT5Feed.closed_bars`` returns a ``FrozenSeries``, while
    ``Analyzer.analyze`` takes ``Sequence[Bar | dict] | BarSeries`` and builds
    a ``BarSeries`` from it -- which iterates its input, so a non-iterable
    ``FrozenSeries`` fails with ``TypeError: 'FrozenSeries' object is not
    iterable``. The engine's own documented flow hands the analyzer
    ``frozen.series``, never the frozen wrapper::

        frozen = feed.closed_bars("EURUSD", M15, 300)
        result = AnalysisSession().on_bars(frozen.series, freeze=frozen.freeze)

    So the frozen series is intentionally the *transport* type and ``.series``
    is what gets analysed. Unwrapping here, at the adapter boundary, rather
    than in the engine: the analyzer contract is unchanged, and anything
    already analyzable (a list, a tuple, a ``BarSeries`` -- none of which
    carries a ``series`` attribute) passes through untouched.
    """
    series = getattr(bars, "series", None)
    if series is None:
        return bars
    return series


def _raw_time_of(bar: object) -> float | None:
    """The bar's time as a float epoch, or ``None`` when it carries none.

    Mappings are read by key, objects by attribute -- the engine's own bars answer
    both, a plain dict only the first. Booleans and non-numeric values are not
    times: ``isinstance(True, int)`` is true, so a naive check would date a bar to
    1970.
    """
    raw_time = bar.get("time") if isinstance(bar, Mapping) else getattr(bar, "time", None)
    if isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool):
        return float(raw_time)
    return None


def _series_len(bars: object) -> int | None:
    """Length of a bar series, or ``None`` when ``bars`` is not a series.

    Strings, bytes and mappings are not series even though they have a length: a
    feed that returned a bare string must not be read as "index N". Anything
    without a length (a generator, ``None``) is refused the same way.
    """
    if isinstance(bars, (str, bytes, bytearray, Mapping)):
        return None
    try:
        return len(bars)  # type: ignore[arg-type]
    except TypeError:
        return None


def _bar_time_at(bars: object, index: int) -> float | None:
    """The time of the bar at ``index``, or ``None`` when it cannot be read.

    Used when the engine reported an index but no time: the reading was made on
    the indexed bar, so its time -- and no other bar's -- is the repair. A
    missing or unreadable time is ``None`` rather than a neighbouring bar's,
    because dating a reading with another bar's time would be a fabrication.
    """
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        return None
    n = _series_len(bars)
    if n is None or index >= n:
        return None
    try:
        bar = bars[index]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    return _raw_time_of(bar)


def _newest_bar(bars: object) -> tuple[int | None, float | None]:
    """The index and close time of the newest bar in a series.

    Read from the **series position**, not from the engine's report: the engine's
    report is exactly what is missing, and the series is what it was given. So this
    is a recovery, not a guess -- and when both are absent the caller refuses.

    Accepts any sized, indexable series -- a list, a tuple, or the engine's own
    ``BarSeries`` (which is a ``Sequence`` but neither a list nor a tuple, and is
    what the adapter actually hands the analyzer after unwrapping the frozen
    transport type). A string or mapping is not a series and is refused.

    The index is the position from the end rather than from the start, because the
    engine indexes bars that way and the two must agree: the newest bar in a
    300-bar window is ``299``, not ``0``. A recovery that disagreed with the engine's
    own convention would put the reading on a bar the engine never saw, which is
    worse than refusing.
    """
    n = _series_len(bars)
    if n is None or n == 0:
        return None, None
    try:
        newest = bars[-1]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None, None

    index = n - 1
    if isinstance(newest, Mapping):
        raw_index = newest.get("index")
        if isinstance(raw_index, int) and not isinstance(raw_index, bool):
            index = raw_index

    return index, _raw_time_of(newest)
