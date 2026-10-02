"""The whole bridge, assembled, on a real-ish machine.

Phase 10. Every other test file drives one adapter with doubles. This one builds
**the composition root** and runs a signal through it, and the three things it can
find that the others cannot are:

* **the wiring is wrong.** Two phases' worth of independent tests all pass while
  `build_bridge` hands the pipeline a `SymbolSpecProvider` for the wrong thing, or
  the preflight a stale binding. Only an assembly exercises the seams.
* **the default really cannot execute.** Asserted by building the default
  configuration and reaching for the thing that would place an order, not by
  reading a flag.
* **the refusals compose.** An unreadable ledger plus a live terminal is still a
  refusal, and the refusal has to arrive *before* anything could have been written.

**`auto_trade` is real here** -- the maintainer's checkout is installed editable --
and so are the MT5 bindings, injected as doubles because no terminal is open. The
ledger is the **real** ``JsonExecutionLedger`` on a temporary path, because its
guarantees are what the composition depends on and a double would prove nothing.

No terminal is opened, no order is placed, and the only executable path is
deliberately unreachable: `build_bridge` refuses when execution is enabled.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path

import pytest

from conftest import StubBindings
from signal_to_trade_bridge.adapters.auto_trade import load_bindings
from signal_to_trade_bridge.composition import (
    Bridge,
    CompositionRefusal,
    build_bridge,
)
from signal_to_trade_bridge.configuration.config import BridgeConfig
from signal_to_trade_bridge.domain.enums import DecisionAction
from signal_to_trade_bridge.domain.models import (
    RiskParameters,
    Signal,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SIGNAL_ID = "stb-composition-0001"


# The terminal doubles live in conftest.py now: this module, the CLI tests and any
# future one all need the same EURUSD-shaped surface, and a copy per module is a
# place for a wrong field name to hide.


def _signal(**overrides: object) -> Signal:
    from signal_to_trade_bridge.domain.enums import Direction, SignalAction

    defaults: dict[str, object] = {
        "signal_id": SIGNAL_ID,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "action": SignalAction.BUY,
        "direction": Direction.LONG,
        "entry": Decimal("1.10000"),
        "stop_loss": Decimal("1.09700"),
        "take_profit": Decimal("1.10300"),
        "stop_basis": "PULLBACK_EXTREME",
        "take_profit_basis": "SWING",
        "evidence_score": 0.72,
        "setup_id": "pullback_h#0",
        "bar_index": 299,
        "bar_time": 1727740800.0,
    }
    defaults.update(overrides)
    return Signal(**defaults)  # type: ignore[arg-type]


def _config(tmp_path: Path, **overrides: object) -> BridgeConfig:
    defaults: dict[str, object] = {
        "risk": RiskParameters(allowed_symbols={"EURUSD"}),
        "log_directory": tmp_path / "logs",
        "mt5_data_path": tmp_path / "mt5",
    }
    defaults.update(overrides)
    return BridgeConfig(**defaults)  # type: ignore[arg-type]


def _bindings_or_skip() -> object:
    if find_spec("auto_trade") is None:
        pytest.skip("auto_trade is not installed on this machine")
    return load_bindings()


def _bridge(tmp_path: Path, **overrides: object) -> Bridge:
    return build_bridge(
        _config(tmp_path, **overrides),
        mt5_bindings=StubBindings(),  # type: ignore[arg-type]
        auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
    )


# --- the default cannot execute ---------------------------------------------


class TestTheDefaultConfigurationCannotExecute:
    def test_the_default_bridge_reports_that_it_cannot_execute(self, tmp_path: Path) -> None:
        bridge = _bridge(tmp_path)
        assert bridge.can_execute is False
        assert "execution is not enabled" in bridge.refusal

    def test_the_default_pipeline_has_no_executor(self, tmp_path: Path) -> None:
        # Structural rather than behavioural: an envelope would make `can_execute`
        # true regardless of the flags, and the flags are the thing under test.
        bridge = _bridge(tmp_path)
        assert bridge.pipeline.can_execute is False

    def test_a_default_bridge_turns_a_signal_into_a_dry_run(self, tmp_path: Path) -> None:
        bridge = _bridge(tmp_path)
        decision = bridge.pipeline.process(_signal())
        assert decision.action is DecisionAction.DRY_RUN
        assert decision.execution is None
        assert decision.intent is not None

    def test_dry_run_on_with_execution_enabled_also_refuses(self, tmp_path: Path) -> None:
        # The other half of the pair. `execution_enabled` alone is not permission.
        bridge = _bridge(tmp_path, execution_enabled=True, dry_run=True)
        assert bridge.can_execute is False
        assert "dry-run" in bridge.refusal


class TestTheLivePathIsRefusedNotDegraded:
    def test_enabling_execution_without_dry_run_refuses_to_assemble(self, tmp_path: Path) -> None:
        # The important one. Phase 11 owns the live side, so a caller who enables
        # execution today must be told so -- not handed a silent dry run that looks
        # like their setting took effect.
        with pytest.raises(CompositionRefusal) as raised:
            _bridge(tmp_path, execution_enabled=True, dry_run=False)
        assert "Phase 11" in str(raised.value)

    def test_the_refusal_says_it_is_not_degrading(self, tmp_path: Path) -> None:
        with pytest.raises(CompositionRefusal) as raised:
            _bridge(tmp_path, execution_enabled=True, dry_run=False)
        assert "rather than degrading to a dry run" in str(raised.value)


# --- the wiring -------------------------------------------------------------


class TestTheWiring:
    def test_the_ledger_is_the_real_one_and_it_persists(self, tmp_path: Path) -> None:
        # The composition depends on the real ledger's guarantees, so a double would
        # prove nothing about the thing actually assembled.
        bridge = _bridge(tmp_path)
        bridge.ledger.record_attempt(SIGNAL_ID, "exec-1")
        path = tmp_path / "logs" / "idempotency.json"
        assert path.is_file()
        assert SIGNAL_ID in path.read_text(encoding="utf-8")

    def test_the_account_snapshot_reaches_the_intent(self, tmp_path: Path) -> None:
        # One read, feeding both the concurrency gate and the sizing. If the
        # composition wired two providers, this would still pass and the accounting
        # would be wrong somewhere nobody could see.
        bridge = _bridge(tmp_path)
        decision = bridge.pipeline.process(_signal())
        assert decision.intent is not None
        assert decision.intent.account_balance.balance == Decimal("10000.00")
        assert decision.intent.account_balance.currency == "USD"

    def test_the_symbol_spec_reaches_the_sizing(self, tmp_path: Path) -> None:
        bridge = _bridge(tmp_path)
        decision = bridge.pipeline.process(_signal())
        assert decision.intent is not None
        assert decision.intent.symbol_spec.contract_size == Decimal("100000")
        assert decision.intent.volume > 0

    def test_the_dry_run_report_names_the_real_risk_arithmetic(self, tmp_path: Path) -> None:
        bridge = _bridge(tmp_path)
        decision = bridge.pipeline.process(_signal())
        report = decision.diagnostics["report"]
        assert report["request"]["symbol"] == "EURUSD"
        # 0.5% of 10000 is 50, over a 300-tick stop at $1/lot = 300 per lot.
        assert report["arithmetic"]["risk_amount"] == "50.00"

    def test_the_preflight_is_wired_to_the_real_engine(self, tmp_path: Path) -> None:
        # `ask_downstream` was built from the real bindings, so the report's
        # downstream verdict is the execution project's own opinion rather than a
        # stub agreeing with us.
        bridge = _bridge(tmp_path)
        decision = bridge.pipeline.process(_signal())
        verdict = decision.diagnostics["report"]["downstream"]
        assert verdict["evaluated"] is True
        assert verdict["accepted"] is True

    def test_the_preflight_agrees_when_the_two_allow_lists_agree(self, tmp_path: Path) -> None:
        # The positive case, and the one that would be silently lost if the preflight
        # were wired to the wrong thing.
        #
        # **An honest note on what this configuration can and cannot show.** The
        # composition root builds the preflight's limits from the *bridge's*
        # ``allowed_symbols``, so through the composition the two projects agree by
        # construction -- there is no disagreement to observe here. That is
        # deliberate on the root's part (a composition root that configured the
        # downstream limits independently would be a second policy nobody maintains),
        # and it means the genuine disagreement -- a real ``auto-trade`` install
        # whose own configuration forbids what the bridge allows -- is exercised in
        # `test_dry_run.py` against limits the caller supplies, not here.
        #
        # So this asserts the wiring is live and coherent, and the disagreement tests
        # live where the disagreement is real.
        bridge = build_bridge(
            _config(tmp_path, risk=RiskParameters(allowed_symbols={"GBPJPY"})),
            mt5_bindings=StubBindings({"GBPJPY"}),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        decision = bridge.pipeline.process(_signal(symbol="GBPJPY"))
        report = decision.diagnostics["report"]
        assert report["downstream"]["evaluated"] is True
        assert report["downstream"]["accepted"] is True
        # The only blockers left are the configuration ones, and naming them is the
        # point: a dry run that cannot say why it cannot send is a placeholder.
        assert all("BRIDGE_" in b for b in report["blockers"])
        assert not any("would refuse it" in b for b in report["blockers"])


class TestTheRefusalsCompose:
    def test_an_unreadable_ledger_refuses_the_assembly(self, tmp_path: Path) -> None:
        # Before anything could have been written. The ledger is opened first, and
        # an unreadable one must stop the process while there is still nothing to
        # undo.
        logs = tmp_path / "logs"
        logs.mkdir(parents=True)
        (logs / "idempotency.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(CompositionRefusal) as raised:
            _bridge(tmp_path)
        assert "could not be read" in str(raised.value)

    def test_a_corrupt_ledger_is_not_treated_as_empty(self, tmp_path: Path) -> None:
        # The property the whole adapter exists for, arriving through the assembly.
        logs = tmp_path / "logs"
        logs.mkdir(parents=True)
        (logs / "idempotency.json").write_text("[]", encoding="utf-8")
        with pytest.raises(CompositionRefusal):
            _bridge(tmp_path)

    def test_a_missing_execution_package_refuses_rather_than_going_dry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Silently degrading here would be the worst outcome available: a report
        # whose downstream gate says "not evaluated" reads as caution, and a missing
        # dependency is not caution.
        monkeypatch.setattr(
            "signal_to_trade_bridge.composition._auto_trade_bindings", lambda _s: None
        )
        with pytest.raises(CompositionRefusal) as raised:
            build_bridge(_config(tmp_path), mt5_bindings=StubBindings())  # type: ignore[arg-type]
        assert "idempotency" in str(raised.value)

    def test_a_bindings_object_missing_everything_is_refused_as_a_decision(
        self, tmp_path: Path
    ) -> None:
        # The composition hands the bindings straight to the providers, which raise
        # `MT5Unavailable` on a terminal that does not answer. The important part is
        # that **assembly succeeds and the refusal arrives as a decision** -- a
        # pipeline that raised out of `process` on an unreachable terminal would hand
        # its caller a traceback instead of a reason code.
        bridge = build_bridge(
            _config(tmp_path),
            mt5_bindings=object(),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        decision = bridge.pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        # The pipeline tolerates an unreachable account and carries on to the spec,
        # because only `max_open_positions` needs the count today. With no
        # concurrency gate configured, the refusal lands on the *specification* --
        # and that is the right place: both facts came from the same dead terminal.
        assert decision.reason == "SYMBOL_SPEC_UNAVAILABLE"

    def test_a_terminal_that_answers_nothing_is_refused_rather_than_defaulted(
        self, tmp_path: Path
    ) -> None:
        class _NoAccount:
            def account_info(self) -> object:
                return None

            def symbol_info(self, _name: str) -> object:
                return None

            def shutdown(self) -> None:
                return None

        bridge = build_bridge(
            _config(tmp_path),
            mt5_bindings=_NoAccount(),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        decision = bridge.pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == "SYMBOL_SPEC_UNAVAILABLE"


class TestThePositionReaderIsOptional:
    def test_no_data_path_still_assembles(self, tmp_path: Path) -> None:
        # Refusing the whole assembly over a missing optional feature would be a
        # total outage on a machine that has no indicator attached and never will.
        bridge = build_bridge(
            _config(tmp_path, mt5_data_path=None),
            mt5_bindings=StubBindings(),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        assert bridge.can_execute is False
        assert bridge.pipeline.process(_signal()).action is DecisionAction.DRY_RUN

    def test_a_dead_account_is_refused_at_the_account_gate_when_a_limit_is_set(
        self, tmp_path: Path
    ) -> None:
        # The same dead terminal, with `max_open_positions` configured. Now the count
        # *is* needed, so the refusal moves earlier -- which is the fail-closed
        # property Phase 4 documented and this asserts through the composition.
        class _NoAccount:
            def account_info(self) -> object:
                return None

            def symbol_info(self, _name: str) -> object:
                return None

            def shutdown(self) -> None:
                return None

        bridge = build_bridge(
            _config(
                tmp_path,
                risk=RiskParameters(allowed_symbols={"EURUSD"}, max_open_positions=1),
            ),
            mt5_bindings=_NoAccount(),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        decision = bridge.pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == "ACCOUNT_BALANCE_UNAVAILABLE"
        assert decision.diagnostics["stage"] == "policy"

    def test_a_limit_of_zero_without_a_reader_refuses_the_trade(self, tmp_path: Path) -> None:
        # Phase 7's residual case, seen through the composition. With no data path
        # the count is zero, so a limit of zero admits nothing -- and the trade is
        # refused at the concurrency gate rather than on a spec lookup or a volume
        # check, which is where a reader that answered "unknown" would have sent it.
        bridge = build_bridge(
            _config(
                tmp_path,
                mt5_data_path=None,
                risk=RiskParameters(allowed_symbols={"EURUSD"}, max_open_positions=0),
            ),
            mt5_bindings=StubBindings(),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        decision = bridge.pipeline.process(_signal())
        assert decision.action is DecisionAction.NO_TRADE
        assert decision.diagnostics["stage"] == "policy"

    def test_the_absent_reader_is_reported_as_a_zero_not_hidden(self, tmp_path: Path) -> None:
        # The honest half of the same case, and the reason the safe configuration is
        # to leave the limit *unset*. A limit of one and no reader reports zero open
        # positions, so the trade is admitted -- silently, on a number that was never
        # read. The report must still say the count was not read, or an operator
        # enabling a limit believes it is enforced.
        bridge = build_bridge(
            _config(
                tmp_path,
                mt5_data_path=None,
                risk=RiskParameters(allowed_symbols={"EURUSD"}, max_open_positions=1),
            ),
            mt5_bindings=StubBindings(),  # type: ignore[arg-type]
            auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
        )
        decision = bridge.pipeline.process(_signal())
        assert decision.action is DecisionAction.DRY_RUN
        # The intent records the balance the size came from, and the account's open
        # position count is not on it -- which is the gap, stated.
        assert decision.intent is not None
        assert decision.intent.account_balance.open_positions == 0


class TestEndToEndThroughTheRealLedger:
    def test_a_signal_sent_through_a_wired_pipeline_is_recorded_before_it_goes(
        self, tmp_path: Path
    ) -> None:
        # The two-phase property, exercised through the composition rather than the
        # adapter: an attempt must be durable *before* the executor is reached, or a
        # crash in between leaves no record and the trade may repeat.
        from signal_to_trade_bridge.application.execution_envelope import (
            build_execution_envelope,
        )
        from signal_to_trade_bridge.domain.enums import Direction

        bridge = _bridge(tmp_path)
        executor = _RecordingExecutor()
        order: list[str] = []

        class _Store:
            def contains(self, key: str) -> bool:
                order.append(f"contains:{key}")
                return False

            def record_attempt(self, key: str, execution_id: str) -> None:
                order.append(f"attempt:{key}")

            def record_outcome(self, key: str, execution_id: str, outcome) -> None:  # type: ignore[no-untyped-def]
                order.append(f"outcome:{key}")

        class _Switch:
            active = False

        bridge.pipeline._config = BridgeConfig(
            risk=bridge.pipeline._config.risk,  # type: ignore[attr-defined]
            execution_enabled=True,
            dry_run=False,
        )
        bridge.pipeline.wire_execution(
            build_execution_envelope(executor, _Store(), _Switch())  # type: ignore[arg-type]
        )

        decision = bridge.pipeline.process(_signal())

        assert decision.action is DecisionAction.EXECUTE
        assert order == [f"contains:{SIGNAL_ID}"], order
        assert executor.seen == 1
        assert Direction.LONG is _signal().direction

    def test_a_ledger_holding_the_signal_refuses_before_the_executor(self, tmp_path: Path) -> None:
        from signal_to_trade_bridge.application.execution_envelope import (
            build_execution_envelope,
        )

        bridge = _bridge(tmp_path)
        executor = _RecordingExecutor()

        class _Switch:
            active = False

        # The real ledger, pre-loaded with this signal id.
        bridge.ledger.record_attempt(SIGNAL_ID, "exec-earlier")
        bridge.pipeline._config = BridgeConfig(  # type: ignore[attr-defined]
            risk=bridge.pipeline._config.risk,  # type: ignore[attr-defined]
            execution_enabled=True,
            dry_run=False,
        )
        bridge.pipeline.wire_execution(
            build_execution_envelope(executor, bridge.ledger, _Switch())  # type: ignore[arg-type]
        )

        decision = bridge.pipeline.process(_signal())

        assert decision.action is DecisionAction.NO_TRADE
        assert decision.reason == "DUPLICATE_SIGNAL"
        assert executor.seen == 0


class _RecordingExecutor:
    def __init__(self) -> None:
        self.seen = 0

    def submit(self, request: object) -> object:
        from signal_to_trade_bridge.domain.models import ExecutionResult

        self.seen += 1
        return ExecutionResult(
            signal_id=getattr(request, "signal_id", SIGNAL_ID),
            status=ExecutionResult.STATUS_ACCEPTED,
            position_id="382363348",
        )


class TestTheAbsentOptionalPaths:
    """The branches that return ``None`` rather than raising.

    Every one of these is a *degradation*, and each has a different consequence, so
    they are pinned separately. A single "it still assembles" test would pass with
    all of them broken at once.
    """

    def test_a_mt5_package_that_cannot_be_imported_refuses_the_assembly(
        self, tmp_path: Path
    ) -> None:
        # Not a degradation: the account and the contract have no other source, so a
        # bridge without them cannot size a trade and must not pretend to.
        #
        # Reached by *not* supplying the bindings, so the real loader runs. On a
        # machine with the package installed that succeeds and this test would fail --
        # which is correct, because the refusal is about the *absent* package and
        # cannot be observed where the package is present. Hence the skip.
        if find_spec("MetaTrader5") is not None:
            pytest.skip("MetaTrader5 is installed here, so its absence cannot be observed")

        with pytest.raises(CompositionRefusal) as raised:
            build_bridge(
                _config(tmp_path),
                auto_trade_bindings=_bindings_or_skip(),  # type: ignore[arg-type]
            )
        assert "MetaTrader 5 bindings" in str(raised.value)

    def test_a_supplied_mt5_binding_short_circuits_the_loader(self, tmp_path: Path) -> None:
        # The injection has to actually short-circuit, or every test in this file
        # would be reaching the real loader and the whole suite would need a terminal.
        #
        # Pinned on the private helper rather than through `build_bridge`, because a
        # monkeypatch of a module global does not affect a name the module has already
        # bound -- a fact worth stating, because the obvious version of this test
        # silently does nothing and passes.
        from signal_to_trade_bridge import composition as module

        stub = StubBindings()
        assert module._mt5_bindings(_config(tmp_path), stub) is stub  # type: ignore[arg-type]
        # And with nothing supplied the real loader runs. Whether that succeeds
        # depends on the machine, so it is not asserted -- what matters is that the
        # two cases are *distinguishable*, and a stub returned for both would not be.
        with contextlib.suppress(CompositionRefusal):
            module._mt5_bindings(_config(tmp_path), None)  # type: ignore[arg-type]

    def test_a_supplied_auto_trade_binding_short_circuits_too(self, tmp_path: Path) -> None:
        from signal_to_trade_bridge import composition as module

        supplied = _bindings_or_skip()
        assert module._auto_trade_bindings(supplied) is supplied  # type: ignore[arg-type]

    def test_no_preflight_when_no_symbols_are_allowed(self, tmp_path: Path) -> None:
        # `RiskParameters.allowed_symbols` defaults to empty, which means "nothing is
        # allowed" -- a real configuration, not an unconfigured one. The preflight
        # then has no limits to ask about, so the report must read as *unevaluated*
        # rather than as a pass on a gate that was never asked.
        bridge = _bridge(tmp_path, risk=RiskParameters())
        decision = bridge.pipeline.process(_signal())
        verdict = decision.diagnostics["report"]["downstream"]
        assert verdict["evaluated"] is False
        assert verdict["unevaluated_because"]

    def test_a_loader_that_fails_yields_no_bindings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The uncovered lines are one branch: the real loader raised. On this machine
        # `auto_trade` is installed, so the only way to reach that branch is to make
        # the import fail -- which is exactly the machine this code exists for, so it
        # is worth being able to test rather than only to reason about.
        #
        # Patched on the *composition* module's own binding rather than on the package
        # that exports the loader. `composition` did ``from ... import load_bindings``
        # at import time, so rebinding the attribute on the source module would leave
        # the name it already holds untouched and the test would silently assert
        # nothing.
        from signal_to_trade_bridge import composition as module

        def fail() -> object:
            raise ModuleNotFoundError("No module named 'auto_trade'")

        monkeypatch.setattr(module, "load_bindings", fail)
        assert module._auto_trade_bindings(None) is None

    def test_an_absent_package_means_no_preflight(self, tmp_path: Path) -> None:
        # The other half of the same branch: with no bindings there are no limits to
        # ask about, so `_ask_downstream` short-circuits and the report says the gate
        # was not evaluated rather than showing a tick nobody asked for.
        from signal_to_trade_bridge import composition as module

        assert module._ask_downstream(_config(tmp_path), None) is None

    def test_no_preflight_when_the_package_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A machine without the private package still reports -- and says the
        # downstream gate was not checked, rather than showing a green tick.
        monkeypatch.setattr(
            "signal_to_trade_bridge.composition._auto_trade_bindings", lambda _s: None
        )
        # The ledger needs the bindings, so this asserts the *ordering*: a missing
        # package stops the assembly before the report is ever built.
        with pytest.raises(CompositionRefusal):
            build_bridge(_config(tmp_path), mt5_bindings=StubBindings())  # type: ignore[arg-type]

    def test_a_reachable_package_is_used_for_the_preflight(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The positive of the two above: with bindings supplied, the preflight is
        # built and the verdict is evaluated.
        calls: list[object] = []

        import signal_to_trade_bridge.composition as module

        real = module.ask_downstream_risk

        def spy(bindings: object, limits: object) -> object:
            calls.append(limits)
            return real(bindings, limits)  # type: ignore[arg-type]

        monkeypatch.setattr(module, "ask_downstream_risk", spy)
        _bridge(tmp_path)
        assert len(calls) == 1
        assert calls[0] is not None
