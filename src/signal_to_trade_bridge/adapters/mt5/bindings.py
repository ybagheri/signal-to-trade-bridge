"""The MetaTrader 5 bindings, and the only place they are imported.

Three things live here, and nothing else may:

1. :class:`MT5Bindings` -- a ``Protocol`` declaring the *subset* of the
   ``MetaTrader5`` module this project uses. The real module satisfies it
   structurally; so does a hand-written fake in a test.
2. :func:`load_bindings` -- the one function in the project that performs
   ``import MetaTrader5``, lazily, with an error that names the fix.
3. :class:`MT5Unavailable` -- the error the rest of the adapter raises when the
   terminal cannot be reached.

**Why a Protocol rather than ``Any``.** The bindings ship no type information, so
``import MetaTrader5`` resolves to an untyped module and every attribute read
through it is unchecked -- which would mean the boundary that converts untyped
third-party data into typed domain objects is itself untyped. The Protocol is the
claim this project actually relies on: *these are the calls we make, and this is
what they return.* It is verified at runtime, in a test, rather than asserted.

**Why the import is inside a function.** So ``import signal_to_trade_bridge`` and
``import signal_to_trade_bridge.adapters.mt5.account`` both work on a machine that
has never installed the bindings. The test suite runs on a laptop with no MetaTrader
5 and no upstream checkout, and an adapter that could not be imported would be an
adapter that could not be tested there.

**Nothing here launches the terminal.** ``initialize`` is called; ``launch`` never
is. ``BridgeConfig.require_running_terminal`` defaults to true for the same reason:
a process that starts a trading terminal on import is a process that can start it by
accident. The terminal must already be running and logged in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast, runtime_checkable

__all__ = ["MT5Bindings", "MT5Unavailable", "load_bindings"]


class MT5Unavailable(RuntimeError):
    """The terminal could not be reached, or a fact could not be read from it.

    A ``RuntimeError`` rather than a domain error on purpose. By the time this is
    raised the bridge is outside the domain entirely: the domain's own error
    vocabulary describes *trading* refusals, and "the terminal is not running" is
    not a verdict on a trade — it is a fact about the machine.

    It is still an exception rather than a sentinel, because that is what the ports
    specify. ``AccountProvider.balance`` and ``SymbolSpecProvider.spec`` both say
    they raise when the terminal is unreachable, and the distinction between "the
    account has no money" and "we could not ask" is the difference between refusing
    a trade and reporting a broken connection. A fake that quietly returned a
    default would leave the real adapter's hardest case untested.
    """


@runtime_checkable
class MT5AccountInfo(Protocol):
    """The subset of ``account_info()``'s result this project reads.

    Named fields, because the bindings return a named tuple. The field names are
    MT5's own, and a rename upstream is a change this Protocol should fail on
    rather than absorb.
    """

    @property
    def balance(self) -> float: ...

    @property
    def equity(self) -> float: ...

    @property
    def currency(self) -> str: ...

    @property
    def login(self) -> int: ...

    @property
    def name(self) -> str: ...


@runtime_checkable
class MT5SymbolInfo(Protocol):
    """The subset of ``symbol_info()``'s result that becomes a :class:`SymbolSpec`.

    Every field here is one the domain's sizing arithmetic consumes, and the names
    are MT5's own so the mapping stays obvious. The three currency fields are
    included because the currency-coherence check refuses an unstated profit
    currency, which is only answerable if the adapter read it.
    """

    @property
    def trade_contract_size(self) -> float: ...

    @property
    def trade_tick_size(self) -> float: ...

    @property
    def trade_tick_value_profit(self) -> float: ...

    @property
    def trade_tick_value_loss(self) -> float: ...

    @property
    def volume_min(self) -> float: ...

    @property
    def volume_max(self) -> float: ...

    @property
    def volume_step(self) -> float: ...

    @property
    def digits(self) -> int: ...

    @property
    def point(self) -> float: ...

    @property
    def currency_base(self) -> str: ...

    @property
    def currency_profit(self) -> str: ...

    @property
    def currency_margin(self) -> str: ...


@runtime_checkable
class MT5Bindings(Protocol):
    """The calls this project makes on the ``MetaTrader5`` module.

    Four methods, and four is all. Every one of them has a documented failure mode
    the adapter handles, which is what keeps this list short: a call whose failure
    the adapter would ignore has no business being here.
    """

    def initialize(self, path: str | None = None) -> bool: ...

    def shutdown(self) -> None: ...

    def account_info(self) -> MT5AccountInfo | None: ...

    def symbol_info(self, symbol: str) -> MT5SymbolInfo | None: ...


def load_bindings(terminal_path: Path | None = None) -> MT5Bindings:
    """The real bindings, connected, or :class:`MT5Unavailable`.

    ``terminal_path`` is a parameter and never a constant, for the same reason
    ``MT5Feed.connect`` takes one: nothing in this project may name a machine's
    terminal path, so there is no default that is right on more than one laptop.
    ``None`` means "let the bindings find it", which is the right behaviour on a
    machine with one terminal and the wrong one on a machine with several.

    The error names the package *and* the reason, because "No module named
    MetaTrader5" tells an operator nothing about what to do next. It is Windows-only
    by nature, and so is a terminal, and so is this adapter.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:  # pragma: no cover - needs the bindings absent
        raise MT5Unavailable(
            "the MetaTrader5 package is not installed, so the account and symbol facts "
            "cannot be read from a terminal. It is Windows-only and ships with MetaTrader 5; "
            "install it with: pip install 'signal-to-trade-bridge[windows]'. Everything "
            "outside the MT5 adapter runs without it."
        ) from exc

    bindings = cast("MT5Bindings", mt5)
    path = str(terminal_path) if terminal_path is not None else None
    try:
        connected = bindings.initialize(path)
    except Exception as exc:  # pragma: no cover - the bindings raise several types
        raise MT5Unavailable(
            f"the MetaTrader 5 bindings refused to initialise: {type(exc).__name__}: {exc}"
        ) from exc

    if not connected:
        # `last_error()` is deliberately not consulted: it is a tuple of two ints
        # from an API this project has no contract with, and turning it into a
        # message would be inventing meaning. What is known is that the terminal
        # was not reachable, and that is what the error says.
        raise MT5Unavailable(
            "the MetaTrader 5 bindings could not initialise, which means the terminal was "
            "not reachable. Start MetaTrader 5, log in, and try again. This project never "
            f"launches a terminal itself. terminal_path={path!r}"
        )
    return bindings
