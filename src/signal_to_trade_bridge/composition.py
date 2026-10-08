"""The composition root: everything the bridge needs, assembled once.

Phase 10. Nine phases built the pieces and each was tested against a stand-in. This
is the first place they are wired to each other, and the first place the *order* of
the wiring is a design decision rather than an accident.

### The rule this module exists to enforce

**The default configuration cannot execute, and neither can a partial assembly.**

``BridgeConfig.execution_enabled`` defaults to ``False``. This module honours that
and refuses to hand back anything that would place an order unless the caller has
both set the flag *and* disabled dry-run. So a checkout configured by accident, or by
a stray ``.env``, gets a pipeline that can only report.

### Why the order is the design

```
1. the MT5 bindings           the facts only a terminal has
2. account, symbols, reader   the account snapshot and the contract
3. the ledger                 opened BEFORE anything that could write
4. the pipeline               wired only if execution is live
```

**The ledger is opened before anything that writes.** ``JsonExecutionLedger`` raises
on a file it cannot read, and an unreadable ledger must stop the process before it
has had a chance to place anything -- not after. Opening it first means the refusal
happens while there is still nothing to undo.

### What is deliberately not assembled here

* **The terminal adapter that clicks, and the ``ExecutionWorkflow`` with it.**
  Building them requires importing the private execution package, and on a machine
  without it that would make a *dry run* impossible -- the two are independent, and
  conflating them would mean a missing dependency silently disables reporting
  rather than trading. Phase 11 assembles the live side; this module refuses to
  pretend to.
* **The signal source.** The bridge reads signals; where they come from is the
  caller's business, and a composition root that also chose the strategy would be a
  strategy in disguise.
* **A CLI.** ``cli/`` stays empty until Phase 12. This is a library, and the command
  surface is a separate decision about arguments and exit codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from signal_to_trade_bridge.adapters.auto_trade import (
    AutoTradeBindings,
    AutoTradeUnavailable,
    load_bindings,
    open_ledger,
)
from signal_to_trade_bridge.adapters.auto_trade.preflight import (
    DownstreamLimits,
    ask_downstream_risk,
)
from signal_to_trade_bridge.adapters.mt5 import (
    MT5AccountProvider,
    MT5Bindings,
    MT5PositionReader,
    MT5SymbolSpecProvider,
)
from signal_to_trade_bridge.adapters.mt5 import load_bindings as load_mt5_bindings
from signal_to_trade_bridge.application.process_signal import ProcessSignal
from signal_to_trade_bridge.application.risk_service import RiskService
from signal_to_trade_bridge.configuration.config import BridgeConfig

__all__ = ["Bridge", "CompositionRefusal", "build_bridge"]


class CompositionRefusal(RuntimeError):
    """The bridge could not be assembled, or must not be.

    A ``RuntimeError`` and not a domain error, for the same reason
    :class:`~signal_to_trade_bridge.adapters.mt5.bindings.MT5Unavailable` is: by the
    time this is raised the bridge is outside its own vocabulary, whose error codes
    describe *trading refusals*. "The ledger cannot be read" is a fact about the
    machine, not a verdict on a trade.
    """


@dataclass(frozen=True, slots=True)
class Bridge:
    """Everything assembled, and nothing that reaches outside the process.

    ``pipeline`` is what a caller normally wants. The rest is exposed because a
    composition root that hides what it built cannot be inspected, and an
    uninspectable assembly is where a wrong wiring hides.

    ``bindings`` is ``None`` on a machine without the execution package. That is a
    normal state for a dry run -- the report then says the downstream gates were not
    evaluated, rather than showing a green tick on a gate nobody asked.
    """

    pipeline: ProcessSignal
    risk: RiskService
    account: MT5AccountProvider
    symbols: MT5SymbolSpecProvider
    ledger: Any
    #: Whether this bridge can actually place an order. ``False`` on the default
    #: configuration, and the reason the whole wiring is behind that check.
    can_execute: bool
    #: Why it cannot, when it cannot. Empty when it can.
    refusal: str = ""
    bindings: AutoTradeBindings | None = None


def build_bridge(
    config: BridgeConfig,
    *,
    mt5_bindings: MT5Bindings | None = None,
    auto_trade_bindings: AutoTradeBindings | None = None,
    ledger: Any | None = None,
) -> Bridge:
    """Assemble the bridge, or refuse.

    Both binding sets and the idempotency ledger are injectable so a test can drive
    the whole assembly with no terminal and no private package. **A supplied set or
    ledger is not re-verified** -- ``load_bindings`` verifies on the real path, and a
    caller bringing its own is trusted deliberately, so this stays a library function
    rather than a test hook. A supplied ``ledger`` must satisfy
    :class:`~signal_to_trade_bridge.ports.IdempotencyStore`; when omitted, the
    execution project's own ledger is opened (or the assembly refuses), exactly as
    before.

    :raises CompositionRefusal: on anything that would make the assembly unsafe.
    """
    bindings = _auto_trade_bindings(auto_trade_bindings)
    account = MT5AccountProvider(
        _mt5_bindings(config, mt5_bindings),
        terminal_path=config.mt5_terminal_path,
        position_reader=_position_reader(config),
    )
    symbols = MT5SymbolSpecProvider(_mt5_bindings(config, mt5_bindings))
    risk = RiskService(account, symbols)

    # Opened before anything that writes, and refused rather than replaced: a ledger
    # that cannot be read must stop the process while there is still nothing to undo.
    if ledger is None:
        ledger = resolve_ledger(config, bindings)

    pipeline = ProcessSignal(
        risk,
        config,
        ask_downstream=_ask_downstream(config, bindings),
    )

    live = config.execution_enabled and not config.dry_run
    if live:
        # Refused here rather than degraded. An operator who enabled execution and
        # silently got a dry run would not know their setting was ignored, and the
        # next thing to happen would be a surprise.
        raise CompositionRefusal(
            "execution is enabled and dry-run is off, so this bridge would place real "
            "orders -- but the live execution side is assembled by Phase 11 and not yet "
            "by this module. Refusing rather than degrading to a dry run, because a silent "
            "degradation is how an operator's setting gets ignored. This refusal is the "
            "reason the default configuration cannot trade."
        )

    return Bridge(
        pipeline=pipeline,
        risk=risk,
        account=account,
        symbols=symbols,
        ledger=ledger,
        can_execute=False,
        refusal=_refusal(config),
        bindings=bindings,
    )


# -- the pieces -------------------------------------------------------------


def _mt5_bindings(config: BridgeConfig, supplied: MT5Bindings | None) -> MT5Bindings:
    if supplied is not None:
        return supplied
    try:
        return load_mt5_bindings(config.mt5_terminal_path)
    except Exception as exc:
        raise CompositionRefusal(
            f"the MetaTrader 5 bindings could not be loaded: {type(exc).__name__}: {exc}. "
            f"The bridge reads account and symbol facts from a running terminal, and "
            f"without them no trade can be sized."
        ) from exc


def _position_reader(config: BridgeConfig) -> MT5PositionReader | None:
    """The open-position count's source, or ``None`` if no data path is configured.

    ``None`` is not a failure. Without a data path the count is zero, and the
    consequence -- a configured ``BRIDGE_MAX_OPEN_POSITIONS`` reading zero and
    admitting a trade -- is recorded in ``docs/risk-management.md`` as the reason
    leaving that setting unset is the safe one. Refusing the whole assembly would be
    worse: it would turn a missing optional feature into a total outage, on a
    machine that may have no indicator attached and never will.
    """
    if config.mt5_data_path is None:
        return None
    return MT5PositionReader(config.mt5_data_path)


def _auto_trade_bindings(supplied: AutoTradeBindings | None) -> AutoTradeBindings | None:
    if supplied is not None:
        return supplied
    try:
        return load_bindings()
    except Exception:
        return None


def recording_executor(pre_submit_delay: Any | None = None) -> Any:
    """A :class:`~signal_to_trade_bridge.ports.TradeExecutor` that records instead of trading.

    **Here, in the composition root, because that is the only layer allowed to know
    both the live executor and the fake one.** The CLI needs a recorder for
    `--what-if`, and reaching into `adapters/fake/` for it broke the layering rule
    that ``tests/unit/test_layering.py`` enforces -- correctly. An interface layer
    importing an adapter to get a double is how a preview turns into a second
    production path, and the rule is worth more than the convenience.

    So the wiring asks here, and the CLI just says "give me something that records".

    :param pre_submit_delay: the pause policy the recorder mirrors. A preview
        records the pause the real executor would have taken -- without taking
        it, because a preview that slept would be UI pacing with no UI.
    """
    from signal_to_trade_bridge.adapters.fake.executor import FakeTradeExecutor

    return FakeTradeExecutor(pre_submit_delay=pre_submit_delay)


def resolve_ledger(config: BridgeConfig, bindings: AutoTradeBindings | None) -> Any:
    """The idempotency ledger, or a refusal.

    Opened even on a dry run, and deliberately: a dry run is exactly when somebody
    wants to know the ledger is readable, and finding out at the moment the first
    real order is attempted is the worst time to discover it is not.

    **Public, and named without an underscore, because `live.py` needs it.** It was
    `_ledger` and `live.py` imported the private name, which is the kind of coupling
    that looks harmless and then quietly becomes load-bearing: renaming or
    restructuring this function would have broken the live assembly with no test
    pointing at the seam. Making it public says what it actually is -- the single
    place that decides whether an idempotency store is available, and refuses if not
    -- and gives the live side a name to depend on instead of a private one.
    """
    if bindings is None:
        raise CompositionRefusal(
            "the execution project's ledger is not available, so the idempotency store "
            "cannot be opened. The bridge does not keep a second one: two stores would "
            "be two sources of truth, and the one that mattered least would be the one "
            "deciding whether a duplicate trade happened."
        )
    try:
        return open_ledger(bindings, config.log_directory / "idempotency.json")
    except AutoTradeUnavailable as exc:
        raise CompositionRefusal(str(exc)) from exc


def _ask_downstream(config: BridgeConfig, bindings: AutoTradeBindings | None) -> Any:
    """The dry run's preflight, or ``None`` so the verdict reads as unevaluated.

    Returning ``None`` is the honest answer on a machine without the execution
    package: the report then says "not checked" rather than showing a green tick on
    the one gate that would refuse a disallowed symbol.
    """
    if bindings is None:
        return None
    limits = _downstream_limits(config)
    if limits is None:
        return None
    return ask_downstream_risk(bindings, limits)


def _downstream_limits(config: BridgeConfig) -> DownstreamLimits | None:
    """The execution project's own limits, read from the bridge's risk settings.

    ``None`` when the bridge is not configured to allow any symbol -- because an
    empty allow-list downstream is a real configuration meaning "nothing is
    allowed", and defaulting it to the bridge's own symbols would make the two
    projects agree by construction, which is the disagreement the preflight exists
    to expose.
    """
    risk = config.risk
    symbols = getattr(risk, "allowed_symbols", None)
    if not symbols:
        return None
    from decimal import Decimal

    return DownstreamLimits(
        allowed_symbols=set(symbols),
        max_volume=getattr(risk, "max_volume", None) or Decimal("1.0"),
        max_orders_per_minute=int(getattr(risk, "max_orders_per_minute", 5)),
        expiration_seconds=int(getattr(risk, "signal_expiration_seconds", 10)),
        max_open_positions=getattr(risk, "max_open_positions", None),
    )


def _refusal(config: BridgeConfig) -> str:
    if not config.execution_enabled:
        return (
            "execution is not enabled. A fresh checkout cannot place an order, and "
            "enabling it is a separate, explicit, documented act."
        )
    return "dry-run mode is on, so nothing is sent. That is the default and the safe state."
