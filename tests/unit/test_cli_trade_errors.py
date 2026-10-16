"""The `trade` paths that refuse, misread, or report -- everything except sending.

`test_cli_trade.py` pins the three permissions and `test_cli_whatif.py` pins the
recorder swap. The branches covered here are the error surfaces around them:
a file that cannot be read, a signal that cannot be used, a live side that
cannot be built, a pipeline that was never wired, and the two reports (the
intent lines and the execution lines) that only a decision carrying the full
result can produce. None of them needs a terminal: `build_live` is replaced
at the module attribute, exactly where `_trade` looks it up.
"""

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from signal_to_trade_bridge.cli import EXIT_FAULT, EXIT_OK, EXIT_REFUSED, main
from signal_to_trade_bridge.cli.main import _dispatch
from signal_to_trade_bridge.domain.models import TradeDecision

SIGNAL = {
    "id": "t-1",
    "symbol": "EURUSD",
    "timeframe": "H1",
    "action": "BUY",
    "direction": "LONG",
    "entry": "1.10000",
    "stop_loss": "1.09700",
    "take_profit": "1.10300",
    "stop_basis": "PULLBACK_EXTREME",
    "take_profit_basis": "SWING",
}


def _run(*argv: str) -> tuple[int, str]:
    out = StringIO()
    return main(list(argv), out=out), out.getvalue()


def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> None:
    """No inherited environment, no repository `.env`, logs inside the test.

    A terminal path, because `_trade` refuses before building anything when
    there is not one.
    """
    for name in [n for n in os.environ if n.startswith("BRIDGE_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "logs"))
    fake_terminal = tmp_path / "terminal64.exe"
    fake_terminal.write_bytes(b"")
    monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(fake_terminal))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.chdir(elsewhere)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def signal_file(tmp_path: Path) -> Path:
    path = tmp_path / "sig.json"
    path.write_text(json.dumps(SIGNAL), encoding="utf-8")
    return path


def _fake_build_live(monkeypatch: pytest.MonkeyPatch, bridge: object) -> None:
    """Replace the assembly `_trade` drives, at the attribute it reads."""
    import signal_to_trade_bridge.live as live_module

    def _fake(*args: object, **kwargs: object) -> object:
        return bridge

    monkeypatch.setattr(live_module, "build_live", _fake)


class _Bridge:
    """The shape `_trade` drives: a pipeline holder with a live flag."""

    def __init__(self, pipeline: object) -> None:
        self.pipeline = pipeline
        self.can_execute = True


def _intent() -> object:
    """A real, minimal intent, mirroring the factory `test_cli.py` documents.

    The exit-path tests need a decision that claims to have traded, and the
    domain will not let one exist without an intent -- which is the correct
    behaviour, and the reason this factory builds a real one rather than
    passing ``None``.
    """
    from signal_to_trade_bridge.domain.enums import (
        Direction,
        SignalAction,
        StopSource,
    )
    from signal_to_trade_bridge.domain.models import (
        AccountBalance,
        PositionSize,
        RiskParameters,
        Signal,
        StopLoss,
        SymbolSpec,
        TakeProfit,
        TakeProfitSource,
        TradeIntent,
    )

    entry = Decimal("1.10000")
    distance = Decimal("0.00300")
    return TradeIntent(
        signal=Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=entry,
            stop_loss=entry - distance,
            take_profit=entry + distance,
        ),
        symbol="EURUSD",
        direction=Direction.LONG,
        entry=entry,
        stop_loss=StopLoss(
            price=entry - distance, distance=distance, source=StopSource.SIGNAL, basis="SWING"
        ),
        take_profit=TakeProfit(
            price=entry + distance,
            distance=distance,
            source=TakeProfitSource.RR_DERIVED,
            basis="1R",
        ),
        position_size=PositionSize(
            volume=Decimal("0.10"),
            raw_volume=Decimal("0.10"),
            risk_amount=Decimal("50.00"),
            stop_distance=distance,
            risk_per_unit=Decimal("500.00"),
            ticks=Decimal("300"),
            tick_size=Decimal("0.00001"),
            tick_value=Decimal("1.0"),
        ),
        risk_parameters=RiskParameters(),
        account_balance=AccountBalance(balance=Decimal("10000.00"), currency="USD"),
        symbol_spec=SymbolSpec(
            symbol="EURUSD",
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
        ),
    )


