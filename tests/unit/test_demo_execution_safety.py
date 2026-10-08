"""The single controlled demo execution: one signal, one pass, no resends.

This file pins the properties the demo execution test relies on, without
sending anything. Every test here runs against doubles -- no terminal, no
`auto_trade`, no order -- because the properties are about the *shape* of the
path, and a shape test that needs a terminal stops testing on the machine that
most needs it.

The claims, each enforced below:

* `trade` hands one signal file to the pipeline exactly once. There is no
  loop, no retry, no second entry -- structurally, not by convention. A retry
  after an `UNKNOWN` outcome may open a second position, so "runs once" has to
  be a fact about the code rather than a promise about the operator.
* An `UNKNOWN` execution surfaces as exit code 3, distinct from a refusal.
  That is what stops a caller -- or a person -- from re-running the command
  to "see if it works this time".
* The real path still needs every permission at once: `--confirm-demo` *and*
  an armed configuration. The demo execution is not a relaxed mode; it is the
  fully gated path with a human acknowledgement attached.
* The verification commands (`check`, `doctor`, `signal`, `config`) cannot
  reach the live side at all, so the whole pre-flight in
  `docs/demo-execution-test.md` is read-only by construction.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from signal_to_trade_bridge.cli import EXIT_FAULT, EXIT_REFUSED, EXIT_UNKNOWN, main


def _cli_main_module():
    """The `cli.main` *module*, unambiguously.

    `cli/__init__.py` re-exports the function `main`, which shadows the
    submodule of the same name on the package object -- so attribute access
    finds the function. `sys.modules` is the only spelling that means the
    module. (Same collision as `test_cli_refusals.py` records; same remedy.)
    """
    return sys.modules["signal_to_trade_bridge.cli.main"]


def _trade_source() -> ast.FunctionDef:
    module = _cli_main_module()
    tree = ast.parse(inspect.getsource(module._trade))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))


def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> None:
    """No inherited environment, no repository `.env`, logs inside the test."""
    for name in [n for n in os.environ if n.startswith("BRIDGE_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "logs"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.chdir(elsewhere)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


SIGNAL = {
    "id": "demo-exec-1",
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


@pytest.fixture
def signal_file(tmp_path: Path) -> Path:
    path = tmp_path / "sig.json"
    path.write_text(json.dumps(SIGNAL), encoding="utf-8")
    return path


class TestOneSignalOnePass:
    """`trade` processes the file exactly once and never resends."""

    def test_trade_calls_process_exactly_once(self) -> None:
        # Structural. A second `process` call -- a retry, a confirmation pass,
        # a "best of two" -- would be a second chance to send. Counting call
        # sites in the one function allowed to place an order is the only
        # assertion that cannot be satisfied by a path that sends twice.
        func = _trade_source()
        calls = [
            node
            for node in ast.walk(func)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "process"
        ]
        assert len(calls) == 1

    def test_trade_contains_no_loop(self) -> None:
        # No `for`, no `while`: there is no construct in `_trade` that could
        # iterate over signals, entries, or attempts. One file in, at most one
        # order out -- no grid, no martingale, no multi-entry has anywhere to
        # live on this path.
        func = _trade_source()
        loops = [node for node in ast.walk(func) if isinstance(node, (ast.For, ast.While))]
        assert loops == []

    def test_the_pipeline_is_reached_exactly_once(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Behavioural complement to the two structural tests above: through the
        # public `main` entry point, with an armed configuration and the human
        # acknowledgement, the pipeline handles the signal one time.
        from signal_to_trade_bridge.domain.enums import DecisionAction
        from signal_to_trade_bridge.domain.models import TradeDecision

        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        # A terminal path, because `_trade` faults before building anything
        # without one -- and the first version of this test failed on that
        # fault and never reached the pipeline it exists to count.
        fake_terminal = tmp_path / "terminal64.exe"
        fake_terminal.write_bytes(b"")
        monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(fake_terminal))

        calls = []

        class _OncePipeline:
            _envelope: object = object()

            def wire_execution(self, envelope: object) -> None:
                self._envelope = envelope

            def process(self, signal: object) -> object:
                calls.append(getattr(signal, "signal_id", None))
                return TradeDecision(
                    signal_id=getattr(signal, "signal_id", "s"),
                    action=DecisionAction.NO_TRADE,
                    reason="demo-prep: counting only, nothing was sent",
                )

        pipeline = _OncePipeline()

        class _Bridge:
            def __init__(self) -> None:
                self.pipeline = pipeline  # type: ignore[assignment]
                self.can_execute = True

        import signal_to_trade_bridge.live as live_module

        original = sys.modules["signal_to_trade_bridge.live"]
        sys.modules["signal_to_trade_bridge.live"] = SimpleNamespace(  # type: ignore[assignment]
            build_live=lambda *a, **k: _Bridge(),
            BuildMismatch=live_module.BuildMismatch,
        )
        try:
            out = StringIO()
            code = main(["trade", str(signal_file), "--confirm-demo"], out=out)
        finally:
            sys.modules["signal_to_trade_bridge.live"] = original  # type: ignore[assignment]
        assert calls == ["demo-exec-1"]
        assert code == EXIT_REFUSED  # NO_TRADE: decided once, sent nothing


class TestUnknownIsNeverARetry:
    def test_an_unknown_demo_execution_is_exit_three(self) -> None:
        # The outcome the demo test dreads: the order may or may not have
        # reached the terminal. Exit 3 exists so no caller -- human or script
        # -- reads it as "refused, safe to try again". A resend may open a
        # second position, and the ledger exists precisely to stop one.
        from signal_to_trade_bridge.cli.main import _decision_code
        from signal_to_trade_bridge.domain.enums import DecisionAction
        from signal_to_trade_bridge.domain.models import ExecutionResult, TradeDecision

        decision = TradeDecision(
            signal_id="demo-exec-1",
            action=DecisionAction.NO_TRADE,
            reason="EXECUTION_UNKNOWN",
            execution=ExecutionResult(signal_id="demo-exec-1", status="UNKNOWN"),
        )
        assert _decision_code(decision) == EXIT_UNKNOWN
        assert EXIT_UNKNOWN != EXIT_REFUSED


class TestTheDemoPathIsNotARelaxedMode:
    """The execution test uses the fully gated path, acknowledgement included."""

    def test_armed_config_without_the_flag_still_refuses(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Deterministic version of the first-permission test under an armed
        # configuration: even with execution enabled and dry-run off, no flag
        # means no composition and no order. The flag is per-invocation proof
        # a person was present, and arming the machine must never imply it.
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        out = StringIO()
        code = main(["trade", str(signal_file)], out=out)
        assert code == EXIT_REFUSED
        assert "--confirm-demo" in out.getvalue()

    def test_flag_without_armed_config_still_refuses(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The mirror: the acknowledgement alone does not arm anything. Safe
        # defaults stay safe no matter what flags are typed.
        _isolated(monkeypatch, tmp_path)
        out = StringIO()
        code = main(["trade", str(signal_file), "--confirm-demo"], out=out)
        assert code == EXIT_REFUSED
        assert "BRIDGE_EXECUTION_ENABLED is false" in out.getvalue()


class TestPreflightCannotSend:
    """The verification commands have no path to the live side."""

    @pytest.mark.parametrize("command", ["_check", "_doctor", "_signal", "_config"])
    def test_no_verification_command_reaches_the_live_path(self, command: str) -> None:
        # The whole pre-flight in `docs/demo-execution-test.md` -- `doctor`,
        # `signal`, `check`, `config` -- is read-only by construction, not by
        # discipline. If any of these grew a `build_live` call, running the
        # checklist could assemble the side that can place an order.
        module = _cli_main_module()
        source = inspect.getsource(getattr(module, command))
        assert "build_live" not in source
        assert "wire_execution" not in source

    def test_check_is_a_fault_on_a_missing_file_never_a_trade(
        self, signal_file: Path, tmp_path: Path
    ) -> None:
        # `check` is the signal-validity step of the pre-flight. A file that is
        # not there must be a fault (exit 2), and under no input may `check`
        # report an execution result -- it goes through `build_bridge`, which
        # attaches no executor.
        _ = signal_file
        out = StringIO()
        code = main(["check", str(tmp_path / "nope.json")], out=out)
        assert code == EXIT_FAULT
        assert "ERROR" in out.getvalue()
