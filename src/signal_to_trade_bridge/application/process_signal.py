"""``ProcessSignal`` -- the single use case, and the first thing that calls everything.

Five phases built a dozen functions that each do one job correctly. **Nothing in
``src/`` had ever called them in sequence.** That is what this module is: the
ordering, the wiring between them, and the discipline to stop at the first
refusal.

The order is the design, and it is not arbitrary:

```
 1  validate_signal         is this a signal we will act on at all?
 2  resolve_stop            is there a defensible stop, on the correct side?
 3  resolve_take_profit     where does the trade aim, and did we choose that?
 4  validate_geometry       do entry, stop and target agree with each other?
 5  validate_policy         may *this bridge* act, under its own rules?
 6  validate_against_spec   can the symbol express these prices at all?
 7  budget + position size  how much may be risked, and how big
```

**Cheap and decisive before expensive and arithmetic.** Signal validity costs
nothing and rejects the commonest outcome in the system. Stop resolution comes
next because without a stop there is nothing to size against. Geometry is checked
before policy because a malformed trade is not a policy question. The
account-and-symbol facts come last, because they are the only steps that reach
outside the process, and a signal that fails step 1 must not have cost a round
trip to a terminal.

**It stops at the first refusal.** Running a later check on a signal that has
already failed produces arithmetic on meaningless inputs -- a position size
computed from a stop that does not exist is arithmetically fine and completely
wrong, which is the failure mode this project keeps running into and keeps fixing.
So each stage's refusal becomes the decision, and the stages after it never run.
``diagnostics["stage"]`` names the stage that refused, because "no trade" without
"which check" is an answer nobody can act on.

### Every failure is a decision, not an exception

The return type is always a :class:`~domain.models.TradeDecision`. A signal that
is malformed, an abstention, a stop on the wrong side, a terminal that is not
running -- all of them come back as a ``TradeDecision`` carrying a reason code.
An exception would mean the trading loop died on exactly the conditions it exists
to survive, and the commonest outcome here is "the engine found nothing to trade".

### It cannot place an order -- until Phase 9 wired it, and what that required

A trade that passes every check came back as ``DRY_RUN`` through Phase 8. That was
true and it was not much use. Phase 9 gave :meth:`ProcessSignal.wire_execution` an
:class:`~application.execution_envelope.ExecutionEnvelope`, so the pipeline can now
reach an executor -- **and only together with the idempotency ledger and the kill
switch.**

That is the whole point of the envelope type, and it replaces a rule this module used
to hold by assertion alone. For three phases a test read this file's source and
failed if it imported ``TradeExecutor``: an execution path appearing here without the
ledger and the kill switch around it is the failure the architecture was drawn to
prevent. That test has been retired, because **an absence cannot also authorise** --
it can only forbid. The rule it enforced is now a type: the envelope has no public
constructor, so there is no way to hand this class an executor without the other two.

What is still refused here, deliberately:

* **an executor alone.** :meth:`wire_execution` takes the envelope, not a port.
* **the ledger consulted before the send.** The ledger answers *after* every stage,
  on the deterministic ``signal_id``, so a re-delivered reading of the same bar is
  refused before anything is sent rather than after.
* **anything at all on the default configuration.** ``execution_enabled`` defaults to
  false, and the default cannot execute.

### The account is one snapshot per decision

The account is read **once**, here, and the same read feeds the concurrency gate
at step 5 and the budget at step 7. Reading twice would be cheap and still wrong:
two reads can straddle a close, and the open-position count that admitted the
trade would not be the count that sized it. The same holds for the symbol
specification.

That is why :class:`RiskService` has a resolve half (``budget_for``,
``size_for``) alongside its read-and-resolve convenience methods. This module
reads; the service resolves.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from signal_to_trade_bridge.application.dry_run import (
    DryRunReport,
    report_for,
)
from signal_to_trade_bridge.application.execution_envelope import ExecutionEnvelope
from signal_to_trade_bridge.application.risk_service import RiskService
from signal_to_trade_bridge.configuration.config import BridgeConfig
from signal_to_trade_bridge.domain.enums import DecisionAction, RejectionReason
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionRequest,
    ExecutionResult,
    RiskBudget,
    RiskParameters,
    Signal,
    TakeProfit,
    TradeDecision,
    TradeIntent,
)
from signal_to_trade_bridge.domain.resolution import Resolution, refused
from signal_to_trade_bridge.domain.stops import is_structural_basis, resolve_stop
from signal_to_trade_bridge.domain.take_profit import resolve_take_profit
from signal_to_trade_bridge.domain.validation import (
    validate_against_spec,
    validate_geometry,
    validate_policy,
    validate_signal,
)
from signal_to_trade_bridge.infrastructure.logging import Event, StructuredLogger, get_logger

__all__ = ["STAGES", "ProcessSignal", "budget_to_account"]

#: Every stage in the order it runs. Named in one place because the order is the
#: design, and a reader should be able to check it against the call sequence
#: without counting statements. A test asserts this tuple against the order the
#: refusals actually come out in.
STAGES: tuple[str, ...] = (
    "signal",
    "stop",
    "take_profit",
    "geometry",
    "policy",
    "spec",
    "size",
)

#: What a fully-validated, unsent trade reports as its reason. Not a
#: ``RejectionReason``, because it is not a rejection -- it is the one outcome
#: where nothing went wrong. A reason code is required on every decision and this
#: is the value that keeps it from being a lie.
PASSED = "PIPELINE_PASSED"


class ProcessSignal:
    """Turns one signal into one decision.

    Stateless. It holds two collaborators and a clock and mutates nothing, so the
    same signal processed twice produces the same decision -- a property Phase 9's
    idempotency ledger depends on and cannot itself provide.
    """

    def __init__(
        self,
        risk: RiskService,
        config: BridgeConfig,
        *,
        logger: StructuredLogger | None = None,
        now: Callable[[], datetime] | None = None,
        ask_downstream: Callable[[ExecutionRequest], Any] | None = None,
    ) -> None:
        self._risk = risk
        self._config = config
        self._log = logger or get_logger("application.pipeline")
        # Injected rather than read from the clock inside a domain model, so a
        # test can pin "now" and get an identical decision twice. `utc_now`
        # exists; nothing should force the use of it over an injected value.
        self._now = now or (lambda: datetime.now(UTC))
        # Asked what the execution project's own gates would say, for the dry-run
        # report only. Optional, and `None` produces an *unevaluated* verdict rather
        # than an accepted one -- see `DownstreamVerdict`.
        self._ask_downstream = ask_downstream
        # Absent until `wire_execution`. Not an executor: an executor alone is
        # unrepresentable, so this is either nothing or all three.
        self._envelope: ExecutionEnvelope | None = None

    # -- wiring -----------------------------------------------------------

    def wire_execution(self, envelope: ExecutionEnvelope) -> None:
        """Allow this pipeline to send, with the ledger and the kill switch.

        Takes the envelope rather than an executor **on purpose**: the envelope has
        no public constructor, so a caller cannot reach this method with an executor
        and nothing else. Three phases of "the pipeline imports no executor" could
        forbid the mistake; only a type can permit the right thing safely.

        Idempotent in the harmless direction -- wiring twice replaces the envelope
        rather than composing two -- because a composition root that wires in two
        passes has a bug, and stacking envelopes would be an unlogged second way to
        place an order.
        """
        self._envelope = envelope
        # No new event member here on purpose. `EXECUTION_RESULT` carries the state
        # where it matters and is emitted on every decision; adding an enum member
        # for a wiring diagnostic would grow the vocabulary for something nobody
        # will search for. `debug`, not `event`, because this is once at startup and
        # not a fact about any trade.
        self._log.debug(
            Event.DRY_RUN_COMPLETED,
            stage="wire_execution",
            envelope=repr(envelope),
            execution_enabled=self._config.execution_enabled,
            dry_run=self._config.dry_run,
        )

    @property
    def can_execute(self) -> bool:
        """Whether this pipeline would actually send, if a trade passed.

        Three conditions, and all three reported rather than one derived flag,
        because "wired but dry-running" and "wired but execution disabled" are
        different operational states with different remedies.
        """
        return (
            self._envelope is not None
            and self._config.execution_enabled
            and not self._config.dry_run
        )

    # -- the use case ----------------------------------------------------

    def process(self, signal: Signal) -> TradeDecision:
        """Run every stage in order and return the decision.

        Never raises for anything the market, the configuration or an unreachable
        terminal can cause. The only exceptions that escape are programming
        errors, which is the correct direction: a bug should be loud.
        """
        risk = self._config.risk
        symbol = signal.symbol_normalised

        # -- 1. the signal itself ------------------------------------------
        validated = validate_signal(signal, risk)
        if not validated.ok:
            return self._refuse(signal.signal_id, "signal", validated)

        # -- 2. the stop -----------------------------------------------------
        stop_resolution = resolve_stop(signal, risk)
        if not stop_resolution.ok:
            return self._refuse(signal.signal_id, "stop", stop_resolution)
        stop = stop_resolution.unwrap()
        self._log.event(
            Event.STOP_RESOLVED,
            signal_id=signal.signal_id,
            symbol=symbol,
            ok=True,
            reason="OK",
            price=str(stop.price),
            distance=str(stop.distance),
            source=stop.source.value,
            basis=stop.basis,
            structural=is_structural_basis(stop.basis),
        )

        # -- 3. the take profit ---------------------------------------------
        # Checked with `.ok`, not by testing the value: `TakeProfitSource.NONE`
        # is a *successful* resolution of "there is no target", which is the whole
        # reason `Resolution` carries an explicit flag.
        take_profit_resolution = resolve_take_profit(signal, stop, risk)
        if not take_profit_resolution.ok:
            return self._refuse(signal.signal_id, "take_profit", take_profit_resolution)
        take_profit = take_profit_resolution.value
        self._log_take_profit(signal.signal_id, symbol, take_profit_resolution, take_profit, risk)

        # -- 4. geometry ------------------------------------------------------
        geometry = validate_geometry(signal, stop, take_profit)
        if not geometry.ok:
            return self._refuse(signal.signal_id, "geometry", geometry)
        # The stage's *validated pair* is used from here on, not the two values
        # that were passed in. It is the same objects today, and the difference
        # is the point: this is the stage that exists to say "these two agree", so
        # continuing to use the un-validated copies would make it a function whose
        # answer is ignored, which is how a check stops being one.
        stop, take_profit = geometry.unwrap()

        # -- 5. the operator's rules ------------------------------------------
        # The account is read once, here, and the same snapshot sizes the trade at
        # step 7. `None` means "the terminal did not answer", which is only fatal
        # below if a gate actually needs the number -- see `_account_required`.
        account = self._risk.read_account()
        open_positions = account.open_positions if account is not None else None
        if risk.max_open_positions is not None and open_positions is None:
            return self._refuse(signal.signal_id, "policy", _account_required(risk))
        policy = validate_policy(signal, risk, open_positions)
        if not policy.ok:
            return self._refuse(signal.signal_id, "policy", policy)

        # -- 6. what the symbol can express ------------------------------------
        spec = self._risk.read_spec(symbol)
        if spec is None:
            return self._refuse(signal.signal_id, "spec", _spec_required(symbol))
        spec_check = validate_against_spec(
            signal.entry,  # type: ignore[arg-type]  # stage 1 proved it is not None
            stop.price,
            take_profit,
            spec,
        )
        if not spec_check.ok:
            return self._refuse(signal.signal_id, "spec", spec_check)

        # -- 7. the money ------------------------------------------------------
        budget_resolution = self._risk.budget_for(account, risk, symbol=symbol)
        if not budget_resolution.ok:
            return self._refuse(signal.signal_id, "size", budget_resolution)
        budget = budget_resolution.unwrap()

        size_resolution = self._risk.size_for(symbol, stop, budget, spec)
        if not size_resolution.ok:
            return self._refuse(signal.signal_id, "size", size_resolution)

        # -- 8. the decision ---------------------------------------------------
        intent = TradeIntent(
            signal=signal,
            symbol=symbol,
            direction=signal.direction,
            entry=signal.entry,  # type: ignore[arg-type]  # stage 1 proved it is not None
            stop_loss=stop,
            take_profit=take_profit,
            position_size=size_resolution.unwrap(),
            risk_parameters=risk,
            account_balance=budget_to_account(budget),
            symbol_spec=spec,
        )
        return self._validated(signal.signal_id, intent)

    # -- after the decision: send, or say exactly why not -------------------

    def _validated(self, signal_id: str, intent: TradeIntent) -> TradeDecision:
        """A trade that passed every check: send it, or report in full why not.

        The fork is here and nowhere else, and **both branches produce the same
        ``TradeIntent``** -- the sizing is done either way. That is deliberate: a
        dry run that stops short of building the order cannot report the order, and
        the order is the thing an operator needs to see before enabling execution.

        Order of the checks, and it is not arbitrary:

        1. **the ledger** -- before the configuration. An engine that has no record
           of this signal id may proceed; one that does is refused regardless of
           whether execution is even enabled, because the answer "this trade already
           happened" is true whether or not we are about to place it.
        2. **the configuration** -- wiring, then ``execution_enabled``, then
           ``dry_run``. Each is a separate blocker rather than one boolean, so a
           misconfiguration is named.
        3. **the kill switch** -- checked here even though the workflow checks it
           too. Ours is the outer layer: if it is engaged, nothing is sent and the
           refusal is ours rather than a result the caller has to interpret.
        """
        report = report_for(
            intent,
            execution_enabled=self.can_execute,
            dry_run=self._config.dry_run,
            ask_downstream=self._ask_downstream,
            extra={"wired": self._envelope is not None},
        )
        self._log_dry_run(report)

        if self._envelope is not None and self._envelope.idempotency.contains(signal_id):
            return self._duplicate(signal_id, intent, report)

        if self._envelope is not None and self._config.execution_enabled:
            if self._config.dry_run:
                return self._dry_run(signal_id, intent, report)
            if self._envelope.kill_switch.active:
                return self._killed(signal_id, intent, report)
            return self._send(signal_id, intent, report)

        return self._dry_run(signal_id, intent, report)

    def _log_dry_run(self, report: DryRunReport) -> None:
        """Emit the report whether or not anything will be sent.

        Emitted on **every** path, including the one that sends. The first live
        order is exactly the moment somebody will want to read what was decided, and
        an event that only fires when nothing happens is silent on the day it starts
        mattering.
        """
        request = report.request
        self._log.event(
            Event.EXECUTION_RESULT,
            signal_id=None if request is None else request.signal_id,
            symbol=None if request is None else request.symbol,
            volume=None if request is None else str(request.volume),
            entry=None if request is None else str(request.entry),
            stop_loss=None if request is None else str(request.stop_loss),
            take_profit=None
            if request is None or request.take_profit is None
            else str(request.take_profit),
            reward_to_risk=None if report.reward_to_risk is None else str(report.reward_to_risk),
            risk_amount=str(report.risk_amount),
            planned_loss=str(report.planned_loss),
            would_be_sent=report.would_be_sent,
            downstream_would_accept=report.downstream_would_accept,
            downstream_reason=report.downstream.reason,
            downstream_evaluated=report.downstream.evaluated,
            blockers=list(report.blockers()),
        )

    def _duplicate(
        self, signal_id: str, intent: TradeIntent, report: DryRunReport
    ) -> TradeDecision:
        """This signal id has been acted on before. Refuse before sending.

        Checked against the ledger rather than against this process's memory,
        because a re-delivery can arrive after a restart -- which is the case that
        matters, and the one an in-memory set would silently miss.

        A ``REJECTED`` refusal, not ``UNKNOWN``: nothing was sent, because this is
        the check that happened before the send. It is also the refusal upstream's
        ledger produces on its own (``"duplicate signal id"``), so the bridge and the
        execution project now agree about it rather than one permitting what the
        other forbids.
        """
        self._log.warning(
            Event.TRADE_REJECTED,
            signal_id=signal_id,
            stage="idempotency",
            reason=RejectionReason.DUPLICATE_SIGNAL.value,
            explanation=(
                "this signal id is already in the idempotency ledger, so it has been acted "
                "on before and is not offered again. The signal id is a hash of the bar and "
                "the setup, so this is a re-delivery of the same reading rather than a new "
                "trade."
            ),
            blockers=list(report.blockers()),
        )
        return TradeDecision(
            signal_id=signal_id,
            action=DecisionAction.NO_TRADE,
            reason=RejectionReason.DUPLICATE_SIGNAL.value,
            explanation=(
                "already acted on: this signal id is in the idempotency ledger. Nothing was sent."
            ),
            intent=intent,
            decided_at=self._now(),
            diagnostics={
                "stage": "idempotency",
                "report": report.to_dict(),
            },
        )

    def _killed(self, signal_id: str, intent: TradeIntent, report: DryRunReport) -> TradeDecision:
        """The kill switch is engaged. Nothing is sent, and it is not a failure."""
        self._log.warning(
            Event.TRADE_REJECTED,
            signal_id=signal_id,
            stage="kill_switch",
            reason=RejectionReason.KILL_SWITCH_ACTIVE.value,
            explanation=(
                "the kill switch is engaged, so nothing was sent. This is a deliberate stop "
                "and not a fault."
            ),
        )
        return TradeDecision(
            signal_id=signal_id,
            action=DecisionAction.NO_TRADE,
            reason=RejectionReason.KILL_SWITCH_ACTIVE.value,
            explanation="the kill switch is engaged; nothing was sent.",
            intent=intent,
            decided_at=self._now(),
            diagnostics={"stage": "kill_switch", "report": report.to_dict()},
        )

    def _send(self, signal_id: str, intent: TradeIntent, report: DryRunReport) -> TradeDecision:
        """Send it, and record what came back.

        The ledger is consulted again *after* the send, because the envelope's own
        ledger and the executor's are the same file written by the same upstream
        class -- so this records the bridge's view for a caller that reads the
        bridge's decisions, and the executor's write is what makes a restart see it.

        **An exception from the executor is a decision, not a crash.** The executor
        already converts upstream's escaping ``ExecutionUnknownError`` into an
        ``UNKNOWN`` result; this catches the rest, because a decision whose recording
        failed must still be recorded.
        """
        assert self._envelope is not None  # guaranteed by the caller
        request = report.request
        assert request is not None  # every validated intent produces one since Phase 9

        try:
            result = self._envelope.executor.submit(request)
        except Exception as exc:  # broad on purpose: a crash here loses the record
            result = ExecutionResult(
                signal_id=signal_id,
                status=ExecutionResult.STATUS_UNKNOWN,
                message=f"the executor raised {type(exc).__name__}: {exc}. The order may or "
                f"may not have reached the terminal, so it is recorded as UNKNOWN and "
                f"must not be retried.",
                error=f"{type(exc).__name__}: {exc}",
            )

        self._log_execution(request, result)
        return self._executed(signal_id, intent, report, result)

    def _log_execution(self, request: ExecutionRequest, result: ExecutionResult) -> None:
        """Log one sent order, with everything needed to investigate it later.

        The request is logged alongside the result rather than only the result,
        because a result whose request cannot be reconstructed is not
        investigable: the volume and the prices are the only way to tell whether a
        broker behaved oddly or the bridge sized the trade wrong.

        ``error`` is carried even on success, because upstream's message field is
        where a rejection reason lives and dropping it would lose the only text
        explaining a ``REJECTED``.
        """
        self._log.event(
            Event.EXECUTION_RESULT,
            signal_id=request.signal_id,
            symbol=request.symbol,
            direction=request.direction.value,
            volume=str(request.volume),
            entry=str(request.entry),
            stop_loss=str(request.stop_loss),
            take_profit=None if request.take_profit is None else str(request.take_profit),
            status=result.status,
            position_id=result.position_id,
            error=result.error,
            message=result.message,
        )

    def _executed(
        self,
        signal_id: str,
        intent: TradeIntent,
        report: DryRunReport,
        result: ExecutionResult,
    ) -> TradeDecision:
        """Turn an execution result into a decision, honestly in every direction.

        The mapping is the point. ``ACCEPTED`` becomes ``EXECUTE`` because a position
        was independently observed to exist. **``DRY_RUN`` becomes ``DRY_RUN``**,
        which can happen when the execution project's own policy refuses to execute
        even though the bridge's configuration allows it -- two configurations, and
        the downstream one wins. Everything else is a ``NO_TRADE`` carrying the
        result's status, including ``UNKNOWN``, which is never retried.
        """
        action = (
            DecisionAction.EXECUTE
            if result.is_accepted
            else DecisionAction.DRY_RUN
            if result.is_dry_run
            else DecisionAction.NO_TRADE
        )
        reason = PASSED if action is not DecisionAction.NO_TRADE else result.status
        self._log.event(
            Event.EXECUTION_RESULT,
            signal_id=signal_id,
            status=result.status,
            action=action.value,
            position_id=result.position_id,
            executed_price=None if result.executed_price is None else str(result.executed_price),
            error=result.error,
            retryable=result.is_retryable,
        )
        return TradeDecision(
            signal_id=signal_id,
            action=action,
            reason=reason,
            explanation=result.message,
            intent=intent,
            execution=result,
            decided_at=self._now(),
            diagnostics={"stage": "executed", "report": report.to_dict()},
        )

    def _dry_run(self, signal_id: str, intent: TradeIntent, report: DryRunReport) -> TradeDecision:
        """Every check passed, nothing was sent, and the report says why not."""
        self._log.event(
            Event.TRADE_VALIDATED,
            **{
                "signal_id": signal_id,
                "symbol": intent.symbol,
                "direction": intent.direction.value,
                "volume": str(intent.volume),
                "entry": str(intent.entry),
                "stop_loss": str(intent.stop_loss.price),
                "stop_distance": str(intent.stop_loss.distance),
                "take_profit": None
                if intent.take_profit is None
                else str(intent.take_profit.price),
                "reward_to_risk": None
                if intent.reward_to_risk is None
                else str(intent.reward_to_risk),
                "risk_amount": str(intent.risk_amount),
                "planned_loss": str(intent.position_size.planned_loss),
                "execution_enabled": self._config.execution_enabled,
                "dry_run": self._config.dry_run,
            },
            note="every check passed; nothing was sent. " + "; ".join(report.blockers()),
        )
        return TradeDecision(
            signal_id=signal_id,
            action=DecisionAction.DRY_RUN,
            reason=PASSED,
            explanation=(
                "every check passed and the trade is fully sized, and nothing was sent. "
                + ("The execution envelope is not wired." if self._envelope is None else "")
                + " Blockers: "
                + ("; ".join(report.blockers()) or "none")
            ),
            intent=intent,
            decided_at=self._now(),
            diagnostics={"stage": "complete", "report": report.to_dict()},
        )

    # -- the two outcomes --------------------------------------------------

    def _log_take_profit(
        self,
        signal_id: str,
        symbol: str,
        resolution: Resolution[TakeProfit | None],
        take_profit: TakeProfit | None,
        risk: RiskParameters,
    ) -> None:
        """Emit ``TAKE_PROFIT_RESOLVED``, including when there is no take profit.

        The ``NONE`` case emits too, and it has to: "a trade with no target because
        the operator disabled targets" is a decision somebody will want to find in
        a log, and an event that only fires on success would leave a silence
        indistinguishable from a stage that crashed.

        Merged as a dict rather than unpacked into the call, because the
        resolution's own ``details`` already carry ``signal_id``, ``symbol`` and
        ``policy`` -- and two ``**`` in one call is a ``TypeError`` rather than a
        merge. The explicit fields are applied last so they win, which keeps this
        readable even when the two sets disagree.
        """
        fields: dict[str, Any] = {
            **dict(resolution.details),
            "signal_id": signal_id,
            "symbol": symbol,
            "ok": True,
            "reason": "OK",
            "price": str(take_profit.price) if take_profit is not None else None,
            "distance": str(take_profit.distance) if take_profit is not None else None,
            "source": take_profit.source.value if take_profit is not None else "NONE",
            "basis": take_profit.basis if take_profit is not None else "",
            "policy": risk.take_profit_source.value,
            "has_take_profit": take_profit is not None,
        }
        self._log.event(Event.TAKE_PROFIT_RESOLVED, **fields)

    def _refuse(
        self,
        signal_id: str,
        stage: str,
        resolution: Resolution[Any],
    ) -> TradeDecision:
        """Turn a stage's refusal into a decision, and record why.

        The stage name goes into ``diagnostics`` as well as into the log, so a
        decision read back from an audit trail months later says *which check*
        refused and not merely that it did. Everything the stage knew on the way
        out goes with it: a refused trade often has a stop distance and a raw
        volume, and those are what make a refusal debuggable rather than merely
        negative.
        """
        reason = resolution.reason_code
        self._log.warning(
            Event.TRADE_REJECTED,
            **{
                **dict(resolution.details),
                "signal_id": signal_id,
                "stage": stage,
                "reason": reason,
                "explanation": resolution.explanation,
            },
        )
        return TradeDecision(
            signal_id=signal_id,
            action=DecisionAction.NO_TRADE,
            reason=reason,
            explanation=resolution.explanation,
            decided_at=self._now(),
            diagnostics={"stage": stage, **dict(resolution.details)},
        )


def budget_to_account(budget: RiskBudget) -> AccountBalance:
    """The account a budget was built from, for ``TradeIntent``'s record of it.

    Deliberately the same construction
    :meth:`RiskService.size_for <signal_to_trade_bridge.application.risk_service.RiskService>`
    uses for its currency check, so an intent and a currency refusal can never
    disagree about which account a budget came from.

    The result is **not** a live account: it carries the balance and the currency
    and nothing else. That is the point. The intent records the facts the size was
    computed *from*, and a record of what was decided must not be able to drift by
    re-reading a balance that has since moved.
    """
    return AccountBalance(balance=budget.balance, currency=budget.currency)


def _account_required(risk: RiskParameters) -> Resolution[Any]:
    """The refusal for "the account could not be read, and a gate needs it".

    Separate from the sizing refusal because it is a different fact failing at a
    different moment, and conflating them sends an operator to the wrong place.
    This one fires **only** when ``max_open_positions`` is configured, because
    that is the only gate that needs the count.

    Fail-closed, and that is the point. A concurrency limit that quietly stops
    applying when the terminal is unreachable is a risk control that switches off
    exactly when it is most needed.
    """
    return refused(
        RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE,
        (
            f"a limit of {risk.max_open_positions} open positions is configured and the count "
            f"cannot be read, so the gate is refused rather than skipped. A concurrency limit "
            f"that stops applying when the terminal is unreachable switches off exactly when it "
            f"is most needed."
        ),
        max_open_positions=risk.max_open_positions,
        account_read=False,
    )


def _spec_required(symbol: str) -> Resolution[Any]:
    """The refusal for "the symbol has no specification we can read".

    An unknown or unloaded symbol, or a terminal that did not answer. Refused
    rather than defaulted, because a default specification is an invented contract
    -- which is what the Phase 0 audit established neither upstream project has.
    """
    return refused(
        RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
        (
            f"no trading specification could be read for {symbol}. The later checks need it: the "
            f"tick size and tick value decide the position size, and the volume step and bounds "
            f"decide whether it is tradeable at all. The trade is refused rather than checked "
            f"against a guessed contract."
        ),
        symbol=symbol,
    )
