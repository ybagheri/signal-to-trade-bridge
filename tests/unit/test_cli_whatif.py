"""`--what-if` must never send. The test that should have existed first.

The first version of this flag built the live side -- which wires the **real**
executor -- processed the signal, and then printed "nothing was sent". The order had
already gone out. It was discovered the worst possible way: an `--what-if` run came
back `UNKNOWN`, carrying the execution project's own sentence about an order that
"may or may not have reached the terminal".

So the property is asserted directly and with no terminal involved: the executor
attached to the pipeline *at the moment the signal is processed* must be a recorder,
and the envelope's other two collaborators must have survived the swap. A flag whose
whole contract is "this sends nothing", tested only by its printed output, is a flag
tested by the thing it is not responsible for.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from signal_to_trade_bridge.cli import EXIT_OK, EXIT_REFUSED, main


def _dummy_envelope() -> object:
    """A real envelope with a placeholder executor and real-shaped collaborators.

    Built through the real factory, so the test cannot drift from what
    `ExecutionEnvelope` actually requires. The executor is a trivial object because
    it is about to be replaced -- the point is that the ledger and the kill switch
    are real enough to be carried across the swap and checked afterwards.
    """
    from signal_to_trade_bridge.application.execution_envelope import build_execution_envelope

    class _NeverUsed:
        def submit(self, request: object) -> object:
            raise AssertionError("the live executor must not be reachable in this test")

    class _Store:
        def contains(self, key: str) -> bool:
            return False

        def record_attempt(self, key: str, execution_id: str) -> None:
            return None

    class _Switch:
        @property
        def active(self) -> bool:
            return False

    return build_execution_envelope(
        executor=_NeverUsed(),  # type: ignore[arg-type]
        idempotency=_Store(),  # type: ignore[arg-type]
        kill_switch=_Switch(),  # type: ignore[arg-type]
    )


class _RecorderPipeline:
    """Stands in for the live pipeline and remembers what was wired into it.

    `expect_recorder` is what makes the two tests meaningful in opposite directions.
    An earlier version asserted unconditionally that the live executor was a
    `FakeTradeExecutor`, which meant the test for "without `--what-if` the live
    executor stays" was asserting the exact opposite of what it was named for -- and
    it failed with a message about the flag doing its job.

    So the spy is told what to expect, and the assertion is about the object that
    was **in place when the signal was handled**, not about the history of envelopes:
    `build_live` legitimately wires a live one first.
    """

    def __init__(self, *, expect_recorder: bool = True) -> None:
        self.expect_recorder = expect_recorder
        # `_envelope` because that is the private attribute `wire_execution` sets and
        # `_trade` reads back. The double has to match the real object's shape.
        #
        # It starts **already wired**, because `build_live` does exactly that before
        # `_trade` sees it, and `_trade` then reads this attribute to swap the
        # executor. A spy that started empty made the swap fail on `None`.
        self._initial: object = _dummy_envelope()
        self._envelope: object = self._initial

    @property
    def envelopes(self) -> list[object]:
        """Every envelope that has been in place, oldest first."""
        return [self._initial, self._envelope]

    def wire_execution(self, envelope: object) -> None:
        self._envelope = envelope

    def process(self, signal: object) -> object:
        from signal_to_trade_bridge.domain.enums import DecisionAction
        from signal_to_trade_bridge.domain.models import TradeDecision

        current = self._envelope
        assert current is not None, "the signal was processed before any envelope was wired"
        name = type(current.executor).__name__  # type: ignore[attr-defined]
        if self.expect_recorder:
            assert name == "FakeTradeExecutor", (
                f"the executor in place while the signal was handled was {name}. "
                f"--what-if promises nothing is sent, so the recorder has to be attached "
                f"before the signal reaches the pipeline, not after."
            )
        else:
            assert name != "FakeTradeExecutor", (
                "the recorder was attached without --what-if being given, so the "
                "substitution is not conditional on the flag"
            )
        # The two safety collaborators must survive the swap. Replacing the executor
        # must not become a way to drop the ledger or the kill switch.
        assert current.idempotency is not None  # type: ignore[attr-defined]
        assert current.kill_switch is not None  # type: ignore[attr-defined]
        # `NO_TRADE`, not `DRY_RUN`: the domain refuses a DRY_RUN with no intent, and
        # it is right to -- "nothing was sent" is a claim about a real decision. A
        # refusal is enough to prove what this proves, which is *what was attached*.
        return TradeDecision(
            signal_id=getattr(signal, "signal_id", "s"),
            action=DecisionAction.NO_TRADE,
            reason="recorder: nothing was sent",
        )


def _run_with_spy_live(
    *argv: str, expect_recorder: bool = True
) -> tuple[int, str, _RecorderPipeline]:
    """Run `main` with `build_live` replaced by one that hands back a spy pipeline.

    `expect_recorder` is passed to the spy, which is how the two tests in this file
    assert opposite things about the same code path without either of them lying.
    """
    # Imported first, so there is a real module to put back. Reading
    # `sys.modules["signal_to_trade_bridge.live"]` without importing it first raises
    # `KeyError` on a clean interpreter -- which is what the first version of this
    # test did, and it failed for a reason that had nothing to do with what it was
    # checking.
    import signal_to_trade_bridge.live as live_module

    pipeline = _RecorderPipeline(expect_recorder=expect_recorder)

    class _Bridge:
        def __init__(self) -> None:
            self.pipeline = pipeline  # type: ignore[assignment]
            self.can_execute = True

    original = sys.modules["signal_to_trade_bridge.live"]
    sys.modules["signal_to_trade_bridge.live"] = SimpleNamespace(  # type: ignore[assignment]
        build_live=lambda *a, **k: _Bridge(),
        BuildMismatch=live_module.BuildMismatch,
    )
    try:
        out = StringIO()
        code = main(list(argv), out=out)
    finally:
        sys.modules["signal_to_trade_bridge.live"] = original  # type: ignore[assignment]
    return code, out.getvalue(), pipeline


@pytest.fixture
def signal_file(tmp_path: Path) -> Path:
    import json

    payload = {
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
    path = tmp_path / "sig.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> None:
    import os

    for name in [n for n in os.environ if n.startswith("BRIDGE_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "logs"))
    # A terminal path, because `_trade` refuses before building anything when there
    # is not one -- and the first version of these tests cleared the environment
    # without setting one, so they failed on the refusal and never reached the
    # substitution they exist to check.
    fake_terminal = tmp_path / "terminal64.exe"
    fake_terminal.write_bytes(b"")
    monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(fake_terminal))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    monkeypatch.chdir(elsewhere)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


class TestWhatIfNeverSends:
    def test_a_recorder_is_attached_before_the_signal_is_handled(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        code, output, pipeline = _run_with_spy_live(
            "trade", str(signal_file), "--confirm-demo", "--what-if"
        )
        # The assertion that matters is inside the stub's `process`, which fails if
        # anything other than a recorder was in place when the signal was handled.
        assert pipeline.envelopes, "no envelope was wired, so the swap never happened"
        assert "nothing was sent" in output
        assert "Only the final click was replaced" in output
        assert code in (EXIT_OK, EXIT_REFUSED)

    def test_the_ledger_and_kill_switch_survive_the_swap(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Asserted separately because it is a different property: not "does not
        # send" but "does not send by giving up the safety envelope". An envelope with
        # a recorder and no kill switch would pass a naive version of the first test.
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        _code, _output, pipeline = _run_with_spy_live(
            "trade", str(signal_file), "--confirm-demo", "--what-if"
        )
        for envelope in pipeline.envelopes:
            assert envelope.idempotency is not None  # type: ignore[attr-defined]
            assert envelope.kill_switch is not None  # type: ignore[attr-defined]

    def test_it_needs_confirm_demo_too(self, signal_file: Path) -> None:
        # Deliberate. `--what-if` sends nothing, so requiring the acknowledgement
        # could look like friction -- but a flag that bypasses a permission is a flag
        # that will be copy-pasted into the command *without* `--what-if` one day
        # later, and the habit is the thing being protected.
        out = StringIO()
        code = main(["trade", str(signal_file), "--what-if"], out=out)
        assert code == EXIT_REFUSED
        assert "--confirm-demo" in out.getvalue()

    def test_without_the_flag_no_envelope_is_swapped(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The complement, and the reason the first test can be trusted: the
        # substitution is conditional on the flag, not a property of the command.
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        _code, _output, pipeline = _run_with_spy_live(
            "trade", str(signal_file), "--confirm-demo", expect_recorder=False
        )
        # The envelope in place, not the history: without `--what-if` nothing was
        # replaced, so the live executor the dummy envelope was built with is still
        # active. A `FakeTradeExecutor` here would mean the substitution happens
        # whether or not the flag was given.
        current = pipeline._envelope
        assert current is not None, "build_live should have wired an envelope"
        assert type(current.executor).__name__ != "FakeTradeExecutor", (
            "the recorder replaced the live executor without --what-if being given"
        )


class TestTheRecordingIsVisible:
    def test_it_reports_the_request_it_would_have_sent(
        self, signal_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The point of the flag: not just "nothing was sent" but *this is what would
        # have gone out*. A refusal to send that shows no numbers is a refusal, not a
        # preview, and the operator is left to re-derive them by hand.
        _isolated(monkeypatch, tmp_path, BRIDGE_EXECUTION_ENABLED="true", BRIDGE_DRY_RUN="false")
        _code, output, _pipeline = _run_with_spy_live(
            "trade", str(signal_file), "--confirm-demo", "--what-if"
        )
        assert "would have sent" in output
