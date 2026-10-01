"""The MetaTrader 5 data adapter.

Three providers, reading the three facts neither upstream project has: the account
balance, a symbol's trading contract, and the number of open positions — the last
of which the terminal itself publishes as a JSON snapshot written by
`auto-trade`'s own read-only indicator. Between them they are the last thing
standing between the decision pipeline and real market data — the account and
symbol facts the Phase 0 audit found **nowhere** in either upstream tree.

The pattern is the one this package established in Phase 7, and it is the reason
it is testable on a machine with no MetaTrader 5:

* **the module is injected**, so every branch is testable without a terminal and
  `MetaTrader5` never appears in a test file;
* **the import lives in one function** in :mod:`.bindings`, lazily, so every other
  module imports on a machine that has never installed the bindings;
* **the subset used is declared** as a ``Protocol``, so the boundary is typed
  rather than unchecked `Any`;
* **the terminal path is a parameter,** never a constant, so no machine's path is
  committed — and the position reader *requires* it rather than defaulting, because
  a default would be the `AUTO_TRADE_DATA_PATH` mistake the Phase 0 audit recorded;
* **nothing launches the terminal.** ``initialize`` is called; ``launch`` is not.
  The terminal must already be running and logged in.

**What is not here, and why.** `adapters/auto_trade/` is still empty, and
`TradeExecutor` is still unimplemented. The `auto-trade` checkout is now available
at `D:\\Projects\auto-trade` and its contracts have been re-read against the source,
so the remaining reason to wait is gone — this is simply the next piece of work.
See `HANDOFF.md` for exactly what it must contain.
"""

from signal_to_trade_bridge.adapters.mt5.account import MT5AccountProvider
from signal_to_trade_bridge.adapters.mt5.bindings import (
    MT5AccountInfo,
    MT5Bindings,
    MT5SymbolInfo,
    MT5Unavailable,
    load_bindings,
)
from signal_to_trade_bridge.adapters.mt5.positions import (
    DEFAULT_MAX_AGE,
    SUPPORTED_SCHEMA,
    MT5PositionReader,
    ObservedPosition,
    PositionSnapshot,
    parse_snapshot,
)
from signal_to_trade_bridge.adapters.mt5.symbols import MT5SymbolSpecProvider

__all__ = [
    "DEFAULT_MAX_AGE",
    "SUPPORTED_SCHEMA",
    "MT5AccountInfo",
    "MT5AccountProvider",
    "MT5Bindings",
    "MT5PositionReader",
    "MT5SymbolInfo",
    "MT5SymbolSpecProvider",
    "MT5Unavailable",
    "ObservedPosition",
    "PositionSnapshot",
    "load_bindings",
    "parse_snapshot",
]
