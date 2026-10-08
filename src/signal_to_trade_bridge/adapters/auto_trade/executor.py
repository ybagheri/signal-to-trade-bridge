"""`TradeExecutor` over the execution project's real workflow.

The last piece between a validated decision and an order, and the only place in
this repository allowed to cause a position to exist. Everything upstream of here
is arithmetic and validation.

### What is wrapped, and what is not reimplemented

``ExecutionWorkflow`` is called, not rebuilt. It owns the state machine, the
ledger ordering, the kill switch check, the ``demo_only`` policy, and the
classification of a failed click as ``UNKNOWN`` rather than as a rejection. Those
are the parts of this design that were drawn to prevent a duplicate position, and
re-implementing any of them would be a second opinion about whether a trade
happened.

The mapping is therefore deliberately thin, and it is two-way:

```
ExecutionRequest  ->  TradeSignal        one direction enum, four numbers
ExecutionResult   ->  ExecutionResult    one status, one ticket
```

### Four decisions that are not obvious

**The evidence score is not sent as ``confidence``.** ``TradeSignal.confidence``
is validated as ``0..1`` and downstream code would reasonably read it as a
probability. The bridge's ``evidence_score`` is explicitly *not* one -- Phase 2
said so and the docstring says so again -- and a field named ``confidence`` is
the single most consequential place in this mapping to launder it. So it travels
in ``metadata`` under its own name, where it cannot be mistaken for a win rate by
a reader who has not read this file.

**``CLOSED`` becomes ``UNKNOWN``, not ``REJECTED``.** Upstream has six statuses
and the bridge knows five; ``CLOSED`` is the sixth, produced only by its
``close-position`` command. It maps to ``UNKNOWN`` on purpose. A closed position
means the order *was* placed and later closed, so re-sending is exactly the
duplicate that rule 5 exists to prevent -- whereas ``REJECTED`` says "definitely
not placed", which is a licence to retry. The bridge never closes, so this is a
status it should never see; it is handled rather than ignored because a status
arriving from nowhere is the one signal that something upstream changed.

**``ExecutionUnknownError`` is caught even though upstream re-raises it.** The
workflow's ``except Exception`` around the click deliberately re-raises, so
``execute()`` is **not** exception-free on that path -- and an adapter that
assumed it was would lose the trade record on exactly the outcome that most needs
one. Everything upstream's base exception covers becomes ``UNKNOWN``: not
retried, escalated, recorded.

**Nothing here can construct an ``MT5DesktopAdapter``.** The class that clicks
is bound in the composition root, never in this package, and there is a test that
fails if its name appears in this module. An adapter that can build its own
terminal adapter can build one with ``gate=ExecutionGate(enabled=True)``, and the
two guards -- workflow policy *and* adapter gate -- are what make a dry run
provably unable to reach a final control. This module keeps no way to defeat the
first one, and declines the ability to defeat the second.

### What this adapter does not do

It is **not wired into** ``ProcessSignal``, and that test still passes on purpose.
Execution arrives with the ledger, the kill switch and the audit log around it --
not before. Wiring an executor into the pipeline while the idempotency store is
still absent is precisely the failure the architecture was drawn to prevent, and
the structural test asserting it is the enforcement.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from signal_to_trade_bridge.adapters.auto_trade.bindings import (
    AutoTradeBindings,
    AutoTradeUnavailable,
    load_bindings,
)
from signal_to_trade_bridge.application.pre_submit import roll_delay_ms
from signal_to_trade_bridge.domain.enums import Direction
from signal_to_trade_bridge.domain.models import (
    ExecutionRequest,
    ExecutionResult,
    PreSubmitDelay,
)
from signal_to_trade_bridge.infrastructure.logging import Event, get_logger

if TYPE_CHECKING:
    from signal_to_trade_bridge.adapters.mt5.execution_mode import TradeModeSource
    from signal_to_trade_bridge.ports import KillSwitch

__all__ = ["SENTINEL_PROFILE", "AutoTradeExecutor"]

#: What upstream's ``ExecutionWorkflow`` requires as a ``profile`` and never
#: reads -- the field appears once in its source, in the assignment. Its own
#: tests pass an anonymous object with no attributes at all, which is the proof.
#: So this is a named empty stand-in rather than a fabricated profile: inventing
#: plausible-looking terminal attributes would be asserting facts about the
#: machine that nothing has established.
SENTINEL_PROFILE: Any = object()


class AutoTradeExecutor:
    """Delegates to the execution project's workflow.

    :class:`~signal_to_trade_bridge.ports.TradeExecutor`. Interchangeable with
    :class:`~signal_to_trade_bridge.adapters.fake.executor.FakeTradeExecutor`, so
    a test written against the fake exercises the same call path.
    """

    def __init__(
        self,
        workflow: Any,
        *,
        bindings: AutoTradeBindings | None = None,
        mt5_bindings: TradeModeSource | None = None,
        kill_switch: KillSwitch | None = None,
        now: Callable[[], datetime] | None = None,
        pre_submit_delay: PreSubmitDelay | None = None,
        sleeper: Callable[[float], None] | None = None,
        rng: random.Random | None = None,
        logger: Any | None = None,
    ) -> None:
        """
        :param workflow: an already-assembled ``ExecutionWorkflow``. **Injected,
            never constructed here** -- the kill switch, the ledger, the audit
            logger and the terminal adapter are all safety envelope, and an
            adapter that could assemble them itself could assemble them without
            one.
        :param bindings: the upstream subset, injectable so the mapping can be
            tested against doubles without the package installed.
        :param mt5_bindings: the **MetaTrader 5** bindings, used only to read the
            terminal's trading mode before clicking. A separate parameter from
            ``bindings`` because they are different packages: ``bindings`` is the
            execution project's namespace and knows nothing about the terminal.
            Conflating the two is what left the One Click Trading guard inert for a
            whole phase -- it was handed the wrong object, found it did not look like
            a terminal, and quietly skipped itself. ``None`` means the mode cannot be
            checked and the order goes ahead; ``live.build_live`` always supplies it.
        :param kill_switch: the bridge's own
            :class:`~signal_to_trade_bridge.ports.KillSwitch`. Checked **before**
            delegating, so an engaged switch stops the call rather than becoming
            a result the caller has to interpret. Optional because upstream's own
            workflow checks the same state internally; this is the outer layer,
            and it exists so the refusal is ours and not only theirs.
        :param now: injectable clock, so a test can pin the signal timestamp.
        :param pre_submit_delay: the bounded random pause taken after the order
            is fully prepared and immediately before the submission workflow
            runs. ``None`` (the default) means no pause, exactly as before.
        :param sleeper: what the pause waits on. ``time.sleep`` in production;
            injected in tests so the suite never actually waits.
        :param rng: the draw for the pause duration. A private instance by
            default; injected (seeded) in tests for a reproducible draw.
        :param logger: where the taken pause is recorded. Silent when the
            policy is disabled.
        """
        self._workflow = workflow
        self._bindings = bindings
        self._mt5_bindings = mt5_bindings
        self._kill_switch = kill_switch
        self._now = now or (lambda: datetime.now(UTC))
        self._pre_submit_delay = pre_submit_delay
        self._sleeper = sleeper or time.sleep
        self._rng = rng or random.Random()
        self._log = logger or get_logger("adapters.auto_trade")

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        """Send one order and report what happened.

        Never raises for an ordinary refusal -- refusing is an outcome, not an
        exception. The one thing it will not do is let an exception escape: a
        trade whose outcome is unknown must still be *recorded*, and an exception
        propagating out of here takes the record with it.
        """
        if self._kill_switch is not None and self._kill_switch.active:
            return ExecutionResult(
                signal_id=request.signal_id,
                status=ExecutionResult.STATUS_REJECTED,
                message="the bridge kill switch is engaged, so no order was sent",
            )

        try:
            bindings = self._bindings
            if bindings is None:
                # Loaded here rather than in __init__ so constructing an executor on
                # a machine without the checkout fails at the call, not at the
                # wiring -- and inside this try, because a missing dependency is a
                # fact to be *recorded*, not an exception to escape. An adapter that
                # raised here would take the trade record with it, and "the package
                # is not installed" is precisely the situation where an operator
                # most needs to see which signal did not go out.
                bindings = self._bindings = load_bindings()
            signal = self._to_signal(request, bindings)
        except AutoTradeUnavailable as exc:
            return ExecutionResult(
                signal_id=request.signal_id,
                status=ExecutionResult.STATUS_UNKNOWN,
                message=f"the order could not be translated for the execution project: {exc}",
                error=str(exc),
            )

        # **Refuse before the click, not after the fill.**
        #
        # On a terminal in One Click Trading mode the order ticket's fields are not
        # what gets sent -- the Toolbox Trade panel's values are, and that panel has
        # no stop loss. Every order this bridge placed before this check came back at
        # the panel's default volume with no stop and no target, while the dialog read
        # back exactly what was requested and `confirm_dialog_matches` passed.
        #
        # So the check is here, immediately before the workflow runs, where it can
        # still stop a click rather than describe one that already happened. It reads
        # the terminal's own state and changes nothing.
        refusal = self._refuse_for_execution_mode(request)
        if refusal:
            return ExecutionResult(
                signal_id=request.signal_id,
                status=ExecutionResult.STATUS_REJECTED,
                message=refusal,
            )

        # The pre-submit pause, and only here: every bridge-side refusal above
        # has already had its say, so a pause taken here is never spent on an
        # order that will not go out -- and no UI action has started yet, since
        # `workflow.execute` is the first call that touches the terminal. That
        # is what makes this "after preparation, before submission" rather than
        # a pause held while a half-filled dialog sits open.
        self._pause_before_submit(request)

        try:
            outcome = self._workflow.execute(signal)
        except Exception as exc:
            # Deliberately broad. Upstream's workflow is exception-free for
            # expected failures but **re-raises** `ExecutionUnknownError` when a
            # click may have been used, and a narrower catch here would lose the
            # record of the one outcome that most needs one. UNKNOWN is correct
            # for all of them: not retried, escalated, logged.
            return ExecutionResult(
                signal_id=request.signal_id,
                status=ExecutionResult.STATUS_UNKNOWN,
                message=f"the execution project raised {type(exc).__name__}: {exc}. The "
                f"order may or may not have reached the terminal, so it is recorded as "
                f"UNKNOWN and must not be retried.",
                error=f"{type(exc).__name__}: {exc}",
            )

        return self._to_result(request, outcome, bindings)

    def _refuse_for_execution_mode(self, request: ExecutionRequest) -> str:
        """Whether the terminal can carry this order's levels, or why it cannot.

        Reads the terminal through the MT5 bindings this adapter already has, and
        returns ``""`` when the order may go ahead.

        **A terminal that cannot be read is treated as being in the mode that cannot
        carry a stop.** The refusal costs one order on a machine whose bindings are
        briefly unavailable; failing the other way opens an unprotected position.
        """
        from signal_to_trade_bridge.adapters.mt5.execution_mode import (
            is_one_click_trading,
            refuse_one_click_order,
        )

        source = self._mt5_bindings
        if source is None:
            # No terminal was supplied, so there is nothing to read the mode from.
            # This is the state of every test that wires an executor by hand, and it
            # is **not** the live state: `build_live` always passes the terminal's
            # bindings, and `test_live_assembly` asserts it does.
            return ""
        try:
            return refuse_one_click_order(request, one_click=is_one_click_trading(source))
        except Exception:
            return (
                "the terminal's trading mode could not be read, so this order's stop "
                "loss cannot be confirmed as one that would actually be sent. Refusing "
                "rather than opening a position that may have no stop."
            )

    def _pause_before_submit(self, request: ExecutionRequest) -> int | None:
        """Wait out the configured pre-submit pause, if one is configured.

        Returns the milliseconds waited, or ``None`` when no pause was taken.
        The duration is rolled fresh per submission within the configured
        bounds, and logged with the signal id -- a pause nobody can see in the
        log is indistinguishable from one that never happened.
        """
        delay_ms = roll_delay_ms(self._pre_submit_delay, self._rng)
        if delay_ms is None:
            return None
        self._log.event(
            Event.PRE_SUBMIT_DELAY_APPLIED,
            signal_id=request.signal_id,
            delay_ms=delay_ms,
        )
        self._sleeper(delay_ms / 1000.0)
        return delay_ms

    def _to_signal(self, request: ExecutionRequest, bindings: AutoTradeBindings) -> Any:
        """The bridge's request as an upstream ``TradeSignal``.

        Four numbers cross unchanged -- entry, stop, take profit, volume -- because
        the bridge computed them and re-deriving them here would be a second
        opinion about position size. The direction is translated, not renamed;
        see :mod:`.bindings`' note on why ``BUY`` is not a synonym for ``LONG``.
        """
        action = bindings.order_action(_order_action(request.direction))
        metadata = dict(request.metadata)
        metadata["bridge_evidence_score"] = request.evidence_score
        metadata["bridge_dry_run"] = False

        # Read once. The clock is injectable, so a test pinning it is fine, but
        # calling `_now()` twice would produce a signal whose timestamp and whose
        # audit moment differ -- and with upstream's 10-second default expiry, a
        # clock that straddles that boundary can report a signal as expired before
        # it was ever seen. Found by the test that drives the *real* workflow: an
        # injected clock of a fixed past date made every signal look stale.
        now = self._now()

        return bindings.TradeSignal(
            signal_id=request.signal_id,
            timestamp=now,
            source=_SOURCE,
            symbol=request.symbol,
            action=action,
            volume=request.volume,
            price=request.entry,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            comment=request.comment,
            strategy=request.strategy,
            # confidence is deliberately unset -- see the module docstring.
            #
            # `expiration` is set from the clock rather than left to upstream's
            # default of `timestamp + 10s`. Both are "now + a window", but the
            # default is measured from a *stamped* time that an injected clock may
            # have set in the past, so a test pinning the clock saw every signal
            # reported as expired. Setting it here makes the window mean what it
            # says: from the moment this adapter ran.
            expiration=now + _DEFAULT_EXPIRY,
            metadata=metadata,
        )

    def _to_result(
        self, request: ExecutionRequest, outcome: Any, bindings: AutoTradeBindings
    ) -> ExecutionResult:
        """Upstream's result as the bridge's, with nothing dropped silently.

        Two of the bridge's result fields have no upstream source, and passing
        ``None`` is the honest answer rather than a guess:

        * ``position_id`` is upstream's ``order_reference``, renamed. What upstream
          reports is the thing it observed, and calling it an order id would be a
          claim about a broker order it does not make.
        * ``executed_price`` is always ``None``. Upstream's ``VerificationEvidence``
          carries ``baseline``, ``observed`` and ``position_id`` -- references to
          the snapshots it compared -- and **no fill price**. A click is not a
          fill, so upstream never had one to report; inventing one from the
          requested price would be the most confident-looking fabrication in this
          adapter.
        """
        raw = str(getattr(outcome, "status", "") or "")
        status, note = _STATUS_MAP(raw, bindings)

        evidence: dict[str, Any] = {
            "upstream_status": raw,
            "upstream_state": str(getattr(outcome, "state", "")),
        }
        if note:
            evidence["status_note"] = note

        upstream_evidence = getattr(outcome, "evidence", None)
        if upstream_evidence is not None and hasattr(upstream_evidence, "to_dict"):
            evidence["verification"] = upstream_evidence.to_dict()

        message = str(getattr(outcome, "message", "") or "")
        if note:
            message = f"{message} ({note})" if message else note

        return ExecutionResult(
            signal_id=request.signal_id,
            status=status,
            message=message,
            position_id=_optional_str(getattr(outcome, "order_reference", None)),
            executed_price=None,
            requested_price=request.entry,
            error=_optional_str(getattr(outcome, "error", None)),
            evidence=evidence,
        )


#: The bridge's direction vocabulary onto upstream's order actions. Exhaustive over
#: the *tradable* directions, and a function rather than a bare subscript so an
#: unmapped direction raises here -- where the message can name the direction --
#: instead of at an enum lookup inside the bindings object.
_ORDER_ACTIONS: Mapping[Direction, str] = {
    Direction.LONG: "BUY",
    Direction.SHORT: "SELL",
}


def _order_action(direction: Direction) -> str:
    try:
        return _ORDER_ACTIONS[direction]
    except KeyError as exc:
        raise AutoTradeUnavailable(
            f"{direction} is not a tradable direction, so there is no order action to "
            f"send. A direction can only reach here by bypassing ExecutionRequest's own "
            f"validation, which means the trade was never validated either."
        ) from exc


#: What the bridge calls itself in upstream's audit log. A stable string, because
#: an audit record that names its producer is the difference between a log that
#: can be correlated and one that has to be guessed at.
_SOURCE = "signal-to-trade-bridge"

#: How long the built signal stays valid. Upstream's own default for
#: ``RiskLimits.expiration_seconds``, restated here because this adapter sets
#: ``expiration`` explicitly rather than letting a *stamped* timestamp decide --
#: see ``_to_signal``. Not a configuration key: it is upstream's, and a bridge
#: that invented a different window would silently widen or narrow a safety limit
#: it does not own.
_DEFAULT_EXPIRY = timedelta(seconds=10)


def _STATUS_MAP(raw: str, bindings: AutoTradeBindings) -> tuple[str, str | None]:
    """Upstream's status string onto the bridge's, plus a note when lossy.

    Five of upstream's six statuses pass through by name, because upstream and
    this bridge were built against the same five-state vocabulary and the names
    matching is not an accident worth relying on silently -- so each is listed.
    The sixth is ``CLOSED`` and is the interesting one; see the module docstring.
    """
    status_enum = bindings.ExecutionStatus
    for name in ExecutionResult.KNOWN_STATUSES:
        if getattr(status_enum, name, None) == raw:
            return name, None

    # CLOSED: a position existed and was closed. The trade happened, so a retry
    # is the duplicate rule 5 forbids. UNKNOWN is the only honest status for it.
    closed = getattr(status_enum, "CLOSED", None)
    if closed is not None and closed == raw:
        return ExecutionResult.STATUS_UNKNOWN, "upstream reported CLOSED, mapped to UNKNOWN"

    # Anything else is a vocabulary the bridge does not know. UNKNOWN is the
    # safe reading, and the value is kept in the evidence so the drift is visible
    # rather than merely handled.
    return ExecutionResult.STATUS_UNKNOWN, f"unrecognised upstream status {raw!r}"


def _optional_str(value: object) -> str | None:
    """Upstream's optional string fields, with an empty one treated as absent.

    ``order_reference`` and ``error`` are both ``str | None`` upstream and both may
    arrive as ``""`` rather than ``None``. An empty string is not a position
    ticket, and storing it would make ``position_id is not None`` -- the check a
    caller would naturally write -- true for a result that names nothing.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None
