"""Ports: the abstractions the domain is allowed to depend on.

Every protocol here is a boundary. The domain and the application layer depend on
these; the concrete implementations in ``adapters/`` satisfy them. Nothing
inward imports outward.

The ports are deliberately **narrow**. ``AccountProvider`` does not also return
positions, and ``SymbolSpecProvider`` does not also return quotes. That is not
minimalism for its own sake: a test has to fake these, and a fat port means every
fake in every test must implement every method, including the ones that test does
not care about. The cost of a wide port is paid on every test that touches it.

Two of these ports exist because neither upstream project can supply the facts
they need. The price-action engine treats a symbol as an opaque string, and the
execution project drives the MetaTrader order dialog by clicking on it, so it can
only read what a human could see on screen. Between them, neither knows the
account balance or a symbol's contract specification, and without both of those a
position size cannot be computed at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from signal_to_trade_bridge.domain.models import (
        AccountBalance,
        ExecutionRequest,
        ExecutionResult,
        Signal,
        SymbolSpec,
    )

__all__ = [
    "AccountProvider",
    "IdempotencyStore",
    "KillSwitch",
    "MarketDataProvider",
    "SignalSource",
    "SymbolSpecProvider",
    "TradeExecutor",
]


@runtime_checkable
class SignalSource(Protocol):
    """Where trading signals come from.

    One method, returning ``None`` when there is nothing to say. Returning
    ``None`` rather than raising is a real decision: "the engine found no setup"
    is a normal, frequent outcome and not an error, and a source that raised on
    every quiet bar would make the normal case look like a failure.

    Implemented by :class:`~signal_to_trade_bridge.adapters.albrooks.source.AlBrooksSignalSource`,
    which wraps the upstream analyzer.
    """

    def latest_signal(self, symbol: str, timeframe: str) -> Signal | None:
        """The most recent signal for a symbol and timeframe, if there is one."""
        ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Closed bars for analysis.

    ``count`` rather than a time range, because the upstream analysis pipeline
    works over a fixed-length window and a range would invite a caller to ask for
    a series too short to produce a reading.
    """

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> list[Any]:
        """Return up to ``count`` closed bars, oldest first.

        The element type is ``Any`` rather than a concrete model on purpose: this
        port exists so the bridge can hand bars to the upstream engine, which
        defines its own ``Bar``. Naming a bridge type here would either duplicate
        that model or couple the port to a package the domain is meant not to
        import.
        """
        ...


@runtime_checkable
class AccountProvider(Protocol):
    """Account facts needed to size a trade.

    Implemented by the MT5 adapter in production and by a fake in tests. There is
    no other source for these numbers, which is why the bridge cannot delegate
    position sizing upstream.
    """

    def balance(self) -> AccountBalance:
        """The current account state.

        Raises rather than returning a sentinel when the terminal is unreachable.
        The distinction between "the account has no money" and "we could not ask"
        is the difference between refusing a trade and reporting a broken
        connection, and the two must not be collapsed.
        """
        ...


@runtime_checkable
class SymbolSpecProvider(Protocol):
    """A symbol's trading contract.

    Also has no upstream source. The execution project has a ``SymbolInfo`` class
    with three fields, but it is dead code -- never constructed, never read -- and
    it carries no contract size, tick value or volume step in any case.
    """

    def spec(self, symbol: str) -> SymbolSpec:
        """The specification for ``symbol``.

        Raises when the symbol is unknown or not loaded in the terminal. An
        unknown symbol is not a trade with a default size; it is a refusal.
        """
        ...


@runtime_checkable
class TradeExecutor(Protocol):
    """Where orders go.

    The one place the bridge is allowed to cause a position to exist. Everything
    upstream of this is arithmetic and validation.

    Two implementations exist and they are interchangeable:
    :class:`~signal_to_trade_bridge.adapters.auto_trade.executor.AutoTradeExecutor`,
    which delegates to the execution project, and
    :class:`~signal_to_trade_bridge.adapters.fake.executor.FakeTradeExecutor`,
    which records and returns. A test asserting on the fake's recorded orders
    exercises the real pipeline, because the pipeline cannot tell them apart.
    """

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        """Send one order and report what happened.

        Returns a result rather than raising for an ordinary refusal: refusing is
        a valid outcome, not an exception. An implementation should raise only
        when it genuinely cannot proceed, and the bridge treats an unexpected
        exception as ``UNKNOWN`` rather than as a rejection.
        """
        ...


@runtime_checkable
class IdempotencyStore(Protocol):
    """Which signals have already been acted on.

    Backed in production by the execution project's own JSON ledger, which is
    durable, atomic and cross-process, and which the execution workflow already
    consults. The bridge does not build a second store: two stores would be two
    sources of truth, and the one that mattered least would be the one that
    decided whether a duplicate trade happened.

    The key is the signal's deterministic identity, not a fresh identifier. A
    store keyed on something generated per call would never match on a
    re-delivery, and the whole mechanism would pass its tests while doing nothing
    in production.

    ### Why two methods and not one

    This was a single ``record(key, mapping)`` until Phase 9, and that shape cannot
    express what the real ledger does. Upstream's ``JsonExecutionLedger`` writes
    ``REQUESTED`` **before** the click and the outcome **after** it, and that gap is
    the whole mechanism: a process that dies mid-attempt leaves a pending record an
    operator can settle, rather than no record at all. A single call collapses the
    two moments, and the collapse is invisible until the first crash — at which point
    the ledger says the signal was never attempted and the trade is free to repeat.

    ``execution_id`` is what ties the two halves to one attempt. It is generated per
    attempt, and the ledger refuses to overwrite an entry whose id differs, so a
    second attempt cannot quietly take over the first one's record.
    """

    def contains(self, key: str) -> bool:
        """Whether this key has already been recorded.

        **Any record counts, whatever its outcome.** Upstream's ledger is keyed on the
        signal id and does not distinguish a filled attempt from a refused one, so a
        signal id that has been acted on is not offered again. That is stricter than
        :attr:`~domain.models.ExecutionResult.is_retryable`, which describes what a
        caller may do with a result it holds rather than what a *new* attempt would
        meet — see the note in ``docs/risk-management.md``.
        """
        ...

    def record_attempt(self, key: str, execution_id: str) -> None:
        """Record that an attempt on this key has begun, before anything is sent.

        Must be durable before it returns. The window it opens is the recoverable
        one: a crash after this call and before the outcome is recorded leaves a
        pending entry rather than silence.
        """
        ...

    def record_outcome(self, key: str, execution_id: str, outcome: Mapping[str, Any]) -> None:
        """Record how the attempt turned out.

        ``execution_id`` must match the one passed to :meth:`record_attempt`, and a
        mismatched id must not overwrite the entry. ``outcome`` carries the status
        and whatever else the implementation persists; its shape belongs to the
        ledger, so it is a mapping rather than a bridge type the ledger would have
        to import.
        """
        ...


@runtime_checkable
class KillSwitch(Protocol):
    """Whether an emergency stop is engaged.

    Separate from ``IdempotencyStore`` and from the execution policy, because a
    kill switch that had to be consulted through a ledger could not stop anything
    while the ledger was unavailable.
    """

    @property
    def active(self) -> bool:
        """Whether trading is halted."""
        ...
