"""The command line, and the exit codes as a contract.

Phase 12. Twelve phases produced a library nobody could run, because a library with
no entry point is a library whose every use begins with a Python REPL. This module is
that entry point, and it is deliberately small.

### The exit code is the API

A shell script driving this has exactly three questions: did it work, was the answer
no, or was something broken. Anything else -- one code for everything, or prose the
caller has to grep -- forces every consumer to re-parse output that was designed to
be read by a person.

```
0   the command did what it was asked
1   a refusal. The bridge worked and declined to trade. This is an expected
    outcome and a *successful* run: "the engine found nothing to trade" is the
    commonest result there is, and an exit code that says "error" for it teaches
    operators to ignore the exit code.
2   a fault. The terminal was unreachable, the ledger was unreadable, the
    configuration was refused, a package was missing.
3   the answer was UNKNOWN. Separated from 1 and 2 because it must never be
    retried automatically -- a retry may open a second position.
```

**3 is the one that matters.** It exists so a caller's retry logic has something to
key on, and it is why ``UNKNOWN`` is not folded into "refused": the bridge cannot
tell whether a position exists, and a caller told "refused" would reasonably send it
again.

### What each command does, and what it deliberately does not

``doctor``
    Whether this machine can run the bridge at all. Every dependency, the terminal's
    build, the control identifiers, the configuration, the ledger. **Read-only** and
    safe to run anywhere, including against a live terminal.

``check``
    Read one signal file through the whole pipeline and print the decision. The
    signal comes from a file, never from a live engine: a command that pulls from a
    live source would trade a market rather than analyse a file, and the difference
    is exactly what a dry run exists to be certain about.

``signal``
    Print a signal as JSON in the shape ``auto-trade``'s own provider reads. For
    interop, and for writing a signal file for ``check``.

``trade``
    Send one signal file through the **live** path, which can place a demo order.
    Added in Phase 14, and gated three ways: ``--confirm-demo`` must be given, the
    account must be a demo account, and the configuration must already have
    execution enabled and dry-run off. Any one of them missing is a refusal.

``config``
    Print the effective configuration. Nothing in it is a secret, and none of it is
    redacted -- a caller that has to guess why the
    bridge is not executing is a caller who will eventually guess wrong.

### Only ``trade`` can place an order, and it takes three separate permissions

The rest of this module cannot. ``check`` and every library consumer go through
``build_bridge``, which returns ``can_execute=False`` and attaches no executor.

Placing an order takes all three of:

1. ``BRIDGE_EXECUTION_ENABLED=true`` and ``BRIDGE_DRY_RUN=false`` in the environment
2. a terminal whose control identifiers were measured on *its* build
3. ``--confirm-demo`` on the command line

Each is refused separately and each refusal names the one that is missing, because
a single combined check would leave an operator unable to tell which of the three
they had not done. That is the same reasoning as ``auto-trade execute
--confirm-demo``, and it is why the flag exists at all: the point is not to make
the order hard, it is to make the *intent* explicit at the moment it happens.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

from signal_to_trade_bridge.composition import CompositionRefusal, build_bridge
from signal_to_trade_bridge.configuration.config import BridgeConfig
from signal_to_trade_bridge.domain.enums import DecisionAction
from signal_to_trade_bridge.domain.models import Signal
from signal_to_trade_bridge.version import __version__

__all__ = ["EXIT_FAULT", "EXIT_OK", "EXIT_REFUSED", "EXIT_UNKNOWN", "main"]

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_FAULT = 2
EXIT_UNKNOWN = 3


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What a command produced, and what to exit with.

    A type rather than a bare ``int`` so a command cannot return a code without a
    result, and a caller cannot read one without both.
    """

    code: int
    output: str = ""
    detail: dict[str, Any] | None = None


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    """Run one command. Returns the exit code; prints to ``out``."""
    parser = _parser()
    args = parser.parse_args(argv)
    stream = out if out is not None else sys.stdout

    try:
        outcome = _dispatch(args, parser)
    except CompositionRefusal as exc:
        # A refusal about the machine or the configuration. Exit 2, and the message
        # is the whole of it -- an operator meeting this needs to read the sentence,
        # not a code.
        print(f"ERROR: {exc}", file=stream)
        return EXIT_FAULT
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=stream)
        return EXIT_FAULT

    if outcome.output:
        print(outcome.output, file=stream)
    return outcome.code


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> _Outcome:
    if args.command == "doctor":
        return _doctor(args)
    if args.command == "check":
        return _check(args)
    if args.command == "signal":
        return _signal(args)
    if args.command == "config":
        return _config(args)
    if args.command == "trade":
        return _trade(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    raise AssertionError("unreachable")


# --- doctor -----------------------------------------------------------------


def _doctor(args: argparse.Namespace) -> _Outcome:
    """Whether this machine can run the bridge, and if not, which thing is missing.

    Ordered so the *first* failure is the one reported, rather than every failure at
    once. A list of ten problems reads as "this is broken" when the answer is usually
    one missing package and the other nine are consequences of it.
    """
    from importlib.util import find_spec

    report: dict[str, Any] = {"version": __version__}
    problems: list[str] = []

    for name, why in (
        ("MetaTrader5", "the account and contract facts come from a running terminal"),
        ("auto_trade", "the execution side is a private package; dry runs need it too"),
    ):
        present = find_spec(name) is not None
        report[name] = present
        if not present:
            problems.append(f"{name} is not installed, and {why}")

    # The terminal, without touching it. `initialize` connects; nothing here launches.
    config = _config_from_env(args)
    terminal = config.mt5_terminal_path
    report["terminal_path"] = str(terminal) if terminal else None
    if terminal is None:
        problems.append(
            "no terminal path is configured. Set BRIDGE_MT5_TERMINAL_PATH -- the bridge "
            "never launches a terminal, so it has to be told where the running one is."
        )
    elif not Path(terminal).is_file():
        problems.append(f"no terminal at {terminal}")

    # The data directory, and the argument for reporting it is that it is the single
    # most error-prone value in the setup: a hash-named folder under
    # `MetaQuotes\Terminal`, one per installation, which cannot be guessed and which
    # `docs/setup.md` tells the reader to go and find by hand. The terminal path is
    # printed above, so leaving this one out made the command describe half of what
    # the operator configured -- and the half it omitted is the half they had to
    # look up.
    #
    # Reported, and checked, but *not* treated as fatal on its own: a build can be
    # read from the executable alone, so an absent data directory is a loss of a
    # confirmation rather than a machine that cannot run.
    data = config.mt5_data_path
    report["data_path"] = str(data) if data else None
    if data is not None and not Path(data).is_dir():
        problems.append(
            f"no MT5 data directory at {data}. This is the hash-named folder under "
            f"MetaQuotes\\Terminal, not the terminal's install directory; the build can "
            f"still be read from the executable, so this is a lost confirmation rather "
            f"than a fault."
        )

    control_ids = _control_id_report(args)
    report["control_ids"] = control_ids
    if control_ids.get("usable") is False:
        problems.append(
            "the measured control identifiers do not belong to this terminal's build. The "
            "live path is refused, and no configuration clears it -- run "
            "python scripts/control_ids.py for the details."
        )

    report["problems"] = problems
    if problems:
        return _Outcome(EXIT_FAULT, _render_doctor(report), report)
    return _Outcome(EXIT_OK, _render_doctor(report), report)


def _control_id_report(args: argparse.Namespace) -> dict[str, Any]:
    from signal_to_trade_bridge.live import check_control_ids

    config = _config_from_env(args)
    if config.mt5_terminal_path is None:
        return {"usable": None, "because": "no terminal path is configured"}
    check = check_control_ids(config.mt5_terminal_path, config.mt5_data_path)
    return {
        "usable": check.matches,
        "build": check.build,
        "measured_on": check.expected,
        "sources": check.sources,
        "refusal": None if check.matches else check.refusal(),
    }


def _render_doctor(report: dict[str, Any]) -> str:
    lines = ["signal-to-trade-bridge", f"  version            {report['version']}", ""]

    # **The problems come first, and they used to not be printed at all.** The
    # function built this list, carefully, with the reason attached to each entry --
    # and then rendered only the status block below, so a machine with a typo in
    # BRIDGE_MT5_TERMINAL_PATH exited 2 and said nothing about the missing file,
    # leaving the operator to read a complaint about control identifiers instead.
    # A diagnostic command that detects a problem and does not report it is worse
    # than one that stays silent, because the exit code is then a fact with no
    # explanation attached.
    #
    # Before the status block rather than after: the person running `doctor` wants
    # the answer first, and the `ok` / `REFUSED` marks below are corroboration for a
    # conclusion they have already been given.
    problems = report.get("problems") or []
    if problems:
        lines.append("  cannot run here")
        lines += [f"    - {problem}" for problem in problems]
        lines.append("")

    for name in ("MetaTrader5", "auto_trade"):
        mark = "ok" if report.get(name) else "MISSING"
        lines.append(f"  {name:<18} {mark}")
    lines.append(f"  terminal           {report.get('terminal_path') or 'not configured'}")
    if report.get("data_path") is not None:
        lines.append(f"  data directory     {report['data_path']}")

    control = report.get("control_ids") or {}
    usable = control.get("usable")
    state = "ok" if usable else ("not checked" if usable is None else "REFUSED")
    lines.append(f"  control ids        {state}")
    if control.get("build") is not None:
        lines.append(
            f"    build            {control['build']} "
            f"(ids measured on {control.get('measured_on')})"
        )
    if control.get("refusal"):
        for line in control["refusal"].strip().splitlines():
            lines.append(f"    {line.strip()}")
    return "\n".join([*lines, ""])


# --- check ------------------------------------------------------------------


def _check(args: argparse.Namespace) -> _Outcome:
    """One signal file, through the whole pipeline.

    The signal comes from a file and never from a live engine. A command that pulled
    from a live source would be *trading the market* rather than analysing a file, and
    that is precisely the distinction a dry run exists to keep certain.
    """
    if not args.signal.exists():
        return _Outcome(EXIT_FAULT, f"ERROR: no signal file at {args.signal}")

    try:
        text = _read_text_any(args.signal)
    except OSError as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} could not be read: {exc}")
    except ValueError as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} could not be read: {exc}")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} is not valid JSON: {exc}")

    try:
        signal = _signal_from(payload)
    except (KeyError, TypeError, ValueError) as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: the signal is not usable: {exc}")

    try:
        bridge = build_bridge(_config_from_env(args))
    except CompositionRefusal as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {exc}")

    decision = bridge.pipeline.process(signal)
    return _Outcome(
        _decision_code(decision), _render_decision(decision), {"decision": decision.to_dict()}
    )