def _executed_decision(**overrides: object) -> TradeDecision:
    """A decision that claims to have traded, with a real intent by default."""
    from signal_to_trade_bridge.domain.enums import DecisionAction
    from signal_to_trade_bridge.domain.models import ExecutionResult, TradeDecision

    values: dict[str, object] = {
        "signal_id": "x",
        "action": DecisionAction.EXECUTE,
        "reason": "PASSED",
        "intent": _intent(),
        "execution": ExecutionResult(signal_id="x", status=ExecutionResult.STATUS_ACCEPTED),
    }
    values.update(overrides)
    return TradeDecision(**values)  # type: ignore[arg-type]


class TestMainReportsAnEscapedRefusal:
    def test_a_composition_refusal_from_dispatch_is_an_error_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every refusal `dispatch` can produce today is caught at its own
        # level, so this handler has no production trigger -- which is exactly
        # why it needs a test. A refusal that escaped as a traceback would be
        # the one error message nobody can act on.
        from signal_to_trade_bridge.composition import CompositionRefusal

        # The module, not the package attribute: `signal_to_trade_bridge.cli`
        # re-exports the `main` *function*, so attribute access on the package
        # finds the function and not the module it came from.
        main_module = sys.modules["signal_to_trade_bridge.cli.main"]

        def _raising(args: object, parser: object) -> object:
            raise CompositionRefusal("the test machine refuses")

        monkeypatch.setattr(main_module, "_dispatch", _raising)
        out = StringIO()
        code = main_module.main(["check", "whatever.json"], out=out)
        assert code == EXIT_FAULT
        assert "ERROR: the test machine refuses" in out.getvalue()


class TestDispatchRefusesUnknownCommands:
    def test_an_unknown_command_cannot_slip_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `argparse` choices normally prevent this, so the stub parser stands
        # in for a caller that bypassed them. The point is the second lock:
        # dispatch must not fall off the end of its command list.
        seen: list[str] = []

        class _StubParser:
            def error(self, message: str) -> None:
                seen.append(message)

        with pytest.raises(AssertionError, match="unreachable"):
            _dispatch(SimpleNamespace(command="bogus"), _StubParser())  # type: ignore[arg-type]
        assert seen and "bogus" in seen[0]


class TestPlainRendersContainersAndEnums:
    def test_a_list_renders_each_item(self) -> None:
        from signal_to_trade_bridge.cli.main import _plain

        assert _plain([Decimal("0.5"), Path("x")]) == ["0.5", "x"]

    def test_a_tuple_renders_as_a_list(self) -> None:
        from signal_to_trade_bridge.cli.main import _plain

        assert _plain(("a", 1)) == ["a", 1]

    def test_an_enum_renders_as_its_value(self) -> None:
        from signal_to_trade_bridge.cli.main import _plain
        from signal_to_trade_bridge.domain.enums import Direction

        assert _plain(Direction.LONG) == "LONG"

    def test_anything_else_falls_back_to_str(self) -> None:
        from signal_to_trade_bridge.cli.main import _plain

        assert _plain(object()).startswith("<object object at 0x")


class TestDoctorReportsMissingPackages:
    def test_a_missing_binding_is_named_not_crashed_on(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # `doctor` reads each package with `find_spec`. Faking the lookup away
        # exercises the branch that reports a missing piece -- the whole reason
        # the command exists.
        import importlib.util

        real_find_spec = importlib.util.find_spec

        def _missing(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("MetaTrader5", "auto_trade"):
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", _missing)
        for name in [n for n in os.environ if n.startswith("BRIDGE_")]:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "logs"))
        code, output = _run("doctor")
        assert code == EXIT_FAULT
        assert "MetaTrader5 is not installed" in output
        assert "auto_trade is not installed" in output


