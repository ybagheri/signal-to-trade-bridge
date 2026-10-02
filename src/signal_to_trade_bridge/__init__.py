"""Public API. The surface a consumer is meant to use.

Phase 12 added this, which is a thing worth being honest about: **twelve phases
shipped a working library with no public API.** Every module was importable and
every type was documented, but nothing declared what a caller should reach for, so
"what does this project expose" was answered by reading `src/`. That is a poor answer
to a question a dependency gets asked on its first day.

So the list below is short on purpose. It is the vocabulary, not the implementation:
a caller assembles a :class:`~signal_to_trade_bridge.composition.BridgeConfig`,
builds a :class:`~signal_to_trade_bridge.composition.Bridge`, and processes signals.
Everything else is reachable if you want it, and importing from here means you have
chosen a thing meant to be stable.

### What is deliberately not exported

* **domain internals** -- ``resolve_stop``, ``size_position`` and the rest are
  reachable and tested, but they are implementation. Exporting them would make
  changing a policy look like a breaking change.
* **adapter constructors** -- available from their own modules for a caller who is
  building something unusual, but the composition root is the supported path. Two
  documented ways to assemble a bridge is one way too many.
* **anything from ``auto_trade`` or ``MetaTrader5``** -- both are private or
  optional, and a public API that leaked either would break the moment a machine
  lacked it. The lazy import is the design; this list is where it is honoured.
"""

from signal_to_trade_bridge.composition import (
    Bridge,
    BridgeConfig,
    CompositionRefusal,
    build_bridge,
)
from signal_to_trade_bridge.domain.enums import (
    DecisionAction,
    Direction,
    RejectionReason,
    SignalAction,
)
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionRequest,
    ExecutionResult,
    RiskParameters,
    Signal,
    SymbolSpec,
    TradeDecision,
    TradeIntent,
)
from signal_to_trade_bridge.version import __version__

__all__ = [
    "AccountBalance",
    "Bridge",
    "BridgeConfig",
    "CompositionRefusal",
    "DecisionAction",
    "Direction",
    "ExecutionRequest",
    "ExecutionResult",
    "RejectionReason",
    "RiskParameters",
    "Signal",
    "SignalAction",
    "SymbolSpec",
    "TradeDecision",
    "TradeIntent",
    "__version__",
    "build_bridge",
]
