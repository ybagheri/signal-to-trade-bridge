"""The live side: what Phase 11 assembles, and the two gates in front of it.

Phase 10 left the composition root refusing the live path. This module supplies what
was missing -- a terminal adapter, a kill switch, an audit log and the
``ExecutionWorkflow`` -- and **the refusal moves from "not built yet" to "two
specific things I checked"**, which is a different and much better place for it to be.

### Gate 1: the control identifiers must belong to this build

A control identifier is a *position in a window*, not a stable name. MT5 adds and
removes controls between builds, so an id measured on one build can address a
different control, or nothing, on another.

`auto-trade`'s own rule is explicit: *"Never substitute a control identifier you have
not measured... If a build presents something different, refuse and report it."* So
:func:`check_control_ids` reads the build from two independent sources, requires
them to agree, and **refuses when the build is not the one the identifiers were
measured on.**

`scripts/control_ids.py` is the same check as a runnable report. This is the version
the composition root calls, because a check nobody runs is a check that will not be
there when it matters.

**The re-measurement, and what it found.** The identifiers were originally measured
on build 6184 and this terminal runs 6230 -- 46 builds, which is why Gate 1 refused.
The remedy was never to change the number, so the number was changed **after a
measurement**:

```
auto-trade terminal-check
```

`auto_trade.interfaces.preflight.run_check` is upstream's own read-only probe. It
opens the order dialog, walks the control tree, reports every expected identifier as
OK / DRIFTED / MISSING, and closes the dialog again -- a dialog left open over a
trading terminal, or a click on an unrecognised one, is a market order.

The result on build 6230, recorded in `logs/terminal_check.json`:

| control | measured | found |
|---|---|---|
| `symbol` | 10325 | 10325 |
| `volume` | 10333 | 10333 |
| `stop_loss` | 10334 | 10334 |
| `take_profit` | 10336 | 10336 |
| `final_control_buy` | 10408 | 10408 |
| `final_control_sell` | 10409 | 10409 |
| `trade_grid` | 10328 | 10328 |

13 of 13 controls OK, nothing drifted, nothing missing. The build moved and no
control this project depends on moved with it, so the measured-on build is now
**6230**.

**Why that is a measurement and not a bypass.** The constant moved from 6184 to 6230
*after* every identifier was observed in place on 6230, which is the opposite of
the failure the rule exists to prevent. Had any control read DRIFTED or MISSING, the
constant would not have moved and the table above would name the control that moved
instead. A re-measurement that cannot report failure is not a re-measurement, so
`check_control_ids` keeps refusing the moment the build changes again.

### Gate 2: the terminal must already be running and logged in

:func:`build_live` never launches a terminal. ``initialize`` connects;
``launch`` starts a process, and a process that starts a trading terminal is a
process that can start it by accident -- and one that starts it *without a login* is
worse. The terminal must be up and logged in before this is called, and the account
type is checked: a non-``DEMO`` account is refused.

### What "live" means here, precisely

Assembling this does not place an order. It produces a pipeline that *can*, gated on:

* the control identifiers matching the build (:func:`check_control_ids`)
* ``config.execution_enabled`` and ``not config.dry_run`` (the caller's own act)
* the ledger being readable (inherited from Phase 9)
* the kill switch being clear
* the account being ``DEMO``

**A dry run still cannot reach a final control**, and that is upstream's guarantee
rather than this project's: the workflow refuses before ``execute_order`` *and* the
adapter closes the dialog when the gate says dry-run. Two independent guards, and
this module reaches neither.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signal_to_trade_bridge.composition import (
    Bridge,
    CompositionRefusal,
    resolve_ledger,
)
from signal_to_trade_bridge.configuration.config import BridgeConfig

__all__ = [
    "BuildMismatch",
    "ControlIdCheck",
    "build_live",
    "check_control_ids",
]

#: The build `auto-trade`'s control identifiers were measured on.
#:
#: **6230, since the Phase 13 re-measurement.** It was 6184, and this terminal runs
#: 6230, so :func:`check_control_ids` refused. The constant was changed only *after*
#: `auto-trade terminal-check` reported all 13 controls present with the identifier
#: they were measured for, on 6230 -- the probe is upstream's, it is read-only, and
#: the evidence is kept in `logs/terminal_check.json`. See the module docstring for
#: the table.
#:
#: **The next MT5 update will set this check off again, and that is the design.** A
#: number that only ever moves when a measurement says so is the difference between a
#: measurement and a constant somebody changed to make a test pass.
MEASURED_ON_BUILD = 6230


class BuildMismatch(CompositionRefusal):
    """The terminal's build is not the one the control identifiers were measured on.

    Its own class rather than a message, because it is the one refusal that is
    **about the machine rather than about a trade**, and an operator meeting it needs
    to know that no amount of configuration will clear it. Only re-measuring the
    identifiers on this build will.
    """


@dataclass(frozen=True, slots=True)
class ControlIdCheck:
    """What two independent sources said the build is, and whether it matches.

    ``agreed`` is a field rather than an implied success because the two sources can
    legitimately disagree -- the executable on disk and the running process are
    different things, and a terminal that was updated while running is exactly the
    case where they would. That is a refusal in its own right and it needs saying.
    """

    build: int | None
    sources: dict[str, int | None]
    expected: int
    agreed: bool

    @property
    def matches(self) -> bool:
        """Whether the ids may be used at all."""
        return self.agreed and self.build == self.expected

    def refusal(self) -> str:
        """Why not, in one paragraph an operator can act on.

        **Empty when there is nothing to refuse**, and that is a real case that used
        to be wrong: the build was reconciled on 6230, ``matches`` became ``True``,
        and this method still returned the full "a gap of 0 builds" paragraph --
        because it branched on whether the *sources* were readable and never asked
        whether the builds agreed. ``doctor`` reads ``matches`` for its verdict and
        ``refusal()`` for the text under it, so it would have printed **ok** directly
        above a refusal. A method that cannot say "no reason" will eventually be
        asked to justify a pass.
        """
        if self.matches:
            return ""
        readable = {k: v for k, v in self.sources.items() if v is not None}
        if not readable:
            return (
                "The terminal's build could not be read from any source, so the control "
                "identifiers cannot be confirmed for it. They are refused rather than "
                "assumed: a control identifier is a position in a window, and using one "
                "whose window has not been confirmed is how a click lands on the wrong "
                "control."
            )
        if not self.agreed:
            return (
                f"The two sources disagree about the build ({readable}), which means the "
                f"terminal on disk and the terminal running are not the same version. A "
                f"running-but-stale terminal is a real state -- MT5 updates itself between "
                f"launches -- and it is refused rather than resolved by preferring one."
            )
        return (
            f"The measured control identifiers belong to build {self.expected} and this "
            f"terminal is build {self.build} -- a gap of "
            f"{abs(int(self.build or 0) - self.expected)} builds. Upstream's rule is explicit: "
            f"never substitute a control identifier you have not measured, and if a build "
            f"presents something different, refuse and report it. Every identifier has to be "
            f"re-measured on this build, at this display resolution, before the live path can "
            f"be assembled."
        )


def check_control_ids(
    terminal: Path, data_path: Path | None = None, *, expected: int | None = None
) -> ControlIdCheck:
    """Read the build from two independent sources and compare it.

    ``expected`` defaults to :data:`MEASURED_ON_BUILD`, read at **call** time rather
    than bound at definition time. A default argument freezes the value when the
    module is imported, so the constant and the check it is supposed to guard could
    disagree -- and the only way to notice would be to edit the constant, which is
    exactly what happened when the re-measurement moved it: the function kept
    comparing against 6184 no matter what the module said.

    Two sources, because one is a file on disk and the other is a running process
    reporting on itself, and the whole point of the check is that they are not the
    same thing. Where the second is unavailable -- no published snapshot, no
    indicator attached -- the first stands alone and ``agreed`` is ``False`` rather
    than ``True``, because
    "one source said so" is not agreement.

    Read-only throughout. It reads the executable and the snapshot file; it does not
    open a terminal, connect to one, or touch the window.
    """
    sources = {
        "terminal64.exe FileVersion": _build_from_executable(terminal),
        "published position snapshot": _build_from_snapshot(data_path)
        if data_path is not None
        else None,
    }
    readable = [v for v in sources.values() if v is not None]
    return ControlIdCheck(
        build=readable[0] if readable else None,
        sources=sources,
        expected=MEASURED_ON_BUILD if expected is None else expected,
        agreed=len(readable) >= 2 and len(set(readable)) == 1,
    )


def build_live(
    config: BridgeConfig,
    *,
    terminal: Path,
    data_path: Path | None = None,
    bindings: Any | None = None,
    mt5_bindings: Any | None = None,
) -> Bridge:
    """Assemble the side that *can* place an order, or refuse.

    Refuses in this order, and the order is the design:

    1. **the control identifiers must match this build** -- a machine fact, and the
       only refusal here that no configuration can clear
    2. **execution must be enabled and dry-run off** -- the caller's own act
    3. **the terminal must already be running**, and the account must be ``DEMO``

    The control-id check comes first because it is the only one that is about the
    *machine*: every other refusal is about configuration, and an operator can
    change configuration. A 46-build gap cannot be configured away.
    """
    from signal_to_trade_bridge.adapters.mt5 import (
        MT5AccountProvider,
        MT5PositionReader,
        MT5SymbolSpecProvider,
    )
    from signal_to_trade_bridge.application.risk_service import RiskService
    from signal_to_trade_bridge.composition import _auto_trade_bindings, _mt5_bindings

    check = check_control_ids(terminal, data_path)
    if not check.matches:
        raise BuildMismatch(check.refusal())

    if not config.execution_enabled:
        raise CompositionRefusal(
            "execution is not enabled. Enabling it is a separate, explicit act, and this "
            "refusal is what makes 'we have not turned that on' a true statement."
        )
    if config.dry_run:
        raise CompositionRefusal(
            "dry-run mode is on, so nothing would be sent. A dry run does not need the live "
            "side assembled at all: build_bridge() already reports in full."
        )

    resolved = _auto_trade_bindings(bindings)
    if resolved is None:
        raise CompositionRefusal(
            "the execution project could not be loaded, so there is nothing to execute "
            "through. Refusing rather than degrading: an operator who enabled execution and "
            "silently got a dry run would not know their setting was ignored."
        )

    # The terminal must be UP already. `initialize` connects; nothing here launches.
    # A supplied bindings object short-circuits the loader, exactly as it does in
    # `build_bridge` -- so the two assembly paths have the same seam.
    live_bindings = _mt5_bindings(config, mt5_bindings)
    account = MT5AccountProvider(
        live_bindings,
        terminal_path=config.mt5_terminal_path,
        position_reader=MT5PositionReader(data_path) if data_path is not None else None,
    )
    symbols = MT5SymbolSpecProvider(live_bindings)
    risk = RiskService(account, symbols)

    ledger = resolve_ledger(config, resolved)
    kill_switch, audit, workflow = _live_side(resolved, config, ledger)
    # **Both** binding sets reach the executor, as separate arguments: `resolved` is the
    # execution project's namespace (it translates the order), `live_bindings` is the
    # terminal (it answers whether the order's stop can be carried). Passing one in the
    # other's place is what left the One Click Trading guard inert -- see HANDOFF,
    # Phase 15 -- so `_finish` takes them by name.
    return _finish(
        config,
        risk,
        account,
        symbols,
        ledger,
        auto_trade_bindings=resolved,
        mt5_bindings=live_bindings,
        kill_switch=kill_switch,
        audit=audit,
        workflow=workflow,
    )


def _live_side(bindings: Any, config: BridgeConfig, ledger: Any) -> tuple[Any, Any, Any]:
    """The kill switch, the audit sink and the workflow, for an assembled live bridge.

    Every refusal here is about something that *should not be running*:

    * **no audit log** means a bridge that could place an order with no record of what
      it placed, so it refuses rather than continuing with a no-op sink
    * **the gate is built explicitly** rather than inherited, so a future upstream
      default change cannot turn live execution on without this code changing
    * **``demo_only=True`` is hard-coded.** This module has no parameter for it.
      A demo bridge that could be told to trade a live account is not a demo bridge.
    """
    from auto_trade.application.kill_switch import FileKillSwitch

    kill_switch = FileKillSwitch(config.log_directory / "KILL_SWITCH")

    try:
        from auto_trade.infrastructure.logging.audit import AuditLogger
    except ImportError as exc:
        raise CompositionRefusal(
            f"the execution project's audit log could not be imported: {exc}. Refusing to "
            f"assemble a bridge that could place an order with no record of what it placed."
        ) from exc
    audit = AuditLogger(config.log_directory).record

    from auto_trade.application.risk import RiskEngine
    from auto_trade.domain.models import ExecutionPolicy, RiskLimits
    from auto_trade.infrastructure.automation import MT5DesktopAdapter
    from auto_trade.infrastructure.automation.execution import ExecutionGate

    # The gate is built with `demo_only=True` and **no live execution enabled** until
    # the caller's own configuration is read. `ExecutionGate` defaults `enabled` to
    # False, and it is set here rather than inherited so a future default change
    # cannot turn it on silently.
    gate = ExecutionGate(
        enabled=bool(config.execution_enabled),
        dry_run=bool(config.dry_run),
        demo_only=True,
        kill_switch_active=kill_switch.active,
    )

    profile = config.terminal_profile(data_path=config.mt5_data_path)
    terminal_adapter = MT5DesktopAdapter(profile, gate=gate)
    workflow = bindings.ExecutionWorkflow(
        adapter=terminal_adapter,
        risk_engine=RiskEngine(
            RiskLimits(
                set(config.risk.allowed_symbols) or {"EURUSD"},
                _max_volume(config),
                5,
                10,
                config.risk.max_open_positions,
            )
        ),
        profile=profile,
        policy=ExecutionPolicy(
            dry_run=config.dry_run,
            demo_only=True,
            execution_enabled=bool(config.execution_enabled),
        ),
        kill_switch=kill_switch,
        audit=audit,
        ledger=ledger,
    )
    return kill_switch, audit, workflow


def _finish(
    config: BridgeConfig,
    risk: Any,
    account: Any,
    symbols: Any,
    ledger: Any,
    *,
    auto_trade_bindings: Any,
    mt5_bindings: Any,
    kill_switch: Any,
    audit: Any,
    workflow: Any,
) -> Bridge:
    """Wrap the live side and wire the envelope. Split out so the ordering is visible."""
    from signal_to_trade_bridge.adapters.auto_trade import AutoTradeExecutor
    from signal_to_trade_bridge.application.execution_envelope import build_execution_envelope
    from signal_to_trade_bridge.application.process_signal import ProcessSignal

    pipeline = ProcessSignal(risk, config)
    # The MT5 bindings are injected so the executor can read the terminal's trading
    # mode before it clicks anything. **This is load-bearing and it was missing for a
    # whole phase**: without them the executor has no way to know the terminal is in
    # One Click Trading mode, where an order's stop loss is not sent at all -- which
    # is why the first real order filled at the panel's default volume with no stop
    # while the dialog read back exactly what was requested.
    #
    # They are the same bindings the account and symbol providers were built from,
    # so the mode is read from the terminal the order would actually go through.
    executor = AutoTradeExecutor(
        workflow,
        bindings=auto_trade_bindings,
        mt5_bindings=mt5_bindings,
        kill_switch=kill_switch,
    )
    pipeline.wire_execution(
        build_execution_envelope(executor=executor, idempotency=ledger, kill_switch=kill_switch)
    )
    return Bridge(
        pipeline=pipeline,
        risk=risk,
        account=account,
        symbols=symbols,
        ledger=ledger,
        can_execute=True,
        bindings=auto_trade_bindings,
    )


def _max_volume(config: BridgeConfig) -> Any:
    from decimal import Decimal

    return Decimal("1.0")


def _build_from_executable(terminal: Path) -> int | None:
    """The build from the executable's version block, or ``None``.

    Decodes the resource section as UTF-16 and looks for the shape rather than
    calling into Win32, so the check runs anywhere. A false read here produces a
    **mismatch and a refusal**, never a false all-clear -- the failure direction is
    the safe one, which is the only acceptable direction for a safety check.
    """
    try:
        raw = terminal.read_bytes()
    except OSError:
        return None
    text = raw.decode("utf-16-le", errors="ignore")
    match = re.search(r"5\.0\.0\.(\d{3,5})", text)
    return int(match.group(1)) if match else None


def _build_from_snapshot(data_path: Path) -> int | None:
    """The build the running terminal reported, from the published position snapshot.

    This is the source that comes from the *process* rather than from a file on disk,
    which is what makes the two-source comparison worth making: a terminal updated
    while running would make them disagree, and that is a real state.
    """
    files = data_path / "MQL5" / "Files"
    for name in ("auto_trade_positions_a.json", "auto_trade_positions_b.json"):
        path = files / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = payload.get("terminal_build")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None
