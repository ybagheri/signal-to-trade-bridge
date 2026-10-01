"""The MetaTrader 5 data adapter.

Two providers, reading the two facts neither upstream project has: the account
balance, and a symbol's trading contract. Between them they are the last thing
standing between the decision pipeline and real market data — the account and
symbol facts the Phase 0 audit found **nowhere** in either upstream tree.

The pattern is the one `albrooks.adapters.mt5.MT5Feed` already demonstrates, and
it is the reason this package is testable on a machine with no MetaTrader 5:

* **the module is injected**, so every branch is testable without a terminal and
  `MetaTrader5` never appears in a test file;
* **the import lives in one function** in :mod:`.bindings`, lazily, so every other
  module imports on a machine that has never installed the bindings;
* **the subset used is declared** as a ``Protocol``, so the boundary is typed
  rather than unchecked `Any`;
* **the terminal path is a parameter**, never a constant, so no machine's path is
  committed;
* **nothing launches the terminal.** ``initialize`` is called; ``launch`` is not.
  The terminal must already be running and logged in.

**What is not here, and why.** `adapters/auto_trade/` is still empty, and
`TradeExecutor` is still unimplemented: the execution adapter must wrap
`auto-trade`'s real `ExecutionWorkflow`, and that package is a private repository
which is not installed on this machine. Writing it against a reading of its
documentation would be code that looks finished and has never been checked against
the thing it calls. See `HANDOFF.md` for where that work is recorded.
"""

from signal_to_trade_bridge.adapters.mt5.account import MT5AccountProvider
from signal_to_trade_bridge.adapters.mt5.bindings import (
    MT5AccountInfo,
    MT5Bindings,
    MT5SymbolInfo,
    MT5Unavailable,
    load_bindings,
)
from signal_to_trade_bridge.adapters.mt5.symbols import MT5SymbolSpecProvider

__all__ = [
    "MT5AccountInfo",
    "MT5AccountProvider",
    "MT5Bindings",
    "MT5SymbolInfo",
    "MT5SymbolSpecProvider",
    "MT5Unavailable",
    "load_bindings",
]
