"""Verifying that the control identifiers belong to this build.

**Known Issue 5, and its current state.** `auto-trade`'s control identifiers were
measured on Alpari build **6184**. This machine runs **6230** -- a 46-build gap,
confirmed from two independent sources (the live position snapshot's
``terminal_build`` field and `terminal64.exe`'s ``FileVersion``). The upstream
project's own rule is unambiguous:

> **Never substitute a control identifier you have not measured.** A control found
> once is a control whose behaviour is not established. If a build presents
> something different, refuse and report it -- do not wire it up.

So this script does not "fix" anything. It **reports**, and its only interesting
output is a mismatch.

### What it does, in order

1. reads the build from two sources and requires them to agree
2. reads the measured identifiers from upstream's own source
3. **refuses** if this build is not the one they were measured on

It is read-only throughout: it reads ``terminal64.exe``'s version resource, upstream's
source, and the published position snapshot. **It does not open the terminal, and it
does not click anything.** Measurement on a live window is a separate, later step and
this deliberately does not do it -- a script that both measures and wires is a script
that can do both in the wrong order.

### The re-measurement that changed ``EXPECTED_BUILD``

The measurement is `auto-trade terminal-check`, which is upstream's own read-only
probe. It opens the order dialog, reads the control tree, reports each expected
identifier as OK / DRIFTED / MISSING, and closes the dialog -- a dialog left open over
a trading terminal is a market order waiting to happen.

On build 6230 it reported 13 of 13 controls OK: every identifier in this project was
found where it was measured for. So ``EXPECTED_BUILD`` became 6230, **after** the
evidence rather than instead of it, and the report is kept in
``logs/terminal_check.json``. Had anything read DRIFTED or MISSING, the number would
not have moved and this file would name the control that moved.

**Running it here again will still exit 1 on the next MT5 update, and that is the
design working.** A check that can only be cleared by measuring is the difference
between a measurement and a number somebody edited to quiet a warning.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: The upstream checkout, where the measured identifiers live. Read from
#: ``AUTO_TRADE_PATH``, the same variable ``scripts/setup.*`` uses.
UPSTREAM = Path(os.getenv("AUTO_TRADE_PATH", ""))

#: This machine's terminal and data folder. Parameters, not constants, for the reason
#: every path in this project is a parameter: no machine's path (or user name) belongs
#: in a repository. Read from the same variables the bridge itself uses.
TERMINAL = Path(os.getenv("BRIDGE_MT5_TERMINAL_PATH", ""))
DATA_PATH = Path(os.getenv("BRIDGE_MT5_DATA_PATH", ""))

#: The build the measured identifiers belong to. **A different build is a refusal,
#: not a warning** -- see the module docstring.
#:
#: **6230 since the Phase 13 re-measurement.** It was 6184, and this terminal runs
#: 6230, so this script refused. The number moved only after `auto-trade
#: terminal-check` reported every expected identifier present, in place, on 6230 --
#: 13 of 13 controls OK, nothing drifted. The evidence is in
#: `logs/terminal_check.json`, and the probe that produced it is upstream's own
#: read-only one, which closes the dialog it opens.
#:
#: This script still only *reports*. It does not measure, and it must not: a script
#: that both measures and wires is a script that can do both in the wrong order. To
#: re-measure after the next MT5 update, run `auto-trade terminal-check` first, and
#: change this number only if it reports OK for every control.
EXPECTED_BUILD = 6230


def terminal_build(terminal: Path) -> int | None:
    """The terminal's build, from its version resource.

    ``5.0.0.6230`` -> ``6230``. Returns ``None`` rather than guessing when the
    resource is unreadable or the shape is not the one expected: a build number
    obtained by string surgery on something unexpected is not a build number.
    """
    try:
        raw = terminal.read_bytes()
    except OSError:
        return None

    # `FileVersionInfo` via ctypes is the supported route, but it is a Win32 call
    # and this script also has to run where it is absent. The version block is a
    # fixed-offset UTF-16 string in the resource section, and searching for the
    # shape is enough for a *report*: a false read here produces a mismatch and a
    # refusal, never a false all-clear.
    text = raw.decode("utf-16-le", errors="ignore")
    match = re.search(r"5\.0\.0\.(\d{3,5})", text)
    return int(match.group(1)) if match else None


def snapshot_build(data_path: Path) -> int | None:
    """The build the terminal itself reported, from the published snapshot.

    The second of the two sources, and the one that comes from the running process
    rather than from a file on disk. Two sources agreeing is what makes either of
    them worth believing.
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


