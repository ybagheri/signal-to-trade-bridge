"""The single import site for the execution project's private package.

`auto_trade` is a private repository, is not on PyPI, and is not installed in this
repository's test environment. So it is imported the way
:mod:`signal_to_trade_bridge.adapters.mt5.bindings` imports ``MetaTrader5``:
**once, lazily, through a function**, and behind a declared subset rather than
``Any``.

The alternative -- importing `auto_trade` at module level in the executor --
would make this package unimportable on any machine without the checkout, and the
whole test suite would go with it. That is not a convenience argument. The suite
running without the upstreams is the property that lets the risk arithmetic be
verified by anyone, on any machine, and a convenience import that quietly makes
the safety-critical half untestable is a bad trade.

### The subset, and why it is exactly this subset

Six names, and every one of them was read out of the upstream source rather than
guessed:

``ExecutionWorkflow``
    The thing being wrapped. It is not reimplemented here. It carries the state
    machine, the ledger ordering and the ``UNKNOWN`` classification, all of which
    are the reason the bridge delegates instead of executing.

``TradeSignal`` / ``OrderAction``
    The input. ``OrderAction`` is needed because ``TradeSignal.action`` is typed
    as the enum, not as a string.

``ExecutionResult`` / ``ExecutionStatus``
    The output. ``ExecutionStatus`` is needed because the bridge reads
    ``result.status.value`` rather than trusting a string shape.

``AutoTradeError``
    The base of every exception the upstream raises, including
    ``ExecutionUnknownError`` -- which **escapes** ``ExecutionWorkflow.execute``
    rather than being returned as a result. Catching the base class is what
    turns that escape into an ``UNKNOWN`` the bridge can record instead of a
    crash that loses the trade record.

``KillSwitch``
    Not imported from ``application`` -- upstream's concrete base class lives in
    ``domain.protocols`` and is not re-exported from ``domain``. It is bound here
    so the safety envelope can be assembled and tested without the package.

### What is deliberately absent

``ExecutionPolicy``, ``RiskEngine``, ``JsonExecutionLedger`` and ``MT5DesktopAdapter``
are **not** bound. The first three are assembly concerns belonging to the
composition root, and binding them here would give the adapter a second opinion
about configuration. The fourth is the thing that clicks, and nothing in this
package may construct or reach it -- see :mod:`.executor`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["AutoTradeBindings", "AutoTradeUnavailable", "load_bindings"]


class AutoTradeUnavailable(RuntimeError):
    """The execution project could not be loaded, or does not look as expected.

    A ``RuntimeError``, and deliberately **not** a domain error, for the same
    reason :class:`~signal_to_trade_bridge.adapters.mt5.bindings.MT5Unavailable`
    is: by the time this is raised the bridge is outside its own vocabulary, and
    its error vocabulary describes *trading refusals*. "The execution package is
    not installed" is a fact about the machine, not a verdict on a trade.

    The two causes it covers -- the package is absent, and the package is present
    but has changed shape -- reach the bridge as the same ``UNKNOWN``, but the
    operator needs to be told which, because the remedy differs: install the
    checkout, or fix the binding.
    """


def _resolves(path: str) -> bool:
    """Whether an ``auto_trade``-relative dotted path names something real.

    The paths in :attr:`AutoTradeBindings.REQUIRED` are written relative to the
    package root, because that is the readable form -- ``domain.models.TradeSignal``
    rather than ``auto_trade.domain.models.TradeSignal`` -- and the prefix is
    applied here, once, rather than in eight strings that would all have to be
    edited together if the import root ever changed.

    ``importlib.import_module`` caches, so this is a lookup rather than a second
    load.
    """
    import importlib

    module_name, _, attribute = path.rpartition(".")
    try:
        return getattr(importlib.import_module(f"{_ROOT}.{module_name}"), attribute) is not None
    except (ImportError, AttributeError):
        return False


#: The distribution's import name. Upstream's package directory is ``auto_trade``
#: while its project name is ``auto-trade``; this is the one place that difference
#: is written down.
_ROOT = "auto_trade"


@dataclass(frozen=True, slots=True)
class AutoTradeBindings:
    """The upstream names this adapter is allowed to see.

    Frozen, and every attribute required: a bindings object that could be built
    partially would let a test pass against a subset of the real API, which is
    the way an adapter ends up working in tests and failing in production.

    ``module`` is kept so a caller can reach a name that is deliberately not
    bound here, with the import made visible at the call site rather than hidden
    behind a permissive ``__getattr__``.

    **The subset is not verified on construction, and that is deliberate.**
    ``__post_init__`` does not check that these names resolve, because that check
    imports the real package -- and a dataclass that imports on construction
    cannot be built from doubles, which would make the mapping untestable on
    exactly the machine where it most needs testing.
    :meth:`verify` does the check and :func:`load_bindings` calls it, so the
    guarantee holds on the path that matters and the suite stays runnable without
    the checkout.
    """

    module: Any
    ExecutionWorkflow: Any
    TradeSignal: Any
    OrderAction: Any
    ExecutionResult: Any
    ExecutionStatus: Any
    AutoTradeError: Any
    KillSwitch: Any

    #: Where each name genuinely lives upstream. Upstream has no top-level
    #: ``__all__`` and re-exports nothing from its root, so every name has to come
    #: from the subpackage that actually defines it -- and a rename in any of them
    #: is the failure mode this table exists to name.
    REQUIRED: tuple[str, ...] = (
        "application.workflow.ExecutionWorkflow",
        "domain.models.TradeSignal",
        "domain.models.OrderRequest",
        "domain.enums.OrderAction",
        "domain.models.ExecutionResult",
        "domain.enums.ExecutionStatus",
        "domain.exceptions.AutoTradeError",
        "domain.protocols.KillSwitch",
    )

    def verify(self) -> AutoTradeBindings:
        """Raise unless every name in :attr:`REQUIRED` actually exists.

        The subset is a claim about a private package's API, and a claim should be
        checkable. Upstream has changed shape before, so a missing name has to fail
        here with a sentence naming which one -- rather than as an
        ``AttributeError`` from three frames inside the adapter, where the cause
        reads as a mistake rather than as an upstream rename.
        """
        missing = [path for path in self.REQUIRED if not _resolves(path)]
        if missing:
            raise AutoTradeUnavailable(
                f"auto_trade is installed but does not expose {', '.join(missing)}. The "
                f"upstream package is private and has changed shape before -- it has no "
                f"top-level __all__ and re-exports nothing from its root -- so an adapter "
                f"written against an older reading of it would import cleanly and then fail "
                f"here. Fix the binding rather than the adapter."
            )
        return self

    def order_action(self, name: str) -> Any:
        """The upstream ``OrderAction`` member for ``name``.

        A lookup through the enum rather than a bare subscript at the call site, so
        the failure names the bridge's own vocabulary instead of upstream's, and so
        an unrecognised direction cannot become a silent ``KeyError`` three frames
        away.
        """
        try:
            return self.OrderAction[str(name).strip().upper()]
        except KeyError as exc:
            raise AutoTradeUnavailable(
                f"the execution project has no order action {name!r}. This bridge only "
                f"maps a tradable direction, so reaching this means the direction enum and "
                f"the upstream vocabulary have diverged."
            ) from exc


def load_bindings() -> AutoTradeBindings:
    """Import the execution project and return the subset this adapter uses.

    Called lazily, never at module level, so importing this package on a machine
    without the checkout succeeds and only *using* the executor fails.
    """
    try:
        import auto_trade
        from auto_trade.application.workflow import ExecutionWorkflow
        from auto_trade.domain.enums import ExecutionStatus, OrderAction
        from auto_trade.domain.exceptions import AutoTradeError
        from auto_trade.domain.models import ExecutionResult, TradeSignal
        from auto_trade.domain.protocols import KillSwitch
    except ImportError as exc:
        raise AutoTradeUnavailable(
            f"the execution project could not be imported: {exc}. It is a private "
            f"repository with no PyPI release, so it has to be installed from a local "
            f"checkout; point scripts/setup.ps1 at one, or set AUTO_TRADE_PATH and run it."
        ) from exc

    return AutoTradeBindings(
        module=auto_trade,
        ExecutionWorkflow=ExecutionWorkflow,
        TradeSignal=TradeSignal,
        OrderAction=OrderAction,
        ExecutionResult=ExecutionResult,
        ExecutionStatus=ExecutionStatus,
        AutoTradeError=AutoTradeError,
        KillSwitch=KillSwitch,
    ).verify()