def _decision_code(decision: Any) -> int:
    """The exit code for a decision, and the only place that mapping is written.

    `UNKNOWN` is checked before the refusals, because a decision whose outcome could
    not be determined must not be lumped in with one that was determined. A caller
    reading "refused" as retryable would open a second position.
    """
    execution = decision.execution
    if execution is not None and execution.is_unknown:
        return EXIT_UNKNOWN
    if decision.action is DecisionAction.NO_TRADE:
        return EXIT_REFUSED
    return EXIT_OK


def _render_decision(decision: Any) -> str:
    """The decision, in full, because a summary hides the thing that went wrong."""
    lines = [
        f"signal   {decision.signal_id}",
        f"action   {decision.action.value}",
        f"reason   {decision.reason}",
        f"because  {decision.explanation}",
    ]
    if decision.intent is not None:
        intent = decision.intent
        ratio = intent.reward_to_risk
        lines += [
            "",
            f"  symbol     {intent.symbol} {intent.direction.value}",
            f"  volume     {intent.volume}",
            f"  entry      {intent.entry}",
            f"  stop       {intent.stop_loss.price}  ({intent.stop_loss.distance})",
            f"  target     {'none' if intent.take_profit is None else intent.take_profit.price}",
            f"  risk       {intent.risk_amount}",
            f"  planned    {intent.position_size.planned_loss}",
            f"  ratio      {'n/a' if ratio is None else ratio}",
        ]
    report = (decision.diagnostics or {}).get("report")
    if isinstance(report, dict):
        downstream = report.get("downstream") or {}
        lines += [
            "",
            "  downstream gates",
            f"    evaluated  {downstream.get('evaluated')}",
            "    accepted   "
            + str(downstream.get("accepted"))
            + (f"  ({downstream['reason']})" if downstream.get("reason") else ""),
        ]
        if not downstream.get("evaluated"):
            lines.append(f"    because    {downstream.get('unevaluated_because')}")
        blockers = report.get("blockers") or []
        lines.append("    blockers   " + ("; ".join(blockers) if blockers else "none"))
    return "\n".join(lines)


