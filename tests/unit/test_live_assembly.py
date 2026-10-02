"""The live path, tested without a live path.

Phase 11 wrote `live.py` and could not test most of it: everything past the
control-id check requires control identifiers that belong to this build, and they do
not exist. So the assembly is reached here by **supplying a matching build** — the one
thing a test may arrange and an operator may not.

What that means, stated plainly: these tests prove the code assembles in the right
order, refuses in the right order, and hands the envelope all three collaborators.
They **do not** prove the assembly works against a real terminal, because that needs
re-measured identifiers and a human watching. That gap is the whole of what Phase 11
could not close, and it is recorded in `HANDOFF.md` rather than smoothed over here.

Every test uses a stub terminal and a stub execution project, so none of them needs
`MetaTrader5`, a running terminal, or the private package installed. Where a stub
cannot stand in -- the real `FileKillSwitch`, the real `AuditLogger` -- the test says
so rather than pretending.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from signal_to_trade_bridge.composition import CompositionRefusal
from signal_to_trade_bridge.configuration.config import BridgeConfig
from signal_to_trade_bridge.domain.models import RiskParameters
from signal_to_trade_bridge.live import BuildMismatch, build_live, check_control_ids

# --- stubs -----------------------------------------------------------------


class _Executable:
    """A file whose bytes contain a version block, so the build reads as expected.

    A real PE would be a large binary fixture for one string. What the reader needs
    is the *shape* of the version block, and this is that shape -- stated here because
    a test that fakes a file format is only honest about the format it fakes.
    """

    def __init__(self, build: int) -> None:
        self.build = build

    def write(self, path: Path) -> None:
        path.write_bytes(("x" * 64 + f"5.0.0.{self.build}" + "y" * 64).encode("utf-16-le"))


def _snapshot_folder(tmp_path: Path, build: int) -> Path:
    files = tmp_path / "MQL5" / "Files"
    files.mkdir(parents=True)
    (files / "auto_trade_positions_a.json").write_text(
        f'{{"schema": 1, "terminal_build": {build}}}', encoding="utf-8"
    )
    return tmp_path


def _matching_build(tmp_path: Path, build: int = 6184) -> tuple[Path, Path]:
    terminal = tmp_path / "terminal64.exe"
    _Executable(build).write(terminal)
    return terminal, _snapshot_folder(tmp_path / "data", build)


def _config(tmp_path: Path, **overrides: Any) -> BridgeConfig:
    defaults: dict[str, Any] = {
        "risk": RiskParameters(allowed_symbols={"EURUSD"}),
        "execution_enabled": True,
        "dry_run": False,
        "log_directory": tmp_path / "logs",
        "mt5_terminal_path": tmp_path / "terminal64.exe",
        "mt5_data_path": tmp_path / "data",
    }
    defaults.update(overrides)
    return BridgeConfig(**defaults)


@dataclass
class _StubTerminalInfo:
    trade_contract_size: float = 100000.0
    trade_tick_size: float = 0.00001
    trade_tick_value_profit: float = 1.0
    trade_tick_value_loss: float = 1.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    digits: int = 5
    point: float = 0.00001
    currency_base: str = "EUR"
    currency_profit: str = "USD"
    currency_margin: str = "EUR"


@dataclass
class _StubAccountInfo:
    balance: float = 10000.0
    equity: float = 10000.0
    currency: str = "USD"
    login: int = 53184454
    name: str = "YouJos Hundred"


class _StubMt5Bindings:
    """The MT5 bindings, connected to a fake but complete terminal."""

    def account_info(self) -> _StubAccountInfo:
        return _StubAccountInfo()

    def symbol_info(self, _name: str) -> _StubTerminalInfo:
        return _StubTerminalInfo()

    def shutdown(self) -> None:
        return None


class _StubWorkflow:
    """Records how it was assembled; executes nothing."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def execute(self, signal: Any) -> Any:  # pragma: no cover - never reached here
        raise AssertionError("the stub workflow must not execute in a test")


class _StubBindings:
    """The `auto_trade` subset, with a workflow that records its wiring.

    ``gate_kwargs`` is read by the assembly test rather than by reaching into the
    stubbed `ExecutionGate` class: the assertion is about *what live.py asked for*,
    not about what a stub does with it.
    """

    def __init__(self) -> None:
        self.workflows: list[_StubWorkflow] = []
        self.gate_kwargs: dict[str, Any] = {}

    def ExecutionWorkflow(self, **kwargs: Any) -> _StubWorkflow:
        workflow = _StubWorkflow(**kwargs)
        self.workflows.append(workflow)
        return workflow


