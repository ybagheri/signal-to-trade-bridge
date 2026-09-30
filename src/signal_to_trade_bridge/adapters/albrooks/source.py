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

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol, cast

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
                bars, symbol=normal_symbol, timeframe=timeframe
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
        self._log_outcome(normal_symbol, timeframe, outcome)
        return outcome

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
