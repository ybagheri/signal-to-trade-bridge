"""The pipeline reaching an executor: the four paths, and what makes each safe.

Phase 9's behavioural half. ``test_execution_envelope`` covers the *type*; this
covers what the pipeline does once it holds one.

The four paths, and the two that are easiest to get wrong:

* **not wired** -- the default. Nothing is sent and nothing can be.
* **wired, not enabled** -- the envelope is present so the ledger and the kill
  switch are, and the default configuration still refuses. Enabling the envelope is
  not enabling execution, and conflating them is how a dry run becomes a live one
  without anybody deciding to.
* **wired and enabled, duplicate** -- the ledger already holds this signal id, so
  nothing is sent. Checked **before** the configuration, because "this trade already
  happened" is true whether or not we were about to place it.
* **wired, enabled, live** -- sent, and the result mapped honestly.

Plus the failure paths, because they are where a decision gets lost: an executor
that raises must produce a recorded ``UNKNOWN`` rather than a traceback, and an
``UNKNOWN`` must never be retryable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from signal_to_trade_bridge.adapters.fake import FakeTradeExecutor
from signal_to_trade_bridge.application.execution_envelope import (
    build_execution_envelope,
)
from signal_to_trade_bridge.domain.enums import DecisionAction, RejectionReason
from signal_to_trade_bridge.domain.models import ExecutionResult

from .test_process_signal import _pipeline, _signal

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class _Store:
    """An idempotency store over a set, matching the two-phase port."""

    def __init__(self, recorded: set[str] | None = None) -> None:
        self.recorded = set(recorded or ())
        self.attempts: list[tuple[str, str]] = []
        self.outcomes: list[tuple[str, str, dict]] = []

    def contains(self, key: str) -> bool:
        return key in self.recorded

    def record_attempt(self, key: str, execution_id: str) -> None:
        self.attempts.append((key, execution_id))
        self.recorded.add(key)

    def record_outcome(self, key: str, execution_id: str, outcome) -> None:  # type: ignore[no-untyped-def]
        self.outcomes.append((key, execution_id, dict(outcome)))


class _Switch:
    def __init__(self, active: bool = False) -> None:
        self.active = active


def _live(executor: object | None = None, **overrides: object):
    """A pipeline wired for real sending: execution enabled, dry-run off.

    ``_pipeline()`` returns ``(pipeline, account, symbols)`` -- the *data* fakes,
    not an executor -- so the executor is this module's own. Passing one in is how a
    test drives a specific result; leaving it out gives a ``FakeTradeExecutor``,
    which answers ``DRY_RUN`` unless told otherwise.
    """
    from signal_to_trade_bridge.configuration.config import BridgeConfig
    from signal_to_trade_bridge.domain.models import RiskParameters

    pipeline, _, _ = _pipeline()
    pipeline._config = BridgeConfig(risk=RiskParameters(), execution_enabled=True, dry_run=False)
    fake = FakeTradeExecutor() if executor is None else executor
    kwargs: dict[str, object] = {
        "executor": fake,
        "idempotency": _Store(),
        "kill_switch": _Switch(),
    }
    kwargs.update(overrides)
    pipeline.wire_execution(build_execution_envelope(**kwargs))  # type: ignore[arg-type]
    return pipeline, fake


def _accepted(signal_id: str, position_id: str = "382363348") -> ExecutionResult:
    return ExecutionResult(
        signal_id=signal_id, status=ExecutionResult.STATUS_ACCEPTED, position_id=position_id
    )


class TestTheDefaultIsStillSafe:
    def test_an_unwired_pipeline_sends_nothing(self) -> None:
        # Nothing is even reachable, so the executor here is never handed to it --
        # which is the point being made by the type, not by this test.
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.DRY_RUN
        assert decision.execution is None

    def test_wiring_alone_does_not_execute(self) -> None:
        # The envelope carries the ledger and the kill switch, and the default
        # configuration still refuses. Two separate acts.
        executor = FakeTradeExecutor()
        pipeline, _, _ = _pipeline()
        pipeline.wire_execution(build_execution_envelope(executor, _Store(), _Switch()))
        assert pipeline.can_execute is False
        assert pipeline.process(_signal()).action is DecisionAction.DRY_RUN
        assert executor.submitted == []

    def test_execution_enabled_but_dry_run_on_still_sends_nothing(self) -> None:
        from signal_to_trade_bridge.configuration.config import BridgeConfig
        from signal_to_trade_bridge.domain.models import RiskParameters

        executor = FakeTradeExecutor()
        pipeline, _, _ = _pipeline()
        pipeline._config = BridgeConfig(risk=RiskParameters(), execution_enabled=True, dry_run=True)
        pipeline.wire_execution(build_execution_envelope(executor, _Store(), _Switch()))
        assert pipeline.can_execute is False
        assert pipeline.process(_signal()).action is DecisionAction.DRY_RUN
        assert executor.submitted == []


class TestTheDuplicateIsRefusedBeforeSending:
    def test_a_recorded_signal_id_is_not_sent_again(self) -> None:
        # THE test. A re-delivery of the same bar must not become a second
        # position, and it must be refused *before* the executor is reached.
        signal_id = _signal().signal_id
        store = _Store({signal_id})
        pipeline, executor = _live(idempotency=store)

        decision = pipeline.process(_signal())

        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == RejectionReason.DUPLICATE_SIGNAL.value
        assert executor.submitted == []

    def test_the_duplicate_check_runs_before_the_configuration(self) -> None:
        # "This trade already happened" is true whether or not execution is
        # enabled. Checking it second would mean a duplicate on a live pipeline
        # reached the executor before the ledger was consulted.
        signal_id = _signal().signal_id
        pipeline, executor = _live(idempotency=_Store({signal_id}))
        pipeline._config = pipeline._config.__class__(
            risk=pipeline._config.risk, execution_enabled=False, dry_run=True
        )
        decision = pipeline.process(_signal())
        assert decision.reason == RejectionReason.DUPLICATE_SIGNAL.value
        assert executor.submitted == []

    def test_the_refusal_names_the_ledger_as_the_stage(self) -> None:
        signal_id = _signal().signal_id
        pipeline, _ = _live(idempotency=_Store({signal_id}))
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "idempotency"

    def test_an_unrecorded_signal_id_goes_through(self) -> None:
        # The positive case: without it the class above would pass with a store that
        # contains everything.
        store = _Store()
        pipeline, executor = _live(idempotency=store)
        decision = pipeline.process(_signal())
        assert decision.action is not DecisionAction.NO_TRADE
        assert len(executor.submitted) == 1


class TestTheKillSwitchStopsIt:
    def test_an_engaged_switch_sends_nothing(self) -> None:
        pipeline, executor = _live(kill_switch=_Switch(active=True))
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == RejectionReason.KILL_SWITCH_ACTIVE.value
        assert executor.submitted == []

    def test_it_is_a_refusal_rather_than_an_unknown(self) -> None:
        # Nothing was sent, which is known rather than uncertain -- so `REJECTED`,
        # not `UNKNOWN`. An operator needs to tell "we chose not to" from "we could
        # not tell".
        pipeline, _ = _live(kill_switch=_Switch(active=True))
        decision = pipeline.process(_signal())
        assert decision.execution is None
        assert decision.reason == RejectionReason.KILL_SWITCH_ACTIVE.value

    def test_a_clear_switch_lets_the_order_through(self) -> None:
        pipeline, executor = _live(kill_switch=_Switch(active=False))
        assert pipeline.process(_signal()).action is not DecisionAction.NO_TRADE
        assert len(executor.submitted) == 1


class TestSendingAndRecording:
    def test_an_accepted_result_becomes_an_execute(self) -> None:
        accepted = ExecutionResult(
            signal_id=_signal().signal_id,
            status=ExecutionResult.STATUS_ACCEPTED,
            position_id="382363348",
        )
        executor = FakeTradeExecutor(result=accepted)
        pipeline, _ = _live(executor=executor)

        decision = pipeline.process(_signal())

        assert decision.action is DecisionAction.EXECUTE
        assert decision.is_trade is True
        assert decision.execution is accepted
        assert decision.reason == "PIPELINE_PASSED"

    def test_a_downstream_dry_run_stays_a_dry_run(self) -> None:
        # Two configurations, and the downstream one wins. The execution project's
        # own policy can refuse to execute even when the bridge's allows it, and
        # reporting that as a refusal would be wrong -- it validated, and chose not
        # to send.
        executor = FakeTradeExecutor(
            result=ExecutionResult(
                signal_id=_signal().signal_id, status=ExecutionResult.STATUS_DRY_RUN
            )
        )
        pipeline, _ = _live(executor=executor)

        decision = pipeline.process(_signal())

        assert decision.action is DecisionAction.DRY_RUN
        assert decision.is_trade is False
        assert decision.execution is not None

    def test_an_unknown_result_is_a_refusal_and_never_retryable(self) -> None:
        # Rule 5. The outcome could not be determined, so it is escalated rather
        # than resent.
        executor = FakeTradeExecutor(
            result=ExecutionResult(
                signal_id=_signal().signal_id, status=ExecutionResult.STATUS_UNKNOWN
            )
        )
        pipeline, _ = _live(executor=executor)

        decision = pipeline.process(_signal())

        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == ExecutionResult.STATUS_UNKNOWN
        assert decision.execution is not None
        assert decision.execution.is_retryable is False

    def test_a_rejected_result_keeps_the_rejection_status_as_the_reason(self) -> None:
        # Carried verbatim rather than flattened, so nothing the execution project
        # said is lost on the way through.
        executor = FakeTradeExecutor(
            result=ExecutionResult(
                signal_id=_signal().signal_id,
                status=ExecutionResult.STATUS_REJECTED,
                message="symbol is not allowed",
            )
        )
        pipeline, _ = _live(executor=executor)
        decision = pipeline.process(_signal())
        assert decision.reason == ExecutionResult.STATUS_REJECTED
        assert "symbol is not allowed" in decision.explanation

    def test_the_order_sent_carries_the_sized_numbers(self) -> None:
        # Not a smoke test: the whole of Phases 4 and 5 is in these four numbers,
        # and an executor that received different ones would place a different
        # trade.
        pipeline, executor = _live()
        pipeline.process(_signal())
        request = executor.submitted[0]
        assert request.stop_loss > 0
        assert request.entry > 0
        assert request.volume > 0
        assert request.symbol == "EURUSD"


class TestTheFailurePathKeepsItsRecord:
    def test_an_executor_that_raises_becomes_an_unknown_not_a_crash(self) -> None:
        # A decision whose recording failed must still be recorded. An exception
        # propagating out of here takes the record with it -- and this is the one
        # outcome that most needs one.
        executor = FakeTradeExecutor(error=RuntimeError("the terminal vanished"))
        pipeline, _ = _live(executor=executor)

        decision = pipeline.process(_signal())

        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == ExecutionResult.STATUS_UNKNOWN
        assert decision.execution is not None
        assert "the terminal vanished" in (decision.execution.error or "")

    def test_the_raised_error_is_never_retryable(self) -> None:
        executor = FakeTradeExecutor(error=RuntimeError("boom"))
        pipeline, _ = _live(executor=executor)
        decision = pipeline.process(_signal())
        assert decision.execution is not None
        assert decision.execution.is_retryable is False


class TestTheReportIsEmittedOnEveryPath:
    @pytest.mark.parametrize(
        ("label", "live"),
        [
            ("not wired", False),
            ("wired and live", True),
        ],
    )
    def test_the_report_reaches_the_diagnostics(self, label: str, live: bool) -> None:
        # Emitted on the live path too. The first real order is exactly the moment
        # somebody will want to read what was decided, and an event that only fires
        # when nothing happens is silent on the day it starts mattering.
        if live:
            pipeline, _ = _live()
        else:
            pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())
        report = decision.diagnostics["report"]
        assert report["request"]["symbol"] == "EURUSD"
        assert report["blockers"] is not None

    def test_the_sizing_is_in_the_report_on_the_live_path(self) -> None:
        pipeline, _ = _live()
        decision = pipeline.process(_signal())
        arithmetic = decision.diagnostics["report"]["arithmetic"]
        assert Decimal(arithmetic["volume"]) > 0
        assert Decimal(arithmetic["planned_loss"]) > 0
        assert arithmetic["currency"] == "USD"


class _EmptySymbols:
    """A symbol provider that knows nothing, so every symbol is unknown."""

    def spec(self, _symbol: str) -> object:
        from signal_to_trade_bridge.adapters.mt5 import MT5Unavailable

        raise MT5Unavailable("this terminal does not know that symbol")


class TestTheRefusalsStillStopWhereTheyDid:
    """The wiring changed the *end* of the pipeline, not its refusals.

    Worth pinning after a phase that rewires a use case: the temptation is to prove
    the new paths work and leave the old refusals to the pre-existing suite, which
    would not notice if a wiring change had made one unreachable. Each of these
    refuses **before** any executor is touched.
    """

    def test_a_signal_with_no_stop_never_reaches_the_executor(self) -> None:
        # Rule 1. The one refusal this project exists to make.
        executor = FakeTradeExecutor()
        pipeline, _ = _live(executor=executor)
        decision = pipeline.process(_signal(stop=None))
        assert decision.action is DecisionAction.NO_TRADE
        assert executor.submitted == []

    def test_a_stop_on_the_wrong_side_never_reaches_the_executor(self) -> None:
        # Rule 8: refused here because the execution layer does not check it and
        # would let the broker refuse instead. Entry is 1.10000, so a stop *above*
        # it is wrong for a BUY.
        executor = FakeTradeExecutor()
        pipeline, _ = _live(executor=executor)
        decision = pipeline.process(_signal(stop="1.10500"))
        assert decision.action is DecisionAction.NO_TRADE
        assert executor.submitted == []

    def test_a_signal_with_no_stop_and_no_target_never_reaches_the_executor(self) -> None:
        # Both absent. The resolver is allowed to substitute the signal's own target,
        # so this still reaches a decision -- and the point is only that nothing was
        # sent without one.
        executor = FakeTradeExecutor()
        pipeline, _ = _live(executor=executor)
        pipeline.process(_signal(stop=None, target=None))
        assert executor.submitted == []

    def test_a_volatility_fallback_stop_is_refused_by_default(self) -> None:
        # A stop that is not a level the market produced is refused unless the
        # operator has explicitly permitted it -- and the default does not.
        executor = FakeTradeExecutor()
        pipeline, _ = _live(executor=executor)
        decision = pipeline.process(_signal(stop_basis="ATR_MULTIPLE"))
        assert decision.action is DecisionAction.NO_TRADE
        assert executor.submitted == []

    def test_an_unknown_symbol_never_reaches_the_executor(self) -> None:
        # A refused specification rather than an invented contract -- and the Phase 0
        # finding was that neither upstream project can supply one.
        executor = FakeTradeExecutor()
        pipeline, _, _ = _pipeline()
        pipeline._config = pipeline._config.__class__(
            risk=pipeline._config.risk, execution_enabled=True, dry_run=False
        )
        pipeline.wire_execution(build_execution_envelope(executor, _Store(), _Switch()))
        pipeline._risk._symbols = _EmptySymbols()  # type: ignore[attr-defined]
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        assert executor.submitted == []

    def test_a_volume_below_the_broker_minimum_never_reaches_the_executor(self) -> None:
        # Rule 2, the most dangerous line in any position sizer. A floor-up here
        # would place a position risking far more than the budget allows.
        from decimal import Decimal as D

        from signal_to_trade_bridge.domain.models import RiskParameters

        executor = FakeTradeExecutor()
        pipeline, _, _ = _pipeline()
        pipeline._config = pipeline._config.__class__(
            risk=RiskParameters(risk_percent=D("0.0000001")),
            execution_enabled=True,
            dry_run=False,
        )
        pipeline.wire_execution(build_execution_envelope(executor, _Store(), _Switch()))
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        assert executor.submitted == []

    def test_the_happy_path_still_sends(self) -> None:
        # The positive case for the whole class. Without it, a `pipeline` that
        # refused everything would pass every test above.
        executor = FakeTradeExecutor()
        pipeline, _ = _live(executor=executor)
        assert pipeline.process(_signal()).action is not DecisionAction.NO_TRADE
        assert len(executor.submitted) == 1