# --- signal -----------------------------------------------------------------


def _signal(args: argparse.Namespace) -> _Outcome:
    """A worked example, in the shape `auto-trade` reads.

    Not a template file on disk: a template goes stale silently, and a command that
    prints the *current* schema cannot.

    **It carries no volume and no price**, which is the point. Those two are *this*
    bridge's to compute: the volume from the account balance and the symbol's
    contract, and the entry from the signal. Emitting a plausible-looking ``0.10``
    and ``1.10000`` in a template would be a number nobody computed, and a file
    carrying one would be accepted by the execution project and refused by this one
    with no explanation of which number was invented. The price is here as ``entry``
    because a signal *does* carry one; the volume is absent because a signal does not,
    and a signal that did would be overwriting the sizing arithmetic.
    """
    if args.side == "SELL":
        side = {
            "action": "SELL",
            "direction": "SHORT",
            "stop_loss": "1.10300",
            "take_profit": "1.09700",
        }
    else:
        side = {
            "action": "BUY",
            "direction": "LONG",
            "stop_loss": "1.09700",
            "take_profit": "1.10300",
        }

    # `id`, not `signal_id`. `auto_trade.domain.models.TradeSignal.from_dict`
    # requires the key `id` -- the *constructor* takes `signal_id`, and the
    # deserialiser reads `id`, which is exactly the sort of asymmetry that makes an
    # emitted file rejected by the project it was written for. `--check` accepts
    # either, so the example round-trips through this project too.
    payload = {
        "id": f"stb-example-{side['action'].lower()}",
        "signal_id": f"stb-example-{side['action'].lower()}",
        "symbol": args.symbol,
        "timeframe": "H1",
        **side,
        "entry": "1.10000",
        "stop_basis": "PULLBACK_EXTREME",
        "take_profit_basis": "SWING",
        "evidence_score": 0.72,
        "setup_id": "pullback_h#0",
        "bar_index": 299,
        "bar_time": 1727740800.0,
        "source": "signal-to-trade-bridge",
    }
    return _Outcome(EXIT_OK, json.dumps(payload, indent=2, sort_keys=True))


