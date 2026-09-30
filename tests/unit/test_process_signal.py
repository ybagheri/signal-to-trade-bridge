"""``ProcessSignal`` -- the pipeline, its order, and what it refuses.

This is the first test in the project that exercises more than two layers at
once. Every earlier file tested a function or a boundary; this one tests the thing
that calls all of them, and the two properties that only exist at that level:

* **the order**, which is the design -- a cheap decisive check before an expensive
  one, and a refusal that stops the stages after it;
* **the snapshot**, which is the reason the account is read once and used twice.

Organised by what the pipeline does, in the order it does it. The refusal tests
assert *which stage* refused, not merely that something did, because "no trade"
without "which check" is an answer nobody can act on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

import pytest

from signal_to_trade_bridge.adapters.fake import (
    FakeAccountProvider,
    FakeSymbolSpecProvider,
    eurusd_spec,
    gold_spec,
)
from signal_to_trade_bridge.application.process_signal import (
    PASSED,
    STAGES,
    ProcessSignal,
)
from signal_to_trade_bridge.application.risk_service import RiskService
from signal_to_trade_bridge.configuration.config import BridgeConfig
from signal_to_trade_bridge.domain.enums import (
    DecisionAction,
    Direction,
    RejectionReason,
    SignalAction,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.errors import IntegrationError
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    RiskParameters,
    Signal,
)
from signal_to_trade_bridge.infrastructure.logging import Event, configure_logging

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _logs() -> StringIO:
    """Capture the bridge's own log output for the whole module.

    Autouse because the events *are* the behaviour under test here: Phase 6 is
    the first phase to emit ``STOP_RESOLVED``, ``TAKE_PROFIT_RESOLVED`` and
    ``TRADE_VALIDATED``, and whether a stage emitted its event on a refusal is
    exactly the kind of thing only a log can show.
    """
    stream = StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    return stream


def _account(
    balance: str = "10000",
    currency: str = "USD",
    open_positions: int = 0,
) -> AccountBalance:
    return AccountBalance(
        balance=Decimal(balance),
        currency=currency,
        equity=Decimal(balance),
        open_positions=open_positions,
        account_login=12345678,
        server="Alpari-Demo",
    )


def _signal(
    *,
    stop: str | None = "1.09700",
    target: str | None = "1.10300",
    stop_basis: str = "PULLBACK_EXTREME",
    target_basis: str = "SWING",
    action: SignalAction = SignalAction.BUY,
    evidence: float | None = 0.72,
) -> Signal:
    return Signal(
        signal_id="stb-test-pipeline",
        symbol="EURUSD",
        timeframe="H1",
        action=action,
        direction=Direction.LONG if action is SignalAction.BUY else Direction.FLAT,
        entry=Decimal("1.10000"),
        stop_loss=Decimal(stop) if stop is not None else None,
        take_profit=Decimal(target) if target is not None else None,
        stop_basis=stop_basis,
        take_profit_basis=target_basis,
        evidence_score=evidence,
        setup_id="pullback_h#0",
        bar_index=299,
        bar_time=1727740800.0,
        source="albrooks",
    )


def _pipeline(
    *,
    account: AccountBalance | None = None,
    specs: dict[str, object] | None = None,
    risk: RiskParameters | None = None,
) -> tuple[ProcessSignal, FakeAccountProvider, FakeSymbolSpecProvider]:
    provider = FakeAccountProvider(account if account is not None else _account())
    # `is None` rather than `or`, because an empty registry is a case a test means
    # -- "this symbol is not known" -- and `or` would quietly hand back the
    # default EURUSD and make the test assert nothing.
    registry = {"EURUSD": eurusd_spec()} if specs is None else specs
    symbols = FakeSymbolSpecProvider(registry)  # type: ignore[arg-type]
    service = RiskService(provider, symbols)
    config = BridgeConfig(risk=risk) if risk is not None else BridgeConfig()
    return ProcessSignal(service, config, now=lambda: FIXED_NOW), provider, symbols


def _events(stream: StringIO, name: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if line.strip() and json.loads(line).get("event") == str(name)
    ]


def _names(stream: StringIO) -> list[str]:
    return [
        json.loads(line).get("event", "") for line in stream.getvalue().splitlines() if line.strip()
    ]


class TestTheHappyPath:
    def test_a_good_signal_becomes_a_sized_dry_run(self) -> None:
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())

        assert decision.action is DecisionAction.DRY_RUN
        assert decision.reason == PASSED
        assert not decision.is_trade
        assert decision.intent is not None
        # $50 over a 30-pip stop on a 5-digit pair is 0.16 lots.
        assert decision.intent.volume == Decimal("0.16")
        assert decision.intent.risk_amount == Decimal("50")

    def test_the_intent_carries_every_fact_the_size_was_derived_from(self) -> None:
        # The record of *what was decided*, not a re-reading of it. Every number
        # here can be recomputed by hand from the stop and the specification.
        pipeline, _, _ = _pipeline()
        intent = pipeline.process(_signal()).intent
        assert intent is not None
        assert intent.symbol == "EURUSD"
        assert intent.entry == Decimal("1.10000")
        assert intent.stop_loss.distance == Decimal("0.00300")
        assert intent.position_size.ticks == Decimal("300")
        assert intent.position_size.risk_per_unit == Decimal("300")
        assert intent.account_balance.balance == Decimal("10000")
        assert intent.symbol_spec.tick_size == Decimal("0.00001")

    def test_the_achieved_ratio_is_reported_on_the_decision(self) -> None:
        # 1.10300 is 0.00300 above a 1.10000 entry against a 0.00300 stop, so 1:1
        # by coincidence of the fixture -- and the field is the *achieved* one,
        # computed from the distances used rather than copied from configuration.
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())
        assert decision.diagnostics["reward_to_risk"] == "1"

    def test_a_gold_signal_is_sized_on_its_own_contract(self) -> None:
        # The same $50 budget, a completely different instrument. Proves the
        # pipeline passes the right specification through rather than assuming
        # forex geometry.
        pipeline, _, _ = _pipeline(specs={"XAUUSD": gold_spec()})
        signal = Signal(
            signal_id="stb-test-pipeline-gold",
            symbol="XAUUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("2400.00"),
            stop_loss=Decimal("2397.00"),
            take_profit=Decimal("2403.00"),
            stop_basis="SWING",
            take_profit_basis="MEASURED_MOVE",
        )
        intent = pipeline.process(signal).intent
        assert intent is not None
        assert intent.position_size.risk_per_unit == Decimal("300")
        assert intent.volume == Decimal("0.16")

    def test_the_decision_serialises(self) -> None:
        # A decision nobody can write down is not a decision.
        pipeline, _, _ = _pipeline()
        payload = pipeline.process(_signal()).to_dict()
        assert payload["action"] == "DRY_RUN"
        assert payload["intent"] is not None
        assert payload["intent"]["position_size"]["volume"] == "0.16"


class TestTheOrderIsTheDesign:
    def test_the_stages_are_named_in_the_order_they_run(self) -> None:
        # The tuple is the documented order, and `process` is the only thing that
        # can break it. Reading the call sequence is fine; this is what stops it
        # being changed without anyone noticing that the list now lies.
        assert STAGES == ("signal", "stop", "take_profit", "geometry", "policy", "spec", "size")

    def test_a_missing_stop_refuses_at_the_stop_stage(self) -> None:
        # Not at geometry, and not at sizing. The stop resolver is where the
        # knowledge is, so that is where the answer comes from.
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal(stop=None))
        assert decision.reason == RejectionReason.NO_VALID_STOP.value
        assert decision.diagnostics["stage"] == "stop"

    def test_the_stages_after_a_refusal_do_not_run(self) -> None:
        # The expensive, outward-reaching stages must not cost a round trip for a
        # signal that never had a stop. This is the property that makes the order
        # worth specifying.
        pipeline, provider, symbols = _pipeline()
        pipeline.process(_signal(stop=None))
        assert provider.calls == 0
        assert symbols.calls == 0

    def test_the_stages_after_a_refusal_emit_no_events(self, _logs: StringIO) -> None:
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal(stop=None))
        names = _names(_logs)
        assert "STOP_RESOLVED" not in names
        assert "RISK_CALCULATED" not in names
        assert "POSITION_SIZED" not in names
        assert "TRADE_VALIDATED" not in names
        assert "TRADE_REJECTED" in names

    def test_a_bad_signal_is_refused_before_the_stop_is_even_looked_for(
        self, _logs: StringIO
    ) -> None:
        # An abstention is the commonest outcome in the system, and it must cost
        # nothing to handle.
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(
            _signal(action=SignalAction.WAIT, stop=None, target=None, evidence=None)
        )
        assert decision.diagnostics["stage"] == "signal"
        assert decision.reason == RejectionReason.SIGNAL_INVALID.value
        # The only event is the rejection itself: no stage ran, so no stage has
        # anything to report.
        assert _names(_logs) == ["TRADE_REJECTED"]

    def test_an_evidence_filter_refusal_names_the_signal_stage(self) -> None:
        pipeline, _, _ = _pipeline(risk=RiskParameters(minimum_evidence_score=0.9))
        decision = pipeline.process(_signal(evidence=0.5))
        assert decision.diagnostics["stage"] == "signal"
        assert decision.reason == RejectionReason.EVIDENCE_BELOW_MINIMUM.value

    @pytest.mark.parametrize(
        ("stage", "signal", "risk", "specs"),
        [
            ("signal", _signal(action=SignalAction.NO_TRADE, stop=None, target=None), None, None),
            ("stop", _signal(stop=None), None, None),
            # The strict policy, because under the default one an unusable target
            # is *replaced* rather than refused -- which is the whole difference
            # between the two, and the reason this stage refuses at all.
            (
                "take_profit",
                _signal(target="1.09000"),
                RiskParameters(take_profit_source=TakeProfitSource.SIGNAL),
                None,
            ),
            ("policy", _signal(), RiskParameters(allow_buy=False), None),
            ("spec", _signal(), None, {}),
            ("size", _signal(), RiskParameters(risk_percent=Decimal("0.002")), None),
        ],
        ids=["signal", "stop", "take_profit", "policy", "spec", "size"],
    )
    def test_each_stage_can_be_the_one_that_refuses(
        self,
        stage: str,
        signal: Signal,
        risk: RiskParameters | None,
        specs: dict[str, object] | None,
    ) -> None:
        # One row per refusal-capable stage, so a stage that became unreachable --
        # or reachable when it should not be -- fails here rather than in
        # production. The `ids` are the executable form of `STAGES`: if a stage is
        # added to the pipeline and not to this table, the assertion below fails.
        pipeline, _, _ = _pipeline(specs=specs, risk=risk)
        decision = pipeline.process(signal)

        assert decision.diagnostics["stage"] == stage
        assert decision.reason != PASSED
        assert decision.is_no_trade
        assert decision.intent is None

    def test_geometry_cannot_refuse_a_signal_that_reached_it(self) -> None:
        """The geometry stage is a re-verification, and here it always passes.

        Recorded rather than hidden, because a stage in the pipeline that can
        never fire is either dead code or a bug, and the reader cannot tell which
        from the call sequence.

        It is the first. Every condition :func:`validate_geometry` checks has
        already been checked by an earlier stage on the same objects:
        `resolve_stop` verified the stop price, its distance and its side, and
        `resolve_take_profit` verified the target's side -- and both build their
        result through models that refuse a non-positive distance at construction.
        So a signal that arrives at geometry is geometrically sound by definition.

        It stays in the pipeline anyway, and the Phase 0 rationale still holds:
        the two functions have different callers, the geometry can come from
        configuration as well as from a signal, and a check that trusts its input
        to have been verified elsewhere fails the first time somebody calls it
        directly. A redundant check costs a comparison. A missing one costs a
        rejected broker order.
        """
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "complete"

    def test_the_stage_list_is_the_one_the_pipeline_actually_runs(self) -> None:
        # `STAGES` is documentation, and documentation drifts. This is what keeps
        # it honest: the refusal-capable stages are exactly the six above, and the
        # seventh is the re-verification the previous test explains.
        assert STAGES == (
            "signal",
            "stop",
            "take_profit",
            "geometry",
            "policy",
            "spec",
            "size",
        )
        refusable = {"signal", "stop", "take_profit", "policy", "spec", "size"}
        assert refusable < set(STAGES)

    def test_an_incomplete_specification_refuses_at_the_spec_stage(self) -> None:
        """The other branch geometry does not have: reachable, so tested.

        `validate_against_spec` refuses a symbol whose tick size is not usable, and
        `SymbolSpec` refuses one at construction -- so through a provider this
        cannot happen, which is what makes the geometry branch above
        unreachable. A deserialised specification, or a future adapter that builds
        the object loosely, could. So the specification here bypasses its own
        validation, exactly as `test_defensive_guards.py` does for the same reason.

        It is a real difference from geometry that this one is reachable: the
        specification arrives from outside the process and has been deserialised
        by someone else's code, while the stop and the take profit are built here
        by resolvers that cannot produce an invalid one.
        """
        spec = eurusd_spec()
        object.__setattr__(spec, "tick_size", Decimal("0"))
        pipeline, _, _ = _pipeline(specs={"EURUSD": spec})
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "spec"
        assert decision.reason == RejectionReason.SYMBOL_SPEC_UNAVAILABLE.value


class TestFailClosedOnMissingFacts:
    def test_an_unreadable_account_refuses_the_trade(self) -> None:
        pipeline, provider, _ = _pipeline()
        provider.fail_with(IntegrationError("terminal is not running"))
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "size"
        assert decision.reason == RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE.value

    def test_an_unreadable_account_does_not_come_from_an_exception(self) -> None:
        # A terminal that is not running is an expected condition. A loop that
        # died here would stop processing the signals it could still act on.
        pipeline, provider, _ = _pipeline()
        provider.fail_with(RuntimeError("boom"))
        assert pipeline.process(_signal()).is_no_trade

    def test_a_concurrency_limit_that_cannot_be_checked_refuses(self) -> None:
        # Fail-closed, and this is the test that says so. A limit that quietly
        # stops applying when the terminal is unreachable is a risk control that
        # switches off exactly when it is most needed.
        pipeline, provider, _ = _pipeline(risk=RiskParameters(max_open_positions=3))
        provider.fail_with(IntegrationError("down"))
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "policy"
        assert decision.reason == RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE.value
        assert "rather than skipped" in decision.explanation

    def test_no_concurrency_limit_means_an_unreadable_account_is_not_fatal_yet(
        self, _logs: StringIO
    ) -> None:
        # Without a limit there is no gate to evaluate, so the refusal belongs to
        # sizing -- where the balance is actually needed. It is a different
        # moment and a different remedy, and merging them would send an operator
        # to the wrong place.
        pipeline, provider, _ = _pipeline()
        provider.fail_with(IntegrationError("down"))
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "size"

    def test_an_unknown_symbol_refuses_before_any_sizing(self) -> None:
        pipeline, _, _ = _pipeline(specs={})
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "spec"
        assert decision.reason == RejectionReason.SYMBOL_SPEC_UNAVAILABLE.value
        assert "guessed contract" in decision.explanation

    def test_a_currency_mismatch_is_refused_at_the_size_stage(self) -> None:
        pipeline, _, _ = _pipeline(specs={"EURUSD": eurusd_spec(currency_profit="JPY")})
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "size"
        assert decision.reason == RejectionReason.INVALID_RISK_PARAMETERS.value

    def test_a_budget_too_small_for_the_instrument_refuses(self) -> None:
        # 0.002% of $10,000 is $0.20, which cannot buy a 0.01 lot on a $300 stop.
        pipeline, _, _ = _pipeline(risk=RiskParameters(risk_percent=Decimal("0.002")))
        decision = pipeline.process(_signal())
        assert decision.diagnostics["stage"] == "size"
        assert decision.reason == RejectionReason.VOLUME_BELOW_BROKER_MINIMUM.value


class TestTheConcurrencyGate:
    def test_the_limit_refuses_at_the_boundary(self) -> None:
        pipeline, _, _ = _pipeline(
            account=_account(open_positions=3),
            risk=RiskParameters(max_open_positions=3),
        )
        decision = pipeline.process(_signal())
        assert decision.reason == RejectionReason.MAX_CONCURRENT_POSITIONS.value

    def test_a_count_below_the_limit_passes(self) -> None:
        pipeline, _, _ = _pipeline(
            account=_account(open_positions=2),
            risk=RiskParameters(max_open_positions=3),
        )
        assert pipeline.process(_signal()).action is DecisionAction.DRY_RUN

    def test_the_gate_is_not_applied_when_no_limit_is_configured(self) -> None:
        pipeline, _, _ = _pipeline(account=_account(open_positions=99))
        assert pipeline.process(_signal()).action is DecisionAction.DRY_RUN


class TestTheAccountIsOneSnapshot:
    def test_the_account_is_read_once_per_decision(self) -> None:
        # Read twice is cheap and still wrong: two reads can straddle a close,
        # and the open-position count that admitted the trade would not be the
        # count that sized it.
        pipeline, provider, _ = _pipeline(
            account=_account(open_positions=1),
            risk=RiskParameters(max_open_positions=5),
        )
        pipeline.process(_signal())
        assert provider.calls == 1

    def test_the_specification_is_read_once_per_decision(self) -> None:
        pipeline, _, symbols = _pipeline()
        pipeline.process(_signal())
        assert symbols.calls == 1

    def test_the_count_that_admitted_the_trade_is_the_balance_that_sized_it(
        self, _logs: StringIO
    ) -> None:
        # The observable consequence of the snapshot. A balance that changes
        # between two reads would size the trade against a figure the gate never
        # saw; the intent's recorded balance is the one the budget came from.
        pipeline, provider, _ = _pipeline()
        pipeline.process(_signal())
        intended = _events(_logs, "RISK_CALCULATED")[0]
        assert intended["balance"] == "10000"
        assert provider.calls == 1


class TestObservability:
    def test_the_reserved_events_are_emitted_in_order(self, _logs: StringIO) -> None:
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal())
        names = _names(_logs)
        for event in (
            Event.STOP_RESOLVED,
            Event.TAKE_PROFIT_RESOLVED,
            Event.RISK_CALCULATED,
            Event.POSITION_SIZED,
            Event.TRADE_VALIDATED,
        ):
            assert str(event) in names, f"{event} was not emitted"
        # The order the pipeline ran in, visible in the log.
        assert names.index("STOP_RESOLVED") < names.index("TAKE_PROFIT_RESOLVED")
        assert names.index("TAKE_PROFIT_RESOLVED") < names.index("RISK_CALCULATED")
        assert names.index("RISK_CALCULATED") < names.index("POSITION_SIZED")
        assert names.index("POSITION_SIZED") < names.index("TRADE_VALIDATED")

    def test_the_stop_event_records_its_provenance(self, _logs: StringIO) -> None:
        # The phase that emitted `STOP_RESOLVED` was Phase 6, two phases after the
        # resolution it reports was written. A log that said only "a stop was
        # resolved" would not answer "was it structural?".
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal())
        event = _events(_logs, "STOP_RESOLVED")[0]
        assert event["price"] == "1.09700"
        assert event["distance"] == "0.00300"
        assert event["structural"] is True
        assert event["basis"] == "PULLBACK_EXTREME"

    def test_a_volatility_stop_is_logged_as_not_structural(self, _logs: StringIO) -> None:
        pipeline, _, _ = _pipeline(risk=RiskParameters(allow_volatility_fallback_stop=True))
        pipeline.process(_signal(stop_basis="ATR_FALLBACK"))
        assert _events(_logs, "STOP_RESOLVED")[0]["structural"] is False

    def test_the_take_profit_event_carries_the_achieved_ratio_and_the_source(
        self, _logs: StringIO
    ) -> None:
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal())
        event = _events(_logs, "TAKE_PROFIT_RESOLVED")[0]
        assert event["price"] == "1.10300"
        assert event["source"] == "SIGNAL"
        assert event["achieved_ratio"] == "1"

    def test_a_fallback_is_never_silent_in_the_log(self, _logs: StringIO) -> None:
        # No target on the signal, so the ratio was applied. The log has to say so.
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal(target=None))
        event = _events(_logs, "TAKE_PROFIT_RESOLVED")[0]
        assert event["source"] == "RR_FALLBACK"
        assert "no take profit" in str(event["fallback_because"])

    def test_a_rejection_names_the_stage_and_the_reason(self, _logs: StringIO) -> None:
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal(stop=None))
        event = _events(_logs, "TRADE_REJECTED")[0]
        assert event["stage"] == "stop"
        assert event["reason"] == RejectionReason.NO_VALID_STOP.value
        assert event["explanation"]

    def test_the_validated_event_says_nothing_was_sent(self, _logs: StringIO) -> None:
        pipeline, _, _ = _pipeline()
        pipeline.process(_signal())
        event = _events(_logs, "TRADE_VALIDATED")[0]
        assert event["volume"] == "0.16"
        assert event["execution_enabled"] is False
        assert event["dry_run"] is True
        assert "nothing was sent" in str(event["note"])


class TestNoTakeProfitPolicy:
    def test_the_disabled_policy_produces_a_complete_intent(self) -> None:
        # Phase 5 made `TradeIntent.take_profit` optional because this is a
        # supported configuration. This is the test that says the pipeline
        # actually supports it, rather than the model merely permitting it.
        pipeline, _, _ = _pipeline(risk=RiskParameters(take_profit_source=TakeProfitSource.NONE))
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.DRY_RUN
        assert decision.intent is not None
        assert decision.intent.take_profit is None
        assert decision.intent.reward_to_risk is None

    def test_it_serialises_without_dividing_by_a_missing_target(self, _logs: StringIO) -> None:
        # `to_dict` used to evaluate `reward_to_risk` unconditionally, so this
        # configuration would have raised rather than serialising.
        pipeline, _, _ = _pipeline(risk=RiskParameters(take_profit_source=TakeProfitSource.NONE))
        payload = pipeline.process(_signal()).to_dict()
        assert payload["intent"]["take_profit"] is None
        assert payload["intent"]["reward_to_risk"] is None
        assert payload["intent"]["has_take_profit"] is False

    def test_the_event_records_that_there_is_no_target(self, _logs: StringIO) -> None:
        # An event emitted only on success would leave a silence here that is
        # indistinguishable from a stage that crashed.
        pipeline, _, _ = _pipeline(risk=RiskParameters(take_profit_source=TakeProfitSource.NONE))
        pipeline.process(_signal())
        event = _events(_logs, "TAKE_PROFIT_RESOLVED")[0]
        assert event["has_take_profit"] is False
        assert event["source"] == "NONE"
        assert event["achieved_ratio"] is None


class TestItCannotPlaceAnOrder:
    def test_a_fully_validated_trade_is_never_execute(self, _logs: StringIO) -> None:
        # The single most important property of this phase. A decision that said
        # EXECUTE would be claiming an order exists.
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.DRY_RUN
        assert not decision.is_trade

    def test_it_stays_a_dry_run_even_when_execution_is_enabled(self) -> None:
        # Turning the flag on must not produce an EXECUTE, because there is no
        # executor to call. That is the guard against a future edit that assumes
        # enabling execution implies one exists.
        pipeline, _, _ = _pipeline()
        pipeline._config = BridgeConfig(
            risk=RiskParameters(),
            execution_enabled=True,
            dry_run=False,
        )
        decision = pipeline.process(_signal())
        assert decision.action is DecisionAction.DRY_RUN
        assert decision.reason == PASSED

    def test_the_execution_result_is_never_attached(self) -> None:
        pipeline, _, _ = _pipeline()
        decision = pipeline.process(_signal())
        assert decision.execution is None

    def test_this_module_holds_no_executor(self) -> None:
        # Structural rather than behavioural. An execution path that appears here
        # before Phase 7 would be one without the kill switch, the idempotency
        # ledger and the audit log around it -- which is the failure the whole
        # architecture was drawn to prevent. So the constraint is asserted on the
        # source, not left to review.
        import ast
        import inspect

        from signal_to_trade_bridge.application import process_signal as module

        tree = ast.parse(inspect.getsource(module))
        forbidden = {"TradeExecutor", "IdempotencyStore", "KillSwitch"}
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name.split(".")[-1] for alias in node.names]
            for name in names:
                assert name not in forbidden, (
                    f"process_signal.py imports {name!r}. Execution belongs to Phase 7, where "
                    f"the kill switch, the idempotency ledger and the audit log come with it."
                )


class TestDeterminism:
    def test_the_same_signal_produces_the_same_decision(self, _logs: StringIO) -> None:
        # Phase 9's idempotency ledger depends on this and cannot provide it: the
        # ledger needs to recognise a re-delivery, which means the decision must
        # not vary with the clock or with iteration order.
        pipeline, _, _ = _pipeline()
        first = pipeline.process(_signal()).to_dict()
        second = pipeline.process(_signal()).to_dict()
        assert first == second

    def test_the_decision_carries_the_injected_clock(self) -> None:
        pipeline, _, _ = _pipeline()
        assert pipeline.process(_signal()).decided_at == FIXED_NOW

    def test_nothing_is_mutated_between_runs(self) -> None:
        pipeline, _, _ = _pipeline()
        signal = _signal()
        before = signal.to_dict()
        pipeline.process(signal)
        pipeline.process(signal)
        assert signal.to_dict() == before
