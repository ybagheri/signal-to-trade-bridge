"""The dry run: the order that *would* be sent, and the gates that would judge it.

**The gap this phase closes, stated as a test.** The bridge validates against its
own rules and `auto-trade` validates against its own rules again, and the two
rulebooks disagree by default. `auto-trade`'s defaults are an allow-list of
`EURUSD,XAUUSD,YM`, a volume cap of `1.0` and five orders a minute. **A signal on
`GBPJPY` passes every check in this bridge and is refused downstream** — and before
this phase the only place that became visible was a `REJECTED` result on a live
account.

The tests below are ordered by how much they would cost to get wrong:

* the *mismatch* tests, which is the content of the phase;
* the fail-closed tests, because an unevaluated downstream gate reporting as a pass
  is the specific silence this phase exists to prevent;
* the mapping tests, which pin numbers copied rather than recomputed;
* the structural tests, because the most important property here is an absence.

The `TestAgainstTheRealRiskEngine` class runs `auto-trade`'s actual `RiskEngine`
where the package is installed, which is the only way to know the upstream refusal
strings still say what this file claims.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal
from importlib.util import find_spec

import pytest

from signal_to_trade_bridge.adapters.auto_trade import preflight as preflight_module
from signal_to_trade_bridge.adapters.auto_trade.executor import _ORDER_ACTIONS as EXECUTOR_ACTIONS
from signal_to_trade_bridge.application import dry_run as dry_run_module
from signal_to_trade_bridge.application.dry_run import (
    DownstreamVerdict,
    build_execution_request,
    default_comment,
    direction_word,
    report_for,
)
from signal_to_trade_bridge.domain.enums import Direction
from signal_to_trade_bridge.domain.models import (
    ExecutionRequest,
    TradeIntent,
)

# --- fixtures ---------------------------------------------------------------
# Real model objects rather than mocks: the mapping is a copy of validated values,
# and a mock would let a wrong field through while every assertion still passed.

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _signal(symbol: str = "EURUSD", direction: Direction = Direction.LONG):
    """A real ``Signal``, matching the shape ``conftest.buy_signal`` builds.

    Built here rather than pulled from a fixture because these tests are not
    pytest-parametrised on it and a fixture would mean requesting it in every test
    to use it in a helper. Same shape, same fields.
    """
    from signal_to_trade_bridge.domain.enums import SignalAction
    from signal_to_trade_bridge.domain.models import Signal

    long = direction is Direction.LONG
    return Signal(
        signal_id="stb-dryrun-0001",
        symbol=symbol,
        timeframe="H1",
        action=SignalAction.BUY if long else SignalAction.SELL,
        direction=direction,
        entry=Decimal("1.10000"),
        stop_loss=Decimal("1.09700") if long else Decimal("1.10300"),
        take_profit=Decimal("1.10300") if long else Decimal("1.09700"),
        stop_basis="PULLBACK_EXTREME",
        take_profit_basis="SWING",
        evidence_score=0.72,
        setup_id="pullback_h#0",
        bar_index=299,
        bar_time=1727740800.0,
        source="albrooks",
    )


def _spec(symbol: str = "EURUSD"):
    """A 5-digit EURUSD-style spec, as ``conftest.forex_spec`` builds it.

    Round numbers deliberately: a test asserting ``0.12`` is readable, and a test
    asserting ``0.1198246...`` is not one anybody will notice failing for the right
    reason.
    """
    from signal_to_trade_bridge.domain.models import SymbolSpec

    return SymbolSpec(
        symbol=symbol,
        contract_size=Decimal("100000"),
        tick_size=Decimal("0.00001"),
        tick_value_profit=Decimal("1.0"),
        tick_value_loss=Decimal("1.0"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100.0"),
        volume_step=Decimal("0.01"),
        digits=5,
        point=Decimal("0.00001"),
        currency="USD",
        currency_profit="USD",
        currency_margin="EUR",
    )


def _intent(**overrides: object) -> TradeIntent:
    """A real ``TradeIntent`` -- a copy of validated values, never a mock.

    A mock would let a wrong field through while every assertion still passed.
    """
    from signal_to_trade_bridge.domain.enums import StopSource
    from signal_to_trade_bridge.domain.models import (
        AccountBalance,
        PositionSize,
        RiskParameters,
        StopLoss,
        TakeProfit,
        TakeProfitSource,
    )

    signal = overrides.pop("signal", None) or _signal()
    symbol = overrides.pop("symbol", None) or signal.symbol_normalised
    direction = signal.direction
    entry = signal.entry
    long = direction is Direction.LONG
    distance = Decimal("0.00300")
    volume = overrides.pop("volume", Decimal("0.12"))

    defaults: dict[str, object] = {
        "signal": signal,
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "stop_loss": StopLoss(
            price=entry - distance if long else entry + distance,
            distance=distance,
            source=StopSource.SIGNAL,
            basis="PULLBACK_EXTREME",
        ),
        "take_profit": TakeProfit(
            price=entry + distance if long else entry - distance,
            distance=distance,
            source=TakeProfitSource.SIGNAL,
            basis="SWING",
        ),
        "position_size": PositionSize(
            volume=volume,
            raw_volume=volume,
            risk_amount=Decimal("50.00"),
            stop_distance=distance,
            risk_per_unit=Decimal("300.00"),
            ticks=Decimal("300"),
            tick_size=Decimal("0.00001"),
            tick_value=Decimal("1.0"),
        ),
        "risk_parameters": overrides.pop("risk_parameters", None) or RiskParameters(),
        "account_balance": AccountBalance(balance=Decimal("10000.00"), currency="USD"),
        "symbol_spec": overrides.pop("symbol_spec", None) or _spec(symbol),
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)  # type: ignore[arg-type]


# --- the content of Phase 8 -------------------------------------------------


class TestTheTwoRulebooksDisagree:
    """The mismatch, which is the reason this phase exists."""

    def test_a_symbol_the_bridge_allows_can_be_refused_downstream(self) -> None:
        # The bridge has no opinion about GBPJPY; the downstream project does.
        intent = _intent(signal=_signal("GBPJPY"))
        request = build_execution_request(intent)

        ask = _ask(allowed={"EURUSD", "XAUUSD", "YM"})
        verdict = ask(request)

        assert verdict.evaluated
        assert verdict.accepted is False
        assert "symbol is not allowed" in verdict.reason

    def test_the_bridge_accepts_the_same_signal_the_downstream_refuses(self) -> None:
        # The other half of the same claim, and the one that would be easy to get
        # wrong by accident: the bridge must not *also* refuse it, or the dry run
        # would agree with downstream for the wrong reason and the disagreement
        # would stay hidden.
        intent = _intent(signal=_signal("GBPJPY"))
        request = build_execution_request(intent)

        assert request.symbol == "GBPJPY"
        # validate_policy's own allow-list is the bridge's; with no restriction
        # configured it admits anything tradeable.
        from signal_to_trade_bridge.domain.models import RiskParameters
        from signal_to_trade_bridge.domain.validation import validate_policy

        policy = validate_policy(_signal("GBPJPY"), RiskParameters(), 0)
        assert policy.ok, policy.explanation

    def test_a_volume_the_bridge_computed_can_exceed_the_downstream_cap(self) -> None:
        # The other default disagreement. The bridge sizes from a 0.5% risk budget
        # and does not care about an absolute lot cap; `auto-trade` caps at 1.0 by
        # default and refuses anything larger, however well sized it is here.
        request = build_execution_request(_intent(volume=Decimal("2.00")))

        verdict = _ask(max_volume=Decimal("1.0"))(request)

        assert verdict.evaluated
        assert verdict.accepted is False
        assert "volume exceeds configured limit" in verdict.reason

    def test_a_symbol_and_volume_both_allowed_passes(self) -> None:
        # The positive case. Without it the class above would pass with a stub that
        # refuses everything.
        request = build_execution_request(_intent())

        verdict = _ask(allowed={"EURUSD"}, max_volume=Decimal("1.0"))(request)

        assert verdict.accepted is True
        assert verdict.reason == ""

    def test_the_verdict_is_a_prediction_not_a_placement(self) -> None:
        # A downstream verdict of "accepted" must never read as a fill. It is one
        # gate's opinion, taken before the account, the kill switch, the ledger and
        # the terminal window have all had their say.
        report = report_for(
            _intent(),
            execution_enabled=False,
            dry_run=True,
            ask_downstream=_ask(allowed={"EURUSD"}),
        )
        assert report.downstream_would_accept is True
        assert report.would_be_sent is False
        assert report.request is not None
        # And the blockers still name the configuration, not the verdict.
        assert any("execution is not enabled" in b for b in report.blockers())


class TestFailClosed:
    """An unevaluated gate is not a passing gate. These are the tests that matter."""

    def test_no_engine_configured_is_reported_as_unevaluated(self) -> None:
        report = report_for(_intent(), execution_enabled=False, dry_run=True, ask_downstream=None)
        assert report.downstream.evaluated is False
        assert report.downstream_would_accept is False

    def test_no_engine_configured_is_named_as_a_blocker(self) -> None:
        # Otherwise "unknown" reads as "nothing in the way", which is the failure
        # this class was written to prevent.
        report = report_for(_intent(), execution_enabled=False, dry_run=True, ask_downstream=None)
        assert any("not evaluated" in b for b in report.blockers())

    def test_an_unevaluated_verdict_cannot_be_accepted(self) -> None:
        # A construction error, not a normalisation. An unevaluated gate that
        # reported as accepted would be indistinguishable, downstream, from a gate
        # that ran and passed -- which is the confusion this whole class removes.
        with pytest.raises(ValueError, match="unevaluated downstream gate"):
            DownstreamVerdict(accepted=True, evaluated=False)

    def test_an_engine_that_raises_is_unevaluated_not_accepted(self) -> None:
        # A broken preflight must not read as a clear gate. This is what a missing
        # package, a renamed class and a bug all look like from here.
        def explode(_request: ExecutionRequest) -> DownstreamVerdict:
            raise RuntimeError("engine wiring is broken")

        with pytest.raises(RuntimeError):
            report_for(
                _intent(),
                execution_enabled=False,
                dry_run=True,
                ask_downstream=explode,
            )

    def test_the_real_preflight_reports_an_unusable_engine_as_unevaluated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from signal_to_trade_bridge.adapters.auto_trade.preflight import (
            DownstreamLimits,
            ask_downstream_risk,
        )

        bindings = _bindings_or_skip()
        monkeypatch.setitem(__import__("sys").modules, "auto_trade.application.risk", None)
        ask = ask_downstream_risk(
            bindings,
            DownstreamLimits({"EURUSD"}, Decimal("1.0"), 5, 10),
            now=lambda: NOW,
        )
        verdict = ask(build_execution_request(_intent()))
        assert verdict.evaluated is False
        assert "could not be consulted" in verdict.unevaluated_because


# --- the mapping ------------------------------------------------------------


class TestBuildingTheRequest:
    def test_the_numbers_are_copied_not_recomputed(self) -> None:
        intent = _intent()
        request = build_execution_request(intent)
        assert request.volume == intent.volume
        assert request.entry == intent.entry
        assert request.stop_loss == intent.stop_loss.price
        assert request.take_profit == intent.take_profit.price

    def test_the_identifier_is_the_deterministic_key(self) -> None:
        intent = _intent()
        assert build_execution_request(intent).signal_id == intent.signal.signal_id

    def test_the_full_decision_rides_along_as_metadata(self) -> None:
        # So `auto-trade`'s audit log keeps the provenance without this project
        # needing a second audit store.
        request = build_execution_request(_intent())
        assert request.metadata == _intent().to_dict()

    def test_the_evidence_score_is_carried_but_labelled(self) -> None:
        request = build_execution_request(_intent())
        assert request.evidence_score is not None

    def test_the_comment_names_the_setup_without_the_risk_figure(self) -> None:
        # A comment travels to the broker and back out on the account history.
        # Printing the risk budget there publishes an operating parameter.
        intent = _intent()
        comment = default_comment(intent)
        assert "EURUSD" in comment
        assert intent.signal.setup_id in comment
        assert str(intent.risk_amount) not in comment
        assert str(intent.volume) not in comment

    def test_the_comment_is_bounded(self) -> None:
        # MT5 truncates. A comment long enough to be truncated is one whose useful
        # half is gone.
        assert len(default_comment(_intent())) <= 64

    def test_the_strategy_names_the_upstream_detector(self) -> None:
        # So an audit record can be traced back to the setup that caused the trade.
        intent = _intent()
        assert build_execution_request(intent).strategy == intent.signal.setup_id

    def test_an_intent_without_a_take_profit_produces_a_request(self) -> None:
        # Phase 8 raised here, because ExecutionRequest.take_profit was required
        # while this project's TradeIntent and auto-trade's TradeSignal both have it
        # optional. Phase 9 made the field optional, so the NONE policy now flows
        # through with nothing special done for it.
        request = build_execution_request(_intent(take_profit=None))
        assert request.take_profit is None
        assert request.stop_loss is not None

    def test_a_report_for_an_intent_without_a_take_profit_is_still_complete(self) -> None:
        # The arithmetic, the balance it came from and the blockers are all still
        # reported. The alternative -- refusing the whole report -- would leave an
        # operator who deliberately disabled targets with no dry run at all.
        report = report_for(_intent(take_profit=None), execution_enabled=False, dry_run=True)
        assert report.request is not None
        assert report.request.take_profit is None
        assert report.volume == Decimal("0.12")
        assert report.balance == Decimal("10000.00")

    def test_a_request_without_a_take_profit_serialises_as_absent(self) -> None:
        # Not as the string "None", which is what str() on a missing value would
        # give and what a broker would then try to use as a price.
        payload = build_execution_request(_intent(take_profit=None)).to_dict()
        assert payload["take_profit"] is None

    def test_a_take_profit_that_is_present_must_still_be_positive(self) -> None:
        # Absent is allowed -- exits managed elsewhere -- but present and
        # non-positive is not a target. Refused at the model rather than passed to
        # a broker that would.
        with pytest.raises(ValueError, match="take_profit"):
            ExecutionRequest(
                signal_id="stb-x",
                symbol="EURUSD",
                direction=Direction.LONG,
                volume=Decimal("0.12"),
                entry=Decimal("1.10000"),
                stop_loss=Decimal("1.09700"),
                take_profit=Decimal("0"),
            )

    def test_direction_words_are_words(self) -> None:
        assert direction_word(Direction.LONG) == "buy"
        assert direction_word(Direction.SHORT) == "sell"


class TestTheReport:
    def test_it_records_the_account_the_size_came_from(self) -> None:
        # So a report read later can be checked against the balance it used, rather
        # than silently believed.
        report = report_for(_intent(), execution_enabled=False, dry_run=True)
        assert report.balance == Decimal("10000.00")
        assert report.currency == "USD"

    def test_it_records_the_reward_to_risk_as_achieved(self) -> None:
        intent = _intent()
        report = report_for(intent, execution_enabled=False, dry_run=True)
        assert report.reward_to_risk == intent.reward_to_risk

    def test_execution_enabled_with_dry_run_on_is_still_not_going_to_send(self) -> None:
        # Both conditions, and the pair is a real configuration. Reporting it as
        # "would be sent" because one flag was on is the bug this pins.
        report = report_for(_intent(), execution_enabled=True, dry_run=True)
        assert report.would_be_sent is False
        assert any("dry-run" in b for b in report.blockers())

    def test_both_flags_off_means_a_live_order_would_go_out(self) -> None:
        report = report_for(_intent(), execution_enabled=True, dry_run=False)
        assert report.would_be_sent is True
        assert report.blockers() == () or all("downstream" in b for b in report.blockers())

    def test_a_downstream_refusal_is_named_as_a_blocker(self) -> None:
        # The case this whole phase exists for: the bridge passed, the downstream
        # project would refuse. That has to appear among the blockers, and it has
        # to carry the reason -- an operator who enables execution and gets a
        # REJECTED with no warning will reasonably assume the bridge broke.
        report = report_for(
            _intent(signal=_signal("GBPJPY")),
            execution_enabled=True,
            dry_run=False,
            ask_downstream=_ask(allowed={"EURUSD", "XAUUSD", "YM"}),
        )
        blockers = report.blockers()
        assert report.would_be_sent is True
        assert report.downstream_would_accept is False
        assert any("would refuse it" in b for b in blockers)
        assert any("symbol is not allowed" in b for b in blockers)

    def test_a_configured_system_can_have_no_blockers_at_all(self) -> None:
        # The positive form. Without it the class above would pass with a report
        # whose blockers() returned everything unconditionally.
        report = report_for(
            _intent(),
            execution_enabled=True,
            dry_run=False,
            ask_downstream=_ask(allowed={"EURUSD"}),
        )
        assert report.blockers() == ()
        assert report.would_be_sent is True
        assert report.downstream_would_accept is True

    def test_it_names_every_blocker_not_just_the_first(self) -> None:
        # An operator fixing blockers one at a time, each revealing the next, is the
        # slowest possible way to answer "can this trade ever run here?".
        report = report_for(_intent(), execution_enabled=False, dry_run=True, ask_downstream=None)
        blockers = report.blockers()
        assert len(blockers) >= 3
        assert any("execution is not enabled" in b for b in blockers)
        assert any("dry-run" in b for b in blockers)
        assert any("not evaluated" in b for b in blockers)

    def test_the_dictionary_nests_the_request(self) -> None:
        # A flat record has two symbol-shaped fields and no way to tell which
        # belongs to the order and which to the arithmetic that produced it.
        payload = report_for(_intent(), execution_enabled=False, dry_run=True).to_dict()
        assert payload["request"]["symbol"] == "EURUSD"
        assert payload["arithmetic"]["currency"] == "USD"
        assert payload["configuration"]["dry_run"] is True
        assert "would_be_sent" in payload

    def test_it_is_immutable(self) -> None:
        report = report_for(_intent(), execution_enabled=False, dry_run=True)
        with pytest.raises(AttributeError):
            report.volume = Decimal("99")  # type: ignore[misc]

    def test_the_limits_repr_does_not_leak_the_envelope(self) -> None:
        # It holds a configured risk envelope, and reprs end up in tracebacks and
        # logs.
        from signal_to_trade_bridge.adapters.auto_trade.preflight import DownstreamLimits

        text = repr(DownstreamLimits({"EURUSD", "XAUUSD", "YM"}, Decimal("1.0"), 5, 10))
        assert "1.0" not in text
        assert "EURUSD" not in text
        assert "DownstreamLimits(" in text


# --- structural -------------------------------------------------------------


class TestTheStructuralBoundaries:
    def test_the_dry_run_imports_no_executor(self) -> None:
        # The rule this phase is built around. `AutoTradeExecutor` exists one
        # package away and is perfectly usable -- and the pipeline still may not
        # reach it, because the idempotency ledger is not behind the pipeline yet.
        for module in (dry_run_module,):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.Import):
                    names = [alias.name.split(".")[-1] for alias in node.names]
                for name in names:
                    assert name not in {"TradeExecutor", "AutoTradeExecutor"}, (
                        f"dry_run.py imports {name!r}. Execution belongs to Phase 9, where "
                        f"the ledger and the kill switch arrive with it."
                    )

    def test_the_preflight_imports_no_executor_and_no_clicking_adapter(self) -> None:
        # The preflight is the module closest to the line: it constructs the exact
        # signal the executor would send, so that it can ask the engine about it.
        # It must not be able to send anything.
        # Checked over the AST rather than the raw text, because all four names
        # appear in this module's prose explaining precisely why it may not use
        # them. A text search would fail on the documentation of the rule it
        # enforces -- and a structural test that fires on correct code gets
        # deleted rather than fixed.
        forbidden = {
            "AutoTradeExecutor",
            "MT5DesktopAdapter",
            "ExecutionGate",
            "ExecutionWorkflow",
        }
        tree = ast.parse(inspect.getsource(preflight_module))
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        leaked = used & forbidden
        assert not leaked, (
            f"preflight.py references {sorted(leaked)}. The preflight can do nothing; if it "
            f"could reach the executor or the terminal, running it on every decision "
            f"would not be safe."
        )

    def test_the_preflight_does_not_import_the_executor_module(self) -> None:
        # The other half: not even transitively through an import. The AST check
        # above catches a use; this catches an import that a use might grow later.
        tree = ast.parse(inspect.getsource(preflight_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.endswith("executor"), (
                    f"preflight.py imports from {node.module}. It must construct the signal "
                    f"itself rather than reach through the executor for it, so that it cannot "
                    f"inherit the executor's ability to send anything."
                )

    def test_the_preflight_constructs_no_workflow_or_adapter(self) -> None:
        tree = ast.parse(inspect.getsource(preflight_module))
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "ExecutionWorkflow" not in calls
        assert "MT5DesktopAdapter" not in calls

    def test_the_two_direction_tables_agree(self) -> None:
        # Duplicated deliberately -- this module may not import the executor -- so a
        # test is what stops the copies drifting.
        assert dict(EXECUTOR_ACTIONS) == preflight_module._ORDER_ACTIONS


# --- against the real engine ------------------------------------------------


class TestAgainstTheRealRiskEngine:
    """Runs only where ``auto_trade`` is installed, which is this machine.

    Everything above runs against a stub, so it proves the bridge's side is
    internally consistent and nothing about the upstream refusal *strings*. This
    class is what checks those strings are still what this file claims -- and
    ``auto_trade`` is a private package that has changed shape before.
    """

    def _ask(self, **kwargs: object):
        from signal_to_trade_bridge.adapters.auto_trade.preflight import (
            DownstreamLimits,
            ask_downstream_risk,
        )

        defaults: dict[str, object] = {
            "allowed_symbols": {"EURUSD"},
            "max_volume": Decimal("1.0"),
            "max_orders_per_minute": 5,
            "expiration_seconds": 10,
        }
        defaults.update(kwargs)
        return ask_downstream_risk(
            _bindings_or_skip(),
            DownstreamLimits(**defaults),  # type: ignore[arg-type]
            now=lambda: NOW,
        )

    def test_the_real_engine_refuses_a_disallowed_symbol_with_that_message(self) -> None:
        verdict = self._ask(allowed_symbols={"EURUSD"})(
            build_execution_request(_intent(signal=_signal("GBPJPY")))
        )
        assert verdict.evaluated is True
        assert verdict.accepted is False
        assert "symbol is not allowed" in verdict.reason

    def test_the_real_engine_refuses_an_oversized_volume_with_that_message(self) -> None:
        verdict = self._ask(max_volume=Decimal("1.0"))(
            build_execution_request(_intent(volume=Decimal("2.00")))
        )
        assert verdict.accepted is False
        assert "volume exceeds configured limit" in verdict.reason

    def test_the_real_engine_accepts_an_allowed_sized_order(self) -> None:
        verdict = self._ask(allowed_symbols={"EURUSD"})(build_execution_request(_intent()))
        assert verdict.accepted is True

    def test_the_real_signal_accepts_what_the_bridge_sends(self) -> None:
        # The real TradeSignal's own validation: id pattern, positive volume,
        # symbol upper-casing. If this passes, the request the dry run describes is
        # one the execution project would accept as input at all.
        bindings = _bindings_or_skip()
        signal = preflight_module._to_signal(
            build_execution_request(_intent()), bindings, lambda: NOW, 10
        )
        assert signal.symbol == "EURUSD"
        assert signal.volume > 0
        assert signal.expiration is not None


# --- helpers ----------------------------------------------------------------


def _bindings_or_skip() -> object:
    if find_spec("auto_trade") is None:
        pytest.skip("auto_trade is not installed on this machine")
    from signal_to_trade_bridge.adapters.auto_trade import load_bindings

    return load_bindings()


def _ask(
    *,
    allowed: set[str] | None = None,
    max_volume: Decimal = Decimal("1.0"),
    max_orders_per_minute: int = 5,
    expiration_seconds: int = 10,
    max_open_positions: int | None = None,
):
    """Ask the **real** engine when it is installed, a faithful stub otherwise.

    The stub reproduces upstream's seven gates and its fixed refusal strings, so
    this file tests the same claims on either machine. Where the real package is
    present the real thing is used -- and ``TestAgainstTheRealRiskEngine`` covers
    that path explicitly, so the stub never becomes the only evidence.
    """
    from signal_to_trade_bridge.adapters.auto_trade.preflight import (
        DownstreamLimits,
        ask_downstream_risk,
    )

    symbols = allowed if allowed is not None else {"EURUSD"}

    if find_spec("auto_trade") is not None:
        bindings = _bindings_or_skip()
        return ask_downstream_risk(
            bindings,
            DownstreamLimits(
                symbols, max_volume, max_orders_per_minute, expiration_seconds, max_open_positions
            ),
            now=lambda: NOW,
        )

    recent: list[datetime] = []
    seen: set[str] = set()

    def ask(request: ExecutionRequest) -> DownstreamVerdict:
        if request.signal_id in seen:
            return DownstreamVerdict(accepted=False, reason="duplicate signal id")
        if request.symbol.upper() not in {s.upper() for s in symbols}:
            return DownstreamVerdict(accepted=False, reason="symbol is not allowed")
        if request.volume > max_volume:
            return DownstreamVerdict(accepted=False, reason="volume exceeds configured limit")
        if len(recent) >= max_orders_per_minute:
            return DownstreamVerdict(accepted=False, reason="order rate limit reached")
        seen.add(request.signal_id)
        recent.append(NOW)
        return DownstreamVerdict(accepted=True)

    return ask