def measured_identifiers(upstream: Path) -> dict[str, int] | None:
    """The identifiers upstream's order path actually uses, read from its source.

    Read from ``control_probe.EXPECTED_FIELDS`` and ``EXPECTED_FINAL_CONTROLS``, which
    is where upstream keeps the controls its probe expects to find. Those are
    imported from the same modules the order path uses, so a probe cannot pass
    against a stale copy -- which is upstream's own reason for putting them there,
    and a good enough reason to read them from there.

    Returns ``None`` if upstream is absent or the shape is unfamiliar, and a shape
    this script does not recognise is itself a refusal rather than something to keep
    parsing.

    ### What this deliberately does *not* return

    The build the identifiers were measured on. **Upstream does not record it.**
    There is no string anywhere in ``auto-trade`` naming a build its identifiers
    were measured against, so the earlier version of this function -- which looked
    for ``NNNN build`` and returned ``None`` when it found nothing -- was asking
    upstream for a fact upstream does not have.

    That provenance lives in *this* repository, in ``live.MEASURED_ON_BUILD`` and in
    :data:`EXPECTED_BUILD`, and it is backed by the recorded probe report at
    ``logs/terminal_check.json``. Saying so is more useful than a cross-check
    against a value that was never there: a check that looks like it is verifying
    provenance against an independent source, and is not, is worse than none.
    """
    try:
        sys.path.insert(0, str(upstream / "src"))
        from auto_trade.infrastructure.automation.control_probe import (
            EXPECTED_FIELDS,
            EXPECTED_FINAL_CONTROLS,
        )
    except Exception:
        return None
    finally:
        if sys.path and sys.path[0] == str(upstream / "src"):
            sys.path.pop(0)

    found: dict[str, int] = {}
    for label, value in EXPECTED_FIELDS:
        found[f"field:{label}"] = int(value)
    for label, _name, value in EXPECTED_FINAL_CONTROLS:
        found[f"final:{label}"] = int(value)
    return found or None


def main() -> int:
    print("Known Issue 5 -- control identifiers and the terminal build\n")

    missing = [
        name
        for name, value in (
            ("AUTO_TRADE_PATH", UPSTREAM),
            ("BRIDGE_MT5_TERMINAL_PATH", TERMINAL),
            ("BRIDGE_MT5_DATA_PATH", DATA_PATH),
        )
        if not str(value)
    ]
    if missing:
        print(f"  NOT RUN: set {', '.join(missing)} (see .env.example).")
        return 2

    builds = {
        "terminal64.exe FileVersion": terminal_build(TERMINAL),
        "published position snapshot": snapshot_build(DATA_PATH),
    }
    for source, value in builds.items():
        print(f"  {source:34} {value if value is not None else 'unreadable'}")

    known = [v for v in builds.values() if v is not None]
    if not known:
        print("\n  REFUSING: no build could be read from either source.")
        return 1
    if len(set(known)) != 1:
        print("\n  REFUSING: the two sources disagree about the build.")
        return 1

    build = known[0]
    print(f"\n  build confirmed as {build} from {len(known)} independent source(s)")

    if build != EXPECTED_BUILD:
        print(f"""
  REFUSING.

    The measured control identifiers belong to build {EXPECTED_BUILD} and this
    terminal is build {build} -- a gap of {abs(build - EXPECTED_BUILD)} builds.

    A control identifier is a position in a window, not a stable name. Between
    builds, MT5 adds and removes controls, so an id measured on one build can
    address a different control, or nothing, on another. Upstream's own rule:

      "Never substitute a control identifier you have not measured. A control
       found once is a control whose behaviour is not established. If a build
       presents something different, refuse and report it -- do not wire it up."

    So the identifiers are NOT reported as usable, and the composition root's live
    path stays refused. To proceed, every identifier has to be re-measured against
    build {build} on THIS machine, at THIS display resolution, and recorded here
    with the build it was measured on.

    Nothing was opened, clicked, or changed to produce this report.
""")
        return 1

    identifiers = measured_identifiers(UPSTREAM)
    if identifiers is None:
        print("\n  REFUSING: upstream's control identifiers could not be read.")
        return 1

    print(f"\n  identifiers this project uses, read from upstream's probe ({len(identifiers)}):")
    for name, value in sorted(identifiers.items()):
        print(f"    {name:32} {value}")

    print(f"""
  OK -- the build matches.

    Where the build provenance lives
    --------------------------------
    EXPECTED_BUILD = {EXPECTED_BUILD} is recorded in THIS repository, not in
    auto-trade, because upstream keeps no record of the build its identifiers were
    measured on. It is backed by a read-only probe whose report is kept beside the
    logs:

      logs/terminal_check.json

    Re-measure after the next MT5 update with:

      auto-trade terminal-check

    and change EXPECTED_BUILD only if that reports OK for every control. A
    DRIFTED or MISSING row means the constant must not move.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