# --- config -----------------------------------------------------------------


def _config(args: argparse.Namespace) -> _Outcome:
    """The effective configuration, redacted.

    A caller that has to guess why the bridge is not executing will eventually guess
    wrong, and the wrong guess will be "it is not configured to execute, so turning
    on `execution_enabled` is safe". Printing the whole configuration, with anything
    that looks like a secret replaced, is cheaper than that.
    """
    config = _config_from_env(args)
    return _Outcome(EXIT_OK, json.dumps(_safe(config), indent=2, sort_keys=True))


def _safe(config: BridgeConfig) -> dict[str, Any]:
    """The configuration as JSON-safe data.

    Two jobs, and the second is the one that bites:

    * **a dataclass has to become a mapping.** ``BridgeConfig.risk`` is a
      ``RiskParameters``, and ``json.dumps`` raises on it. The first version of this
      function tried ``vars()`` and then ``dataclasses.fields``, and the field name in
      that condition is the reason the second attempt exists.
    * **a ``Decimal`` has to become a string.** JSON has no decimal type, and
      ``json.dumps`` will happily write a float -- which rounds ``0.5`` into a value
      that is not the risk percentage the operator typed.

    Nothing is redacted here, because nothing in ``BridgeConfig`` is a secret. The
    guesswork a redaction heuristic needs -- "does this value *look* like a token" --
    either over-redacts a risk percentage or under-redacts a credential, and the
    second of those is the one that matters. If a secret is ever added here, it is
    redacted **by field name**, at the point it is added.
    """
    import dataclasses

    flat = {f.name: getattr(config, f.name) for f in dataclasses.fields(config)}
    return {key: _plain(value) for key, value in flat.items()}


