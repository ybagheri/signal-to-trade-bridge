"""The pre-submit pause and the order-comment policy.

Two execution/UI features, both off by default, both tested without ever
waiting: the delay mechanism is injected, so a test asserting "called exactly
once with a value in bounds" runs in milliseconds rather than seconds.

The ordering test below is the load-bearing one. It proves the pause happens
*after* the order is fully prepared and *before* the submission workflow runs
-- which is the bridge-level meaning of "after the form is filled, before the
final click", since the whole upstream UI interaction happens inside
``workflow.execute`` and no UI action has started when the pause is taken.
"""

from __future__ import annotations

import random
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from signal_to_trade_bridge.adapters.auto_trade.executor import AutoTradeExecutor
from signal_to_trade_bridge.adapters.fake.executor import FakeTradeExecutor
from signal_to_trade_bridge.application.pre_submit import roll_delay_ms
from signal_to_trade_bridge.domain.enums import Direction
from signal_to_trade_bridge.domain.models import (
    ExecutionRequest,
    PreSubmitDelay,
)


def _request(**overrides: object) -> ExecutionRequest:
    defaults: dict[str, object] = {
        "signal_id": "stb-pre-submit-0001",
        "symbol": "EURUSD",
        "direction": Direction.LONG,
        "volume": Decimal("0.12"),
        "entry": Decimal("1.08500"),
        "stop_loss": Decimal("1.08300"),
        "take_profit": Decimal("1.08900"),
        "comment": "",
        "strategy": "pullback_h#0",
        "evidence_score": 0.62,
        "metadata": {},
    }
    defaults.update(overrides)
    return ExecutionRequest(**defaults)  # type: ignore[arg-type]


class _NullLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def event(self, event: object, /, **fields: object) -> None:
        self.events.append((str(event), fields))

    def warning(self, *args: object, **fields: object) -> None:
        return None

    def debug(self, *args: object, **fields: object) -> None:
        return None


class _Workflow:
    """A workflow double that records the call sequence, not just the signal."""

    def __init__(self, status: str = "ACCEPTED") -> None:
        self.calls: list[str] = []
        self.status = status

    def execute(self, signal: object) -> SimpleNamespace:
        self.calls.append("execute")
        return SimpleNamespace(
            status=self.status,
            state="",
            message="",
            order_reference=None,
            error=None,
            evidence=None,
        )