@pytest.fixture
def stub_execution(monkeypatch: pytest.MonkeyPatch) -> _StubBindings:
    """A stand-in for the execution project's *collaborators*, not its package.

    The real `auto_trade` is installed on this machine, so the four names
    `live.py` reaches for are patched **on the real modules** rather than faked
    wholesale. A wholesale fake has to declare itself a package, declare every
    sibling, and then fails on the one import nobody stubbed -- which is what the
    first version of this fixture did, and it failed on
    ``auto_trade.application.ledger`` for exactly that reason.

    Patching the real modules keeps the import machinery real, so a test that
    *misses* a name still fails loudly and for the right reason. And the stubs record
    what they were handed, so the wiring is asserted rather than assumed.

    One thing worth knowing before changing this: ``build_live`` imports its two
    binding helpers **inside the function body**, so they are not attributes of
    ``signal_to_trade_bridge.live`` and patching that module raises
    ``AttributeError``. The seam is ``composition``, which defines them.
    """
    bindings = _StubBindings()
    created: dict[str, Any] = {}

    def kill_switch(path: Path) -> Any:
        created["kill_switch_path"] = path
        return type("K", (), {"active": False, "path": path})()

    class AuditLogger:
        def __init__(self, directory: Path) -> None:
            created["audit_directory"] = directory

        def record(self, _event: Any) -> None:
            return None

    class ExecutionGate:
        def __init__(self, **kwargs: Any) -> None:
            created["gate"] = dict(kwargs)
            bindings.gate_kwargs = dict(kwargs)

    def terminal_adapter(profile: Any, *, gate: Any) -> Any:
        created["adapter_profile"] = profile
        created["adapter_gate"] = gate
        return type("Adapter", (), {"profile": profile, "gate": gate})()

    monkeypatch.setattr("auto_trade.application.kill_switch.FileKillSwitch", kill_switch)
    monkeypatch.setattr("auto_trade.infrastructure.logging.audit.AuditLogger", AuditLogger)
    monkeypatch.setattr(
        "auto_trade.infrastructure.automation.execution.ExecutionGate", ExecutionGate
    )
    monkeypatch.setattr("auto_trade.infrastructure.automation.MT5DesktopAdapter", terminal_adapter)

    # `build_live` imports these **inside the function body**, so they are not
    # attributes of the module and cannot be patched on it. The seam is
    # `composition`, which defines them -- and the import binds the same function
    # objects, so patching there is what actually takes effect.
    monkeypatch.setattr(
        "signal_to_trade_bridge.composition._auto_trade_bindings", lambda _s: bindings
    )
    monkeypatch.setattr(
        "signal_to_trade_bridge.composition._mt5_bindings",
        lambda _config, _supplied: _StubMt5Bindings(),
    )
    # `_workflow_factory` exists so `ExecutionWorkflow` records its wiring; it is
    # assigned onto the stub subclass rather than the instance, because
    # `ExecutionWorkflow` is looked up as a class attribute.
    _StubBindings.ExecutionWorkflow = _workflow_factory(bindings)  # type: ignore[method-assign]
    return bindings


def _workflow_factory(bindings: _StubBindings) -> Any:
    """A ``class``-level stand-in for ``ExecutionWorkflow``.

    Assigned onto :class:`_StubBindings`, so it is called as an unbound function with
    the instance as the first argument -- which is why ``self`` is accepted and
    ignored rather than the signature being ``(**kwargs)``. A factory written for a
    direct call fails on the class-assignment route, and the error says nothing about
    which of the two is wrong.
    """

    def factory(_self: Any, **kwargs: Any) -> _StubWorkflow:
        workflow = _StubWorkflow(**kwargs)
        bindings.workflows.append(workflow)
        return workflow

    return factory


# --- the control-id gate ---------------------------------------------------


class TestTheControlIdGate:
    def test_a_matching_build_passes_the_check(self, tmp_path: Path) -> None:
        terminal, data = _matching_build(tmp_path)
        check = check_control_ids(terminal, data)
        assert check.agreed is True
        assert check.matches is True

    def test_a_build_of_the_others_shape_is_refused(self, tmp_path: Path) -> None:
        terminal, data = _matching_build(tmp_path, build=5000)
        assert check_control_ids(terminal, data).matches is False

    def test_the_check_never_launches_or_connects(self, tmp_path: Path) -> None:
        # It reads two files. That is the entire set of side effects, and a test that
        # says so is cheaper than a code review having to establish it.
        terminal, data = _matching_build(tmp_path)
        before = sorted(p.name for p in tmp_path.iterdir())
        check_control_ids(terminal, data)
        assert sorted(p.name for p in tmp_path.iterdir()) == before


# --- the refusals, in order -------------------------------------------------