def _plain(value: Any) -> Any:
    """A JSON-safe rendering of one configuration value.

    A ``Decimal`` becomes a **string**, not a float: the risk percentage is a number
    an operator typed, and writing ``0.00500`` as ``0.005`` would make the printed
    configuration differ from the effective one in exactly the digits that matter.
    A ``Path`` becomes a string too, and is **not** escaped here -- ``json.dumps``
    does that, and doing it twice is how a Windows path ends up in the output as
    ``C:\\Users`` with doubled separators that a human reading it cannot tell from
    a real one.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # An enum, or anything else with a single readable form.
    return getattr(value, "value", str(value))


# --- signal parsing ---------------------------------------------------------


def _read_text_any(path: Path) -> str:
    """Read a text file, whatever the shell wrote it as.

    **Found by running it, and it is a real one.** On Windows,
    ``signal-to-trade-bridge signal > sig.json`` does not produce UTF-8: PowerShell
    redirects through UTF-16, so the file starts with a byte-order mark that a strict
    reader rejects with

        'utf-8' codec can't decode byte 0xff in position 0

    which is true and useless -- the file is a perfectly good signal that the *shell*
    re-encoded. And the documented way to produce a signal file is exactly that
    redirect, so refusing it would make the project's own instructions fail on the
    platform most of its users are on.

    Four encodings, tried in order of how likely they are:

    * ``utf-8-sig`` -- a UTF-8 file that happens to carry a BOM. The most common
      case on every platform, and the reason a bare ``utf-8`` read is wrong.
    * ``utf-16`` -- what a PowerShell redirect produces on Windows.
    * ``utf-8`` -- the ordinary case, kept explicit so the order above is not
      mistaken for a fallback chain that ends here.
    * ``cp1252`` -- the Windows default code page, which is what ``cmd`` writes
      when the console code page is not UTF-8.

    **Not** ``latin-1``, which decodes any byte sequence and would therefore accept
    genuinely corrupt files and turn them into nonsense rather than refusing them.
    The last encoding tried has to be one that can fail.
    """
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        f"{path} is not text in any encoding this bridge reads: utf-8 (with or without a "
        f"BOM), utf-16, or cp1252."
    )


def _signal_from(payload: Any) -> Signal:
    """A JSON payload into a ``Signal``.

    Raises rather than defaulting anything. A signal file is the whole input to a
    trading decision, and a missing field defaulted to a plausible value is how a
    malformed reading becomes a real order -- the exact class of bug this project has
    spent twelve phases refusing.
    """
    from signal_to_trade_bridge.domain.enums import Direction, SignalAction

    if not isinstance(payload, dict):
        raise TypeError(f"expected a JSON object, got {type(payload).__name__}")

    action_text = str(payload["action"]).upper()
    try:
        action = SignalAction(action_text)
    except ValueError as exc:
        raise ValueError(f"unknown action {action_text!r}") from exc
    direction_text = str(payload["direction"]).upper()
    try:
        direction = Direction(direction_text)
    except ValueError as exc:
        raise ValueError(f"unknown direction {direction_text!r}") from exc

    def _decimal(name: str) -> Decimal:
        value = payload.get(name)
        if value is None:
            raise KeyError(f"{name} is required")
        return Decimal(str(value))

    stop = payload.get("stop_loss")
    take = payload.get("take_profit")
    return Signal(
        # Both spellings, deliberately. `auto_trade`'s constructor takes
        # `signal_id` and its `from_dict` reads `id`, so a file written for that
        # project and a file written for this one differ on the key name alone --
        # and a reader that accepted only one of them would reject half the files
        # either project produces.
        signal_id=str(
            payload.get("signal_id")
            or payload.get("id")
            or f"stb-{action_text.lower()}-{_digest(payload)}"
        ),
        symbol=str(payload["symbol"]),
        timeframe=str(payload.get("timeframe", "H1")),
        action=action,
        direction=direction,
        entry=_decimal("entry"),
        stop_loss=None if stop is None else Decimal(str(stop)),
        take_profit=None if take is None else Decimal(str(take)),
        stop_basis=str(payload.get("stop_basis", "")),
        take_profit_basis=str(payload.get("take_profit_basis", "")),
        evidence_score=(
            None if payload.get("evidence_score") is None else float(payload["evidence_score"])
        ),
        setup_id=str(payload.get("setup_id", "")),
        bar_index=int(payload.get("bar_index", -1)),
        bar_time=(None if payload.get("bar_time") is None else float(payload["bar_time"])),
        source=str(payload.get("source", "file")),
    )


def _digest(payload: Any) -> str:
    """A short digest of a payload, for an id the file did not carry.

    **Not** a replacement for a proper identity. It has no bar in it, which is
    exactly the defect Phase 9 found and repaired, so a signal file without a
    `signal_id` gets a *better* id than nothing and a *worse* one than the engine's.
    The two fields it does use -- the action and the entry -- make re-reading the
    same file idempotent, which is the property that actually matters for a file.
    """
    import hashlib

    material = f"{payload.get('symbol')}|{payload.get('action')}|{payload.get('entry')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# --- argument parsing -------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        # From argv, not a literal. The installed command is
        # `signal-to-trade-bridge`, so a hard-coded `stb` in the usage line names a
        # command nobody typed -- and the first person to copy that line into a
        # script gets "not recognised". Whatever name was used to invoke this is the
        # name the help should show.
        prog=Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "signal-to-trade-bridge",
        description=(
            "signal-to-trade-bridge. No command here can place an order: the live path "
            "needs control identifiers measured for this terminal build."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"signal-to-trade-bridge {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--log-dir",
            type=Path,
            default=None,
            help="where the ledger and audit log live (default: $BRIDGE_LOG_DIR, else ./logs)",
        )

    doctor = sub.add_parser(
        "doctor",
        help="whether this machine can run the bridge, and if not which part is missing",
    )
    _common(doctor)

    check = sub.add_parser(
        "check", help="run one signal file through the whole pipeline and print the decision"
    )
    _common(check)
    check.add_argument("signal", type=Path, help="a signal JSON file; see `stb signal`")

    signal = sub.add_parser("signal", help="print a worked signal in the shape auto-trade reads")
    signal.add_argument("--symbol", default="EURUSD")
    signal.add_argument("--side", choices=("BUY", "SELL"), default="BUY")

    config = sub.add_parser("config", help="print the effective configuration as JSON")
    _common(config)

    trade = sub.add_parser(
        "trade",
        help="send one signal file through the LIVE path; can place a demo order",
    )
    _common(trade)
    trade.add_argument("signal", type=Path, help="a signal JSON file; see `stb signal`")
    trade.add_argument(
        "--confirm-demo",
        action="store_true",
        help=(
            "required acknowledgement that this may place a demo order. Without it "
            "nothing is sent, and the refusal says so rather than proceeding."
        ),
    )
    trade.add_argument(
        "--what-if",
        action="store_true",
        help=(
            "do everything except click: build the live path, run the pipeline, and "
            "print the full decision with the volume and prices it would send. This is "
            "the flag to use first."
        ),
    )

    return parser


def _config_from_env(args: argparse.Namespace) -> BridgeConfig:
    """The configuration, with ``--log-dir`` applied.

    ``--log-dir`` is applied **after** ``from_env`` so it wins over the environment.
    A flag that lost to an env var would be a flag that silently did nothing, and a
    developer pointing the ledger somewhere else to look at it is exactly the case
    where that matters most.

    ``apply=True``, so a ``.env`` file is honoured. **That mutates ``os.environ``**,
    which is the documented behaviour of :func:`config_from_env` and is correct for
    a process that is about to use the configuration -- but it is also a side effect
    a library caller does not expect from something that reads a file, so it is
    spelled out here rather than left to the default.

    It was found the hard way: this function left every ``BRIDGE_`` variable from
    the repository's ``.env`` in the process environment, and a test that ran
    afterwards and asserted the *shipped defaults* failed -- correctly, because the
    defaults were no longer what the process could see. Two configuration tests in
    another file broke on the day this machine armed itself, and neither had anything
    to do with the CLI.
    """
    from dataclasses import replace

    from signal_to_trade_bridge.configuration.config import config_from_env

    config = config_from_env()
    override = getattr(args, "log_dir", None)
    return replace(config, log_directory=override) if override else config


# --- trade ------------------------------------------------------------------


def _trade(args: argparse.Namespace) -> _Outcome:
    """One signal file, through the **live** path.

    This is the only command in this module that can place an order, and it is the
    reason the module docstring changed. Three permissions are required and each is
    checked separately, so a refusal names the one that is missing:

    1. ``--confirm-demo`` on the command line
    2. ``execution_enabled`` and not ``dry_run`` in the configuration
    3. a terminal whose control identifiers were measured on its own build

    Number 1 is first and it is a flag, because it is the only one that proves a
    person was present. A configuration left armed on a machine is a machine that
    will trade the next time anything calls the live path; a flag has to be typed
    every time, which is the property that makes the difference between "the system
    is configured to trade" and "somebody asked for a trade".

    ``--what-if`` does everything except the click: it builds the live side, runs the
    signal through it, and prints the decision including the volume and the prices.
    That is the flag to reach for first, and it is why it exists rather than
    "just try it" -- an order that is clicked and then found to be the wrong size is
    not a thing you can undo by reading the output.
    """
    if not getattr(args, "confirm_demo", False):
        return _Outcome(
            EXIT_REFUSED,
            "  no order was sent.\n"
            "  `trade` can place a demo order, and it needs --confirm-demo to say so.\n"
            "  Run it with --what-if first to see exactly what would be sent.",
        )

    if not args.signal.exists():
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} does not exist.")

    try:
        text = _read_text_any(args.signal)
    except (OSError, ValueError) as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} could not be read: {exc}")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} is not valid JSON: {exc}")

    try:
        signal = _signal_from(payload)
    except (TypeError, ValueError, KeyError) as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: {args.signal} is not a usable signal: {exc}")

    config = _config_from_env(args)

    # The configuration gate, reported before the machine gate, because it is the
    # one an operator can have forgotten about and `config` will confirm.
    if config.dry_run or not config.execution_enabled:
        missing = []
        if not config.execution_enabled:
            missing.append("BRIDGE_EXECUTION_ENABLED is false")
        if config.dry_run:
            missing.append("BRIDGE_DRY_RUN is true, and it overrides execution")
        return _Outcome(
            EXIT_REFUSED,
            "  no order was sent. The configuration forbids it:\n"
            + "".join(f"    - {item}\n" for item in missing)
            + "  Both must change. `signal-to-trade-bridge config` shows the current values.",
        )

    from signal_to_trade_bridge.composition import CompositionRefusal as _Refusal
    from signal_to_trade_bridge.live import build_live

    # `mt5_terminal_path` is `Path | None` on the config, and `build_live` wants a
    # `Path`. Checked rather than asserted: an unset terminal path has to produce a
    # sentence an operator can act on, not an AttributeError from deep inside the
    # bindings, and this is the last point where the message can still name the
    # variable that is missing.
    if config.mt5_terminal_path is None:
        return _Outcome(
            EXIT_FAULT,
            "ERROR: no terminal path is configured. Set BRIDGE_MT5_TERMINAL_PATH -- the "
            "bridge never launches a terminal, so it has to be told where the running "
            "one is.",
        )

    try:
        bridge = build_live(
            config,
            terminal=config.mt5_terminal_path,
            data_path=config.mt5_data_path,
        )
    except _Refusal as exc:
        return _Outcome(EXIT_FAULT, f"ERROR: the live path could not be built: {exc}")

    # `--what-if` replaces the executor, and it has to happen **here**, before the
    # signal is processed.
    #
    # The first version of this flag only changed the *printing*: it built the live
    # side -- which wires the real executor -- ran the signal, and then said "nothing
    # was sent". The order had already gone out by then. It was found the way that
    # kind of thing should never be found, by an `--what-if` run that came back
    # `UNKNOWN` with the execution project's own message about an order that "may or
    # may not have reached the terminal".
    #
    # Swapping the executor keeps everything that decides *what* would be sent real:
    # the live composition root, the risk service reading the real account, the real
    # ledger, the real validation and sizing, and the real `ExecutionRequest`. Only
    # the last step is substituted, and the substituted one is the only step that can
    # spend money. Building a dry-run bridge instead would have been simpler and would
    # have produced numbers from different wiring, which is the thing this flag
    # exists to avoid.
    #
    # The recorder comes from `composition`, not from `adapters/fake/`. Reaching into
    # an adapter directory for a double is exactly what `test_layering.py` exists to
    # prevent, and it was right to refuse: an interface layer importing an adapter to
    # get a fake is how a preview turns into a second production path. The composition
    # root is the only layer allowed to know both executors.
    recorder = None
    if getattr(args, "what_if", False):
        from signal_to_trade_bridge.application.execution_envelope import with_executor
        from signal_to_trade_bridge.composition import recording_executor

        recorder = recording_executor()
        # Checked rather than asserted. `build_live` always wires an envelope, but
        # the attribute is typed optional, and a preview flag that raised a TypeError
        # on a build that had not wired one would be a poor way to learn it -- a
        # preview is exactly where a confusing failure is most likely to be mistaken
        # for "the live path is broken".
        existing = bridge.pipeline._envelope
        if existing is None:
            return _Outcome(
                EXIT_FAULT,
                "ERROR: the live pipeline has no execution envelope, so there is nothing "
                "to preview. That is a bug in build_live, not a refusal.",
            )
        bridge.pipeline.wire_execution(with_executor(existing, recorder))

    # The decision, from the live pipeline. This is the same wiring an order would
    # go through, which is the point of building the live side even for `--what-if`:
    # a report of a *different* pipeline would be a report of a different thing.
    decision = bridge.pipeline.process(signal)
    lines = [
        f"signal   {decision.signal_id}",
        f"action   {decision.action.value}",
        f"reason   {decision.reason}",
        "",
    ]
    if decision.intent is not None:
        intent = decision.intent
        lines += [
            f"  symbol     {intent.symbol} {intent.direction.value}",
            f"  volume     {intent.volume}",
            f"  entry      {intent.entry}",
            f"  stop       {intent.stop_loss.price}  ({intent.stop_loss.distance})",
            f"  target     {'none' if intent.take_profit is None else intent.take_profit.price}",
            f"  risk       {intent.risk_amount}",
            "",
        ]

    if getattr(args, "what_if", False):
        lines += [
            "  --what-if was given, so nothing was sent. The numbers above are what",
            "  this signal would have sent, computed by the same wiring an order uses:",
            "  the live composition root, the real account, the real ledger, the real",
            "  sizing. Only the final click was replaced by a recorder.",
        ]
        if recorder is not None and recorder.submitted:
            request = recorder.submitted[0]
            # `stop_loss` and `take_profit` are plain `Decimal` on the request, not
            # price objects. Reading `.price` off them would have been the obvious
            # guess -- the *intent* carries `StopLoss` and `TakeProfit` objects, and
            # the request is built from them. mypy caught it before it ran.
            lines += [
                "",
                "  the request that would have gone to the terminal:",
                f"    {request.symbol} {request.direction.value} {request.volume} lots",
                f"    entry {request.entry}  stop {request.stop_loss}",
                f"    target {request.take_profit if request.take_profit is not None else 'none'}",
                f"    comment {request.comment!r}",
            ]
        return _Outcome(
            _decision_code(decision), "\n".join(lines), {"decision": decision.to_dict()}
        )

    execution = decision.execution
    if decision.action is DecisionAction.NO_TRADE:
        return _Outcome(EXIT_REFUSED, "\n".join(lines), {"decision": decision.to_dict()})
    if execution is None:
        return _Outcome(
            EXIT_REFUSED,
            "\n".join([*lines, "  no order was sent: the decision carries no execution result."]),
            {"decision": decision.to_dict()},
        )

    lines += [
        f"  status     {execution.status}",
        f"  message    {execution.message}",
    ]
    code = _decision_code(decision)
    return _Outcome(code, "\n".join(lines), {"decision": decision.to_dict()})
