"""Fakes: real implementations of the ports, for tests.

These are adapters, not test fixtures, and they are in the package rather than in
``tests/`` for one reason: **a fake implements the same port as the real thing.**
A fixture that returned a tuple would test a function that does not exist in
production; this returns an :class:`~signal_to_trade_bridge.domain.models.SymbolSpec`
through
:class:`~signal_to_trade_bridge.ports.SymbolSpecProvider`, so a test written
against it exercises the same call path the MetaTrader terminal will drive.

That also makes them the specification for Phase 7. When the real MT5 adapter is
written, the contract it has to meet is already pinned by tests that pass today,
and the difference between the two implementations is a terminal rather than an
argument.

Two rules both of these obey, because both are what the ports specify and a fake
that broke them would leave the real adapter's hardest cases untested:

* **An unreachable source raises.** It does not return a sentinel, a default or
  a remembered last-known-good value. "The account has no money" and "we could
  not ask" are different answers, and collapsing them is how a system ends up
  sizing trades against a stale balance.
* **An unknown symbol raises.** It does not return a default specification. A
  default would be an invented contract, which is the one thing the Phase 0
  audit established that neither upstream project has and this project refuses
  to invent.
"""

from signal_to_trade_bridge.adapters.fake.account import FakeAccountProvider
from signal_to_trade_bridge.adapters.fake.symbols import (
    FakeSymbolSpecProvider,
    eurusd_spec,
    gold_spec,
    unusual_spec,
)

__all__ = [
    "FakeAccountProvider",
    "FakeSymbolSpecProvider",
    "eurusd_spec",
    "gold_spec",
    "unusual_spec",
]