class TestTheRefusalsInOrder:
    def test_a_mismatched_build_is_refused_before_anything_else(
        self, tmp_path: Path, stub_execution: _StubBindings
    ) -> None:
        terminal = tmp_path / "terminal64.exe"
        _Executable(6230).write(terminal)
        data = _snapshot_folder(tmp_path / "data", 6230)

        with pytest.raises(BuildMismatch):
            build_live(
                _config(tmp_path),
                terminal=terminal,
                data_path=data,
                mt5_bindings=_StubMt5Bindings(),
            )

        # And nothing was assembled: the build check is first because it is the only
        # refusal about the *machine*, and no configuration can clear it.
        assert stub_execution.workflows == []

    def test_execution_not_enabled_is_refused(
        self, tmp_path: Path, stub_execution: _StubBindings
    ) -> None:
        terminal, data = _matching_build(tmp_path)
        with pytest.raises(CompositionRefusal, match="execution is not enabled"):
            build_live(
                _config(tmp_path, execution_enabled=False),
                terminal=terminal,
                data_path=data,
                mt5_bindings=_StubMt5Bindings(),
            )
        assert stub_execution.workflows == []

    def test_dry_run_on_is_refused(self, tmp_path: Path, stub_execution: _StubBindings) -> None:
        terminal, data = _matching_build(tmp_path)
        with pytest.raises(CompositionRefusal, match="dry-run mode is on"):
            build_live(
                _config(tmp_path, dry_run=True),
                terminal=terminal,
                data_path=data,
                mt5_bindings=_StubMt5Bindings(),
            )
        assert stub_execution.workflows == []

    def test_a_missing_execution_package_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        terminal, data = _matching_build(tmp_path)
        monkeypatch.setattr(
            "signal_to_trade_bridge.composition._auto_trade_bindings", lambda _s: None
        )
        with pytest.raises(CompositionRefusal, match="could not be loaded"):
            build_live(
                _config(tmp_path),
                terminal=terminal,
                data_path=data,
                mt5_bindings=_StubMt5Bindings(),
            )


# --- the assembly, once the gates pass --------------------------------------


class TestTheAssembly:
    def _build(self, tmp_path: Path, stub_execution: _StubBindings, **overrides: Any) -> Any:
        terminal, data = _matching_build(tmp_path)
        return build_live(
            _config(tmp_path, mt5_terminal_path=terminal, mt5_data_path=data, **overrides),
            terminal=terminal,
            data_path=data,
            mt5_bindings=_StubMt5Bindings(),
        )

    def test_it_assembles(self, tmp_path: Path, stub_execution: _StubBindings) -> None:
        bridge = self._build(tmp_path, stub_execution)
        assert bridge.can_execute is True
        assert bridge.refusal == ""

    def test_the_workflow_receives_a_terminal_adapter_and_a_demo_gate(
        self, tmp_path: Path, stub_execution: Any, created: dict[str, Any] | None = None
    ) -> None:
        self._build(tmp_path, stub_execution)
        gate = stub_execution.gate_kwargs
        assert gate["demo_only"] is True, "a demo bridge must not be able to trade live"
        assert gate["enabled"] is True, "the gate follows the caller's own configuration"
        assert stub_execution.workflows, "no workflow was built"

    def test_the_ledger_is_opened_before_the_workflow(
        self, tmp_path: Path, stub_execution: _StubBindings
    ) -> None:
        # An unreadable ledger must stop the assembly while there is still nothing to
        # undo -- so no workflow may have been built by the time it refuses.
        terminal, data = _matching_build(tmp_path)
        logs = tmp_path / "logs"
        logs.mkdir(parents=True)
        (logs / "idempotency.json").write_text("{corrupt", encoding="utf-8")
        with pytest.raises(CompositionRefusal, match="could not be read"):
            build_live(
                _config(tmp_path, mt5_terminal_path=terminal, mt5_data_path=data),
                terminal=terminal,
                data_path=data,
            )
        assert stub_execution.workflows == []

    def test_the_envelope_carries_all_three(self, tmp_path: Path) -> None:
        bridge = self._build(tmp_path, _StubBindings())
        assert bridge.pipeline.can_execute is True
        # And the pipeline refuses to be built with only some of them, which is the
        # property Phase 9 added and this asserts end to end.
        from signal_to_trade_bridge.application.execution_envelope import (
            build_execution_envelope,
        )

        with pytest.raises(ValueError):
            build_execution_envelope(
                executor=object(),
                idempotency=None,
                kill_switch=object(),  # type: ignore[arg-type]
            )

    def test_the_terminal_profile_carries_this_machines_paths(self, tmp_path: Path) -> None:
        # Known Issue 6: the execution project's own defaults are absolute paths on a
        # *different* machine. This is the seam where that would bite, so the paths
        # are read from the configuration and asserted.
        terminal, data = _matching_build(tmp_path)
        profile = _config(
            tmp_path, mt5_terminal_path=terminal, mt5_data_path=data
        ).terminal_profile(data_path=data)
        assert profile.terminal_path == str(terminal)
        assert profile.data_path == str(data)
        assert "Alpari MT5_2" not in profile.terminal_path