class TestCheckRefusesAnArmedConfiguration:
    def test_execution_enabled_reaches_the_safe_path_refusal(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # `check` assembles the *safe* root, which refuses the live
        # configuration rather than degrading to a dry run. Arming the machine
        # and then asking for a decision must fail loudly, not preview
        # something that looks like an answer. The bindings are stubbed at the
        # loader (construction never touches the terminal) so the test reaches
        # the configuration refusal instead of the machine refusal.
        _isolated(
            monkeypatch,
            tmp_path,
            BRIDGE_EXECUTION_ENABLED="true",
            BRIDGE_DRY_RUN="false",
        )
        import signal_to_trade_bridge.composition as composition_module

        monkeypatch.setattr(
            composition_module,
            "load_mt5_bindings",
            lambda terminal_path=None: object(),
        )
        code, output = _run("check", str(signal_file))
        assert code == EXIT_FAULT
        assert "execution is enabled and dry-run is off" in output


class TestTradeUnreadableSignalFile:
    def test_a_directory_is_a_fault(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # `trade` reads the file itself rather than sharing `_check`'s reader,
        # so its own read-error branch needs its own test. A directory exists,
        # so the existence check passes, and reading it raises -- which is the
        # operator passing the log directory by mistake.
        _isolated(monkeypatch, tmp_path)
        code, output = _run("trade", str(tmp_path), "--confirm-demo")
        assert code == EXIT_FAULT
        assert "could not be read" in output

    def test_undecodable_bytes_name_the_encodings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # One byte that fails all four encodings: odd length defeats utf-16,
        # 0x81 is undefined in cp1252, and strict utf-8 rejects it. The message
        # has to name what was tried, or the operator has no next step.
        _isolated(monkeypatch, tmp_path)
        path = tmp_path / "s.bin"
        path.write_bytes(b"\x81")
        code, output = _run("trade", str(path), "--confirm-demo")
        assert code == EXIT_FAULT
        assert "not text in any encoding" in output


class TestTradeUnusableSignal:
    def test_a_json_object_missing_its_action_is_unusable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Valid JSON that is not a signal. The failure names usability rather
        # than syntax, because re-formatting valid JSON fixes nothing.
        _isolated(monkeypatch, tmp_path)
        path = tmp_path / "sig.json"
        path.write_text(json.dumps({"id": "x", "symbol": "EURUSD"}), encoding="utf-8")
        code, output = _run("trade", str(path), "--confirm-demo")
        assert code == EXIT_FAULT
        assert "not a usable signal" in output


class TestTradeLiveAssemblyRefusal:
    def test_a_live_side_that_cannot_be_built_is_a_fault(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Armed, confirmed, terminal configured -- and the assembly itself
        # refuses. The operator needs the assembly's reason verbatim, because
        # it names the gate that stopped the order.
        from signal_to_trade_bridge.composition import CompositionRefusal

        _isolated(
            monkeypatch,
            tmp_path,
            BRIDGE_EXECUTION_ENABLED="true",
            BRIDGE_DRY_RUN="false",
        )

        def _refusing(*args: object, **kwargs: object) -> object:
            raise CompositionRefusal("the test ledger is unreadable")

        import signal_to_trade_bridge.live as live_module

        monkeypatch.setattr(live_module, "build_live", _refusing)
        code, output = _run("trade", str(signal_file), "--confirm-demo")
        assert code == EXIT_FAULT
        assert "live path could not be built" in output
        assert "ledger is unreadable" in output


class TestTradeUnwiredPipeline:
    def test_a_live_pipeline_with_no_envelope_is_a_fault_not_a_send(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # `build_live` always wires an envelope; if one ever arrived without,
        # the preview must refuse rather than process a signal no executor is
        # attached to. `process` raises so the test also proves the signal is
        # never handled on the refusal path.
        _isolated(monkeypatch, tmp_path)

        class _Unwired:
            _envelope = None

            def wire_execution(self, envelope: object) -> None:
                return None

            def process(self, signal: object) -> object:
                raise AssertionError("the signal must not be handled without an envelope")

        _fake_build_live(monkeypatch, _Bridge(_Unwired()))
        code, output = _run("trade", str(signal_file), "--what-if")
        assert code == EXIT_FAULT
        assert "no execution envelope" in output


class _ReportingPipeline:
    """A pipeline that answers with a canned full decision.

    `submit_to_executor` lets a test emulate the live pipeline's last step:
    handing the request to whichever executor is wired when the signal is
    handled. With `--what-if` that is the recorder `_trade` swapped in, so the
    preview report has a real submitted request to print.
    """

    def __init__(self, decision: object, submit_to_executor: bool = False) -> None:
        from signal_to_trade_bridge.application.execution_envelope import (
            build_execution_envelope,
        )

        class _NeverUsed:
            def submit(self, request: object) -> object:
                raise AssertionError("replaced before use")

        class _Store:
            def contains(self, key: str) -> bool:
                return False

            def record_attempt(self, key: str, execution_id: str) -> None:
                return None

        class _Switch:
            @property
            def active(self) -> bool:
                return False

        self._envelope: object = build_execution_envelope(
            executor=_NeverUsed(),  # type: ignore[arg-type]
            idempotency=_Store(),  # type: ignore[arg-type]
            kill_switch=_Switch(),
        )
        self._decision = decision
        self._submit_to_executor = submit_to_executor

    def wire_execution(self, envelope: object) -> None:
        self._envelope = envelope

    def process(self, signal: object) -> object:
        if self._submit_to_executor:
            from signal_to_trade_bridge.domain.enums import Direction
            from signal_to_trade_bridge.domain.models import ExecutionRequest

            self._envelope.executor.submit(  # type: ignore[attr-defined]
                ExecutionRequest(
                    signal_id=getattr(signal, "signal_id", "s"),
                    symbol="EURUSD",
                    direction=Direction.LONG,
                    volume=Decimal("0.99"),
                    entry=Decimal("1.12086"),
                    stop_loss=Decimal("1.11786"),
                    take_profit=Decimal("1.12386"),
                    comment="",
                )
            )
        return self._decision


class TestTradeIntentAndExecutionLines:
    def test_a_traded_decision_prints_its_intent_and_its_result(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The intent block and the status block only render for a decision
        # that carries the full result. A refusal-heavy suite can cover every
        # permission while these lines -- the operator's confirmation of what
        # went out -- stay dark.
        _isolated(
            monkeypatch,
            tmp_path,
            BRIDGE_EXECUTION_ENABLED="true",
            BRIDGE_DRY_RUN="false",
        )
        _fake_build_live(monkeypatch, _Bridge(_ReportingPipeline(_executed_decision())))
        code, output = _run("trade", str(signal_file), "--confirm-demo")
        assert code == EXIT_OK
        assert "EURUSD LONG" in output
        assert "0.10" in output
        assert "1.09700" in output
        assert "status" in output
        assert "ACCEPTED" in output

    def test_an_execute_decision_without_a_result_says_so_plainly(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # `EXECUTE` with no execution result is the strangest honest outcome:
        # the pipeline decided to trade and has nothing to show for it. It
        # must refuse with that sentence, not print an intent as if it went.
        _isolated(
            monkeypatch,
            tmp_path,
            BRIDGE_EXECUTION_ENABLED="true",
            BRIDGE_DRY_RUN="false",
        )
        decision = _executed_decision(execution=None)
        assert decision.execution is None
        _fake_build_live(monkeypatch, _Bridge(_ReportingPipeline(decision)))
        code, output = _run("trade", str(signal_file), "--confirm-demo")
        assert code == EXIT_REFUSED
        assert "no order was sent" in output
        assert "carries no execution result" in output
        assert "ACCEPTED" not in output


class TestWhatIfPreviewLines:
    def test_the_preview_prints_the_request_and_the_recorded_pause(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The request block and the pause block render only when the recorder
        # actually saw a submission. A preview that prints "nothing was sent"
        # without the numbers is a refusal wearing a preview's clothes.
        _isolated(
            monkeypatch,
            tmp_path,
            BRIDGE_EXECUTION_ENABLED="true",
            BRIDGE_DRY_RUN="false",
            BRIDGE_PRE_SUBMIT_DELAY_ENABLED="true",
            BRIDGE_PRE_SUBMIT_DELAY_MIN_MS="1000",
            BRIDGE_PRE_SUBMIT_DELAY_MAX_MS="1000",
        )
        _fake_build_live(
            monkeypatch,
            _Bridge(_ReportingPipeline(_executed_decision(), submit_to_executor=True)),
        )
        code, output = _run("trade", str(signal_file), "--confirm-demo", "--what-if")
        assert code == EXIT_OK
        assert "would have sent" in output
        assert "EURUSD LONG 0.99 lots" in output
        assert "stop 1.11786" in output
        assert "target 1.12386" in output
        assert "pre-submit pause" in output
        assert "1000 ms" in output
