"""Asking `auto-trade`'s risk engine what it *would* say, without sending anything.

Phase 8's substance. The bridge validates against its own rules and `auto-trade`
validates against its own rules again, and the two rulebooks disagree by default:
this project's default allow-list is whatever the operator configured, while
`auto-trade`'s is `EURUSD,XAUUSD,YM` with a 1.0 volume cap and five orders a
minute. **A signal can pass every check here and be refused downstream** — and the
only place that used to become visible was a `REJECTED` result on a live account.

This module closes that gap in advance. It imports `auto_trade`'s ``RiskEngine`` and
``RiskLimits`` and asks them the question, which is a pure function of the request
and the configured limits. It constructs no workflow, no adapter, no terminal, and
cannot reach a final control.

### Why ``RiskEngine`` alone is enough to ask

`ExecutionWorkflow` checks its risk engine before it touches anything else — the
gate runs at ``workflow.py`` step 4, and the state machine's happy path only reaches
``LOCATING_TERMINAL`` afterwards. So a risk decision taken here is the same decision
the real path would take, arrived at without the parts that can move money.

What this **cannot** predict, and what the report must not claim:

* the account-type check, which needs a connected terminal
* the kill switch
* the ledger -- whether this signal id was already acted on
* the two-indicators-later gates: rate limiting and open-position count

Those are real and they can all refuse. So the verdict is named
:class:`~signal_to_trade_bridge.application.dry_run.DownstreamVerdict` and carries
``evaluated`` -- and when no engine is wired it reports ``evaluated=False``, never
``accepted=True``. An unevaluated gate that reads as a pass is the exact failure
this phase was written to prevent, in the direction this project refuses to be
wrong in.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from signal_to_trade_bridge.adapters.auto_trade.bindings import AutoTradeBindings
from signal_to_trade_bridge.application.dry_run import DownstreamVerdict
from signal_to_trade_bridge.domain.enums import Direction
from signal_to_trade_bridge.domain.models import ExecutionRequest

__all__ = ["DownstreamLimits", "ask_downstream_risk"]


class DownstreamLimits:
    """The limits to ask about, as the bridge understands them.

    Mirrors `auto_trade`'s ``RiskLimits`` without importing it, so the bridge can
    configure the check from its own configuration and a test can drive it without
    the package installed. Constructing :class:`auto_trade.domain.models.RiskLimits`
    from these happens inside :func:`ask_downstream_risk`, which is the only place
    the two vocabularies meet.

    ``allowed_symbols`` is **not** defaulted here. An empty set is a real
    configuration meaning "nothing is allowed", and a default of the bridge's
    configured symbols would silently make the downstream check agree with this
    project's own allow-list -- which is the disagreement this module exists to
    expose. Callers pass what `auto-trade` is actually configured with.
    """

    __slots__ = (
        "allowed_symbols",
        "expiration_seconds",
        "max_open_positions",
        "max_orders_per_minute",
        "max_volume",
    )

    def __init__(
        self,
        allowed_symbols: frozenset[str] | set[str],
        max_volume: Decimal,
        max_orders_per_minute: int,
        expiration_seconds: int,
        max_open_positions: int | None = None,
    ) -> None:
        self.allowed_symbols = frozenset(s.upper() for s in allowed_symbols)
        self.max_volume = max_volume
        self.max_orders_per_minute = max_orders_per_minute
        self.expiration_seconds = expiration_seconds
        self.max_open_positions = max_open_positions

    def __repr__(self) -> str:
        # Explicit, and notably *without* the limits themselves. This holds a
        # configured risk envelope, and the repr of a config object ends up in
        # tracebacks and logs. The other reprs in this project redact for the same
        # reason.
        return (
            f"DownstreamLimits(symbols={len(self.allowed_symbols)}, "
            f"max_open_positions={self.max_open_positions})"
        )


def _to_signal(
    request: ExecutionRequest,
    bindings: AutoTradeBindings,
    now: Callable[[], datetime],
    expiration_seconds: int,
) -> Any:
    """The request as upstream's ``TradeSignal``, for the engine to judge.

    The same construction
    :class:`~signal_to_trade_bridge.adapters.auto_trade.executor.AutoTradeExecutor`
    performs, and it has to be: the engine checks ``symbol``, ``volume`` and
    ``expiration``, so a different signal here would be checked against different
    rules than the one that will eventually be sent.

    Deliberately **not** delegated to the executor. Reaching through the executor to
    a private method would couple the dry run to a class that can execute; asking the
    engine directly keeps this module incapable of placing anything, which is what
    makes it safe to run on every decision.

    ``expiration`` is set from the incoming limit, because "the signal has expired"
    is one of the seven gates and a dry run that cannot exercise it would report a
    pass on a signal the real path would refuse as stale.
    """
    return bindings.TradeSignal(
        signal_id=request.signal_id,
        timestamp=now(),
        source="signal-to-trade-bridge.dry-run",
        symbol=request.symbol,
        action=bindings.order_action(_ORDER_ACTIONS[request.direction]),
        volume=request.volume,
        price=request.entry,
        stop_loss=request.stop_loss,
        take_profit=request.take_profit,
        expiration=now() + timedelta(seconds=expiration_seconds),
    )


#: The same mapping the executor uses, written out again for the same reason: this
#: module must not import the executor, and a shared constant in a third module for
#: two uses would be a dependency to get wrong once instead of twice. A test asserts
#: the two tables agree, so the duplication cannot drift.
_ORDER_ACTIONS: dict[Direction, str] = {
    Direction.LONG: "BUY",
    Direction.SHORT: "SELL",
}


def ask_downstream_risk(
    bindings: AutoTradeBindings,
    limits: DownstreamLimits,
    *,
    now: Callable[[], datetime] | None = None,
) -> Callable[[ExecutionRequest], DownstreamVerdict]:
    """A callable the dry run can hand to :func:`~.dry_run.report_for`.

    Returns the closure rather than taking a request, so it composes with
    ``ask_downstream=`` and so the bindings and limits are resolved once.

    **Any failure here is an unevaluated verdict, never an accepted one.** A
    missing package, a renamed class or an engine that raises all mean "this check
    did not happen", and reporting that as a pass would be the most expensive
    possible silence: an operator reads a green tick on the one gate that would
    have refused the trade.
    """
    clock = now or (lambda: datetime.now(UTC))

    def ask(request: ExecutionRequest) -> DownstreamVerdict:
        try:
            from auto_trade.application.risk import RiskEngine
            from auto_trade.domain.models import RiskLimits

            upstream_limits = RiskLimits(
                set(limits.allowed_symbols),
                limits.max_volume,
                limits.max_orders_per_minute,
                limits.expiration_seconds,
                limits.max_open_positions,
            )
            signal = _to_signal(request, bindings, clock, limits.expiration_seconds)
            # `now` is passed explicitly rather than left to upstream's default.
            # Upstream defaults to `utc_now()` while the signal was stamped from our
            # injected clock, so a test pinning "now" would see the *real* clock
            # expire the signal and every gate would report "signal is expired".
            decision = RiskEngine(upstream_limits).validate(signal, now=clock())
        except Exception as exc:
            return DownstreamVerdict.not_evaluated(
                f"the downstream risk engine could not be consulted: {type(exc).__name__}: {exc}"
            )

        return DownstreamVerdict(
            accepted=bool(decision.accepted),
            reason=str(getattr(decision, "reason", "") or ""),
        )

    return ask
