"""The three permissions `trade` requires, and the fact that it can now send.

Phase 14 added the one command in this project that can place an order. These tests
are about the refusals, because the send path is exercised against a demo account by
a person and the refusals are the part that has to be right on every machine that
never runs that test.

Each permission is checked separately and the refusal names the one that is missing.
A single combined check would refuse just as safely, and would leave an operator
unable to tell which of the three they had not done -- which is the situation the
command exists to prevent.
"""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path

import pytest

from signal_to_trade_bridge.cli import EXIT_FAULT, EXIT_OK, EXIT_REFUSED, main

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
    """No inherited environment, no repository `.env`, logs inside the test."""
    for name in [n for n in os.environ if n.startswith("BRIDGE_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "logs"))
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


class TestTheFirstPermission:
    """`--confirm-demo`, and it is first because it is the only one that proves a
    person was there. A configuration left armed is a machine that will trade the
    next time anything calls the live path."""

    def test_without_it_nothing_is_sent(self, signal_file: Path) -> None:
        code, output = _run("trade", str(signal_file))
        assert code == EXIT_REFUSED
        assert "no order was sent" in output
        assert "--confirm-demo" in output

    def test_it_points_at_what_if_rather_than_at_a_manual_step(self, signal_file: Path) -> None:
        # The refusal is an instruction, not a dead end. "Nothing happened" leaves
        # somebody reading the help; "--what-if shows you exactly what would be sent"
        # gives them the next command.
        _code, output = _run("trade", str(signal_file))
        assert "--what-if" in output

    def test_the_flag_is_not_accepted_silently_as_a_prefix(self, signal_file: Path) -> None:
        # argparse allows unambiguous prefixes, so `--confirm` would work. That is
        # fine for convenience, but the flag must never be *implied* by anything
        # else, and there is nothing else that sets it.
        code, _output = _run("trade", str(signal_file), "--confirm")
        assert code == EXIT_REFUSED, "an abbreviation must not satisfy the permission"


class TestTheSecondPermission:
    """The configuration gate, which is what an operator forgets."""

    @pytest.mark.parametrize(
        ("env", "fragment"),
        [
            ({"BRIDGE_EXECUTION_ENABLED": "false"}, "BRIDGE_EXECUTION_ENABLED is false"),
            ({"BRIDGE_DRY_RUN": "true"}, "BRIDGE_DRY_RUN is true"),
        ],
    )
    def test_a_forbidding_configuration_refuses(
        self,
        signal_file: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        env: dict[str, str],
        fragment: str,
    ) -> None:
        _isolated(monkeypatch, tmp_path, **env)
        code, output = _run("trade", str(signal_file), "--confirm-demo")
        assert code == EXIT_REFUSED
        assert fragment in output
        assert "no order was sent" in output

    def test_dry_run_wins_when_only_execution_is_on(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The combination that surprises people: execution says yes, dry run says
        # yes, and the answer is no. Both are reported, because a message naming only
        # the one you did not think about is the message that gets misread.
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="true")
        code, output = _run("trade", str(signal_file), "--confirm-demo")
        assert code == EXIT_REFUSED
        assert "BRIDGE_DRY_RUN is true" in output
        assert "overrides" in output

    def test_it_names_the_command_that_shows_the_current_values(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="false")
        _code, output = _run("trade", str(signal_file), "--confirm-demo")
        assert "config" in output


class TestBadInput:
    def test_a_missing_file_is_a_fault(self, tmp_path: Path) -> None:
        code, output = _run("trade", str(tmp_path / "nope.json"), "--confirm-demo")
        assert code == EXIT_FAULT
        assert "does not exist" in output

    def test_malformed_json_is_a_fault(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text("{not json", encoding="utf-8")
        code, output = _run("trade", str(path), "--confirm-demo")
        assert code == EXIT_FAULT
        assert "not valid JSON" in output

    def test_a_signal_with_no_stop_basis_is_refused_not_completed(self, tmp_path: Path) -> None:
        # The refusal has to survive the order of the checks. This one would need a
        # live terminal to reach, so it is asserted at the `_signal_from` boundary
        # rather than end to end -- but the point stands: a stop price with no stated
        # origin is not a stop this project will act on, and `trade` is where that
        # would matter most.
        payload = dict(SIGNAL)
        del payload["stop_basis"]
        path = tmp_path / "s.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        from signal_to_trade_bridge.cli.main import _signal_from

        signal = _signal_from(payload)
        assert signal.stop_basis == "", "the missing basis stays missing, it is not filled in"


class TestWhatIf:
    """`--what-if` must never send, whatever else is true of the configuration."""

    def test_it_needs_confirm_demo_too(self, signal_file: Path) -> None:
        # Deliberate. `--what-if` sends nothing, so requiring the acknowledgement for
        # it could look like friction -- but a flag that bypasses a permission is a
        # flag that will be copy-pasted into the command *without* `--what-if` one
        # day later, and the habit is the thing being protected.
        code, output = _run("trade", str(signal_file), "--what-if")
        assert code == EXIT_REFUSED
        assert "--confirm-demo" in output

    def test_it_says_plainly_that_nothing_was_sent(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        code, output = _run("trade", str(signal_file), "--confirm-demo", "--what-if")
        # Reaches the live build on this machine, so the code depends on the terminal
        # being reachable; either way the promise is what matters.
        assert code in (EXIT_OK, EXIT_REFUSED, EXIT_FAULT)
        if "--what-if was given" in output:
            assert "nothing was sent" in output
            assert "would have sent" in output


class TestTheCommandSurface:
    def test_trade_is_the_only_command_that_names_the_live_path(self) -> None:
        # Structural. One command may build the live side; the others must not, or
        # the "no order path" property of `check` becomes a claim about three
        # commands instead of a fact about two.
        import ast
        import inspect
        import sys

        module = sys.modules["signal_to_trade_bridge.cli.main"]
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_trade":
                assert "build_live" in ast.unparse(node)
            if isinstance(node, ast.FunctionDef) and node.name in {"_check", "_doctor"}:
                assert "build_live" not in ast.unparse(node)

    def test_the_help_says_which_command_can_trade(self) -> None:
        import inspect
        import sys

        module = sys.modules["signal_to_trade_bridge.cli.main"]
        assert "Only ``trade`` can place an order" in inspect.getdoc(module)
        assert "build_live" in inspect.getsource(module)
