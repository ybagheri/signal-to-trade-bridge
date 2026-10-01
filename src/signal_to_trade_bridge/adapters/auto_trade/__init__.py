"""The execution adapter.

One class, and it is a wrapper rather than a reimplementation:
:class:`~signal_to_trade_bridge.adapters.auto_trade.executor.AutoTradeExecutor`
translates this bridge's :class:`~signal_to_trade_bridge.domain.models.ExecutionRequest`
into the execution project's ``TradeSignal``, calls its real
``ExecutionWorkflow``, and translates the answer back.

**Why a wrapper and not a port implementation of our own.** The execution project
already owns the state machine, the ledger ordering, the kill-switch check and the
classification of a failed click as ``UNKNOWN``. Those are exactly the mechanisms
that stand between a decided trade and a duplicate position. Re-implementing any
of them would be a second opinion about whether a trade happened, and the whole
reason this project has refused that shape of work through six phases is that it
produces code which looks finished and disagrees with the thing it replaced.

**What is not here, deliberately.**

* **No ``MT5DesktopAdapter``**, the class that clicks. It is bound in the
  composition root. An adapter that could construct its own terminal adapter could
  construct one with the execution gate enabled, and the two independent guards
  -- the workflow's ``dry_run`` policy and the adapter's ``ExecutionGate`` -- are
  what together make a dry run provably unable to reach a final control.
* **No ``ProcessSignal`` wiring.** Execution arrives with the idempotency ledger
  and the kill switch around it, not before, and the structural test asserting
  the pipeline imports no executor is still passing on purpose.
* **No risk, ledger or policy assembly.** Those belong to the composition root;
  binding them here would give the adapter a second opinion about configuration.

The one import of the private upstream package lives in :mod:`.bindings`, behind a
function, so this package imports on a machine that does not have the checkout.
"""

from signal_to_trade_bridge.adapters.auto_trade.bindings import (
    AutoTradeBindings,
    AutoTradeUnavailable,
    load_bindings,
)
from signal_to_trade_bridge.adapters.auto_trade.executor import (
    SENTINEL_PROFILE,
    AutoTradeExecutor,
)
from signal_to_trade_bridge.adapters.auto_trade.ledger import (
    AutoTradeLedger,
    open_ledger,
)
from signal_to_trade_bridge.adapters.auto_trade.preflight import (
    DownstreamLimits,
    ask_downstream_risk,
)

__all__ = [
    "SENTINEL_PROFILE",
    "AutoTradeBindings",
    "AutoTradeExecutor",
    "AutoTradeLedger",
    "AutoTradeUnavailable",
    "DownstreamLimits",
    "ask_downstream_risk",
    "load_bindings",
    "open_ledger",
]