def _bindings() -> SimpleNamespace:
    """The slice of upstream bindings the executor reads, as a namespace."""

    def order_action(action: str) -> str:
        return action

    def trade_signal(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(**kwargs)

    return SimpleNamespace(
        order_action=order_action,
        TradeSignal=trade_signal,
        ExecutionStatus=SimpleNamespace(
            ACCEPTED="ACCEPTED",
            REQUESTED="REQUESTED",
            REJECTED="REJECTED",
            UNKNOWN="UNKNOWN",
            DRY_RUN="DRY_RUN",
        ),
    )


def _executor(
    workflow: _Workflow | None = None,
    *,
    policy: PreSubmitDelay | None = None,
    calls: list[tuple[str, float]] | None = None,
    seed: int = 0,
    logger: _NullLog | None = None,
    **kwargs: object,
) -> AutoTradeExecutor:
    if calls is None:
        calls = []

    def sleeper(seconds: float) -> None:
        calls.append(("sleep", seconds))

    return AutoTradeExecutor(
        workflow if workflow is not None else _Workflow(),
        bindings=_bindings(),  # type: ignore[arg-type]
        pre_submit_delay=policy,
        sleeper=sleeper,
        rng=random.Random(seed),
        logger=logger if logger is not None else _NullLog(),
        **kwargs,  # type: ignore[arg-type]
    )


class TestPolicyValidation:
    def test_disabled_by_default_with_documented_bounds(self) -> None:
        policy = PreSubmitDelay()
        assert policy.enabled is False
        assert policy.min_ms == 1000
        assert policy.max_ms == 5000

    def test_a_zero_lower_bound_is_legitimate(self) -> None:
        # Zero means "up to MAX with no floor", which is a real configuration.
        # A validator treating 0 as missing would silently replace it.
        assert PreSubmitDelay(enabled=True, min_ms=0, max_ms=100).min_ms == 0

    def test_equal_bounds_are_a_fixed_pause(self) -> None:
        assert PreSubmitDelay(enabled=True, min_ms=750, max_ms=750).roll_ms(random.Random(0)) == 750

    def test_a_negative_minimum_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_ms"):
            PreSubmitDelay(enabled=True, min_ms=-1)

    def test_max_below_min_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_ms"):
            PreSubmitDelay(enabled=True, min_ms=5000, max_ms=1000)

    @pytest.mark.parametrize("bad", [True, False, 1.5, "1000", None])
    def test_non_integers_are_rejected(self, bad: object) -> None:
        # `isinstance(True, int)` is true, so a naive check would accept a
        # boolean bound -- and `randint(True, ...)` would then silently work.
        with pytest.raises(ValueError, match="integer"):
            PreSubmitDelay(enabled=True, min_ms=bad)  # type: ignore[arg-type]

    def test_an_absurd_maximum_is_rejected(self) -> None:
        # A seconds-versus-milliseconds mistake (e.g. 3600000000) must fail at
        # construction, not sleep for 41 days.
        with pytest.raises(ValueError, match="sanity bound"):
            PreSubmitDelay(enabled=True, min_ms=0, max_ms=10_000_000)


class TestRollBounds:
    def test_disabled_rolls_none(self) -> None:
        assert PreSubmitDelay().roll_ms(random.Random(0)) is None

    def test_no_policy_rolls_none(self) -> None:
        assert roll_delay_ms(None, random.Random(0)) is None

    def test_a_disabled_policy_rolls_none(self) -> None:
        assert roll_delay_ms(PreSubmitDelay(), random.Random(0)) is None

    def test_draws_stay_within_bounds(self) -> None:
        policy = PreSubmitDelay(enabled=True, min_ms=1000, max_ms=5000)
        rng = random.Random(1234)
        draws = [roll_delay_ms(policy, rng) for _ in range(200)]
        assert draws
        assert all(d is not None and 1000 <= d <= 5000 for d in draws)

    def test_draws_are_inclusive_on_both_ends(self) -> None:
        # `randint` is inclusive; a reimplementation with `randrange` would make
        # the configured maximum unreachable, and the bound would be a lie.
        policy = PreSubmitDelay(enabled=True, min_ms=0, max_ms=1)
        seen = {roll_delay_ms(policy, random.Random(seed)) for seed in range(50)}
        assert seen == {0, 1}

    def test_a_seeded_draw_is_reproducible(self) -> None:
        policy = PreSubmitDelay(enabled=True, min_ms=1000, max_ms=5000)
        first = roll_delay_ms(policy, random.Random(7))
        second = roll_delay_ms(policy, random.Random(7))
        assert first == second


class TestExecutorOrdering:
    def test_no_pause_no_sleep_no_log_when_disabled(self) -> None:
        workflow, calls, log = _Workflow(), [], _NullLog()
        result = _executor(workflow, calls=calls, logger=log).submit(_request())
        assert result.is_accepted
        assert calls == []
        assert workflow.calls == ["execute"]
        assert log.events == []

    def test_no_policy_means_no_pause(self) -> None:
        workflow, calls = _Workflow(), []
        _executor(workflow, calls=calls, policy=None).submit(_request())
        assert calls == []
        assert workflow.calls == ["execute"]

    def test_enabled_pauses_exactly_once_before_the_workflow(self) -> None:
        workflow, calls, log = _Workflow(), [], _NullLog()
        policy = PreSubmitDelay(enabled=True, min_ms=1000, max_ms=5000)
        result = _executor(workflow, policy=policy, calls=calls, logger=log).submit(_request())
        assert result.is_accepted
        assert len(calls) == 1
        name, seconds = calls[0]
        assert name == "sleep"
        assert 1.0 <= seconds <= 5.0
        assert workflow.calls == ["execute"]
        delay_events = [e for e in log.events if e[0] == "PRE_SUBMIT_DELAY_APPLIED"]
        assert len(delay_events) == 1
        assert delay_events[0][1]["delay_ms"] == round(seconds * 1000)
        assert delay_events[0][1]["signal_id"] == "stb-pre-submit-0001"

    def test_the_pause_is_deterministic_for_a_seeded_generator(self) -> None:
        policy = PreSubmitDelay(enabled=True, min_ms=1000, max_ms=5000)
        first_calls: list[tuple[str, float]] = []
        second_calls: list[tuple[str, float]] = []
        _executor(_Workflow(), policy=policy, calls=first_calls, seed=42).submit(_request())
        _executor(_Workflow(), policy=policy, calls=second_calls, seed=42).submit(_request())
        assert first_calls == second_calls

    def test_a_refused_order_does_not_pause(self) -> None:
        # The kill switch refuses before the workflow runs. A pause spent on an
        # order that will not go out is waiting for nothing.
        workflow, calls = _Workflow(), []

        class _Engaged:
            active = True

        result = _executor(
            workflow,
            calls=calls,
            policy=PreSubmitDelay(enabled=True),
            kill_switch=_Engaged(),
        ).submit(_request())
        assert result.is_rejected
        assert calls == []
        assert workflow.calls == []


class TestTheRecorderMirrorsWithoutWaiting:
    def test_an_enabled_policy_is_recorded_not_slept(self) -> None:
        # Sixty minutes configured, milliseconds elapsed: the structural proof
        # that the recorder never waits, rather than a timing assertion with a
        # margin somebody will one day shrink.
        recorder = FakeTradeExecutor(
            pre_submit_delay=PreSubmitDelay(enabled=True, min_ms=3_600_000, max_ms=3_600_000),
            rng=random.Random(3),
        )
        started = time.monotonic()
        recorder.submit(_request())
        elapsed = time.monotonic() - started
        assert recorder.pre_submit_delays == [3_600_000]
        assert elapsed < 5.0

    def test_recorded_draws_stay_within_bounds(self) -> None:
        recorder = FakeTradeExecutor(
            pre_submit_delay=PreSubmitDelay(enabled=True, min_ms=1000, max_ms=5000),
            rng=random.Random(11),
        )
        for _ in range(20):
            recorder.submit(_request())
        assert len(recorder.pre_submit_delays) == 20
        assert all(1000 <= d <= 5000 for d in recorder.pre_submit_delays)

    def test_disabled_records_nothing(self) -> None:
        recorder = FakeTradeExecutor()
        recorder.submit(_request())
        assert recorder.submitted
        assert recorder.pre_submit_delays == []


class TestCommentPolicyThroughThePipeline:
    """The configuration flag reaches the order the pipeline would send.

    The unit tests above pin the mapping; this pins the wiring -- that
    `ProcessSignal` actually passes its own configuration through rather than
    the flag existing nowhere but the parser.
    """

    def _decision_comment(self, comment_enabled: bool) -> str:
        from signal_to_trade_bridge.adapters.fake import (
            FakeAccountProvider,
            FakeSymbolSpecProvider,
            eurusd_spec,
        )
        from signal_to_trade_bridge.application.process_signal import ProcessSignal
        from signal_to_trade_bridge.application.risk_service import RiskService
        from signal_to_trade_bridge.configuration.config import BridgeConfig
        from signal_to_trade_bridge.domain.enums import SignalAction
        from signal_to_trade_bridge.domain.models import AccountBalance, Signal

        provider = FakeAccountProvider(AccountBalance(balance=Decimal("10000"), currency="USD"))
        symbols = FakeSymbolSpecProvider({"EURUSD": eurusd_spec()})
        config = BridgeConfig(order_comment_enabled=comment_enabled)
        pipeline = ProcessSignal(RiskService(provider, symbols), config)
        signal = Signal(
            signal_id="stb-pre-submit-0002",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
            take_profit=Decimal("1.10300"),
            stop_basis="PULLBACK_EXTREME",
            take_profit_basis="SWING",
            setup_id="pullback_h#0",
            bar_index=299,
            bar_time=1727740800.0,
            source="albrooks",
        )
        decision = pipeline.process(signal)
        assert decision.intent is not None
        return str(decision.diagnostics["report"]["request"]["comment"])

    def test_default_config_sends_an_empty_comment(self) -> None:
        assert self._decision_comment(comment_enabled=False) == ""

    def test_enabled_config_sends_the_bridge_comment(self) -> None:
        assert self._decision_comment(comment_enabled=True) == ("stb EURUSD LONG pullback_h#0")


class TestCommentPolicy:
    def _intent(self) -> SimpleNamespace:
        # Everything `build_execution_request` reads, and nothing it does not.
        # A namespace rather than a real `TradeIntent` because the request only
        # copies fields off it -- and importing another test module's fixture
        # would couple this file to a helper it does not own.
        intent = SimpleNamespace(
            signal=SimpleNamespace(
                signal_id="stb-pre-submit-0001",
                setup_id="pullback_h#0",
                evidence_score=0.72,
            ),
            symbol="EURUSD",
            direction=Direction.LONG,
            volume=Decimal("0.16"),
            entry=Decimal("1.10000"),
            stop_loss=SimpleNamespace(price=Decimal("1.09700")),
            take_profit=SimpleNamespace(price=Decimal("1.10300")),
            risk_amount=Decimal("50"),
            position_size=SimpleNamespace(planned_loss=Decimal("49.50")),
            reward_to_risk=Decimal("1"),
            account_balance=SimpleNamespace(balance=Decimal("10000"), currency="USD"),
            symbol_spec=SimpleNamespace(),
        )
        intent.to_dict = lambda: {"signal_id": "stb-pre-submit-0001"}  # type: ignore[attr-defined]
        return intent

    def test_default_builds_an_empty_comment(self) -> None:
        from signal_to_trade_bridge.application.dry_run import build_execution_request

        assert build_execution_request(self._intent()).comment == ""  # type: ignore[arg-type]

    def test_enabled_builds_the_existing_comment_value(self) -> None:
        from signal_to_trade_bridge.application.dry_run import (
            build_execution_request,
            default_comment,
        )

        intent = self._intent()
        assert build_execution_request(intent, comment_enabled=True).comment == (  # type: ignore[arg-type]
            default_comment(intent)  # type: ignore[arg-type]
        )

    def test_report_for_passes_the_policy_through(self) -> None:
        from signal_to_trade_bridge.application.dry_run import report_for

        plain = report_for(
            self._intent(),  # type: ignore[arg-type]
            execution_enabled=False,
            dry_run=True,
        )
        assert plain.request is not None and plain.request.comment == ""
        enabled = report_for(
            self._intent(),  # type: ignore[arg-type]
            execution_enabled=False,
            dry_run=True,
            comment_enabled=True,
        )
        assert enabled.request is not None
        assert enabled.request.comment.startswith("stb ")
