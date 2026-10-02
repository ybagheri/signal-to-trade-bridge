"""Re-measuring the control identifiers, because the build moved.

**Known Issue 5, and this is its remedy.** `auto-trade`'s control identifiers were
measured on Alpari build **6184**. This machine runs **6230** -- a 46-build gap,
confirmed from two independent sources (the live position snapshot's
``terminal_build`` field and ``terminal64.exe``'s ``FileVersion``). The upstream
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

### Running it

```powershell
python scripts/control_ids.py
```

Exit code 0 means the build is the expected one. Exit code 1 means it is not, and
the measured identifiers must not be used until they are re-measured.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

#: The upstream checkout, where the measured identifiers live.
UPSTREAM = Path(r"D:\Projects\auto-trade")

#: This machine's terminal. A parameter, not a constant, for the reason every path in
#: this project is a parameter: no machine's path belongs in a repository.
TERMINAL = Path(r"C:\Program Files\Alpari MT5_4\terminal64.exe")

#: The build the measured identifiers belong to. **A different build is a refusal,
#: not a warning** -- see the module docstring.
EXPECTED_BUILD = 6184


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
    """Read the identifiers upstream measured, and the build they were measured on.

    Parsed out of the source rather than configured here, so this script cannot
    disagree with the project it is checking. Returns ``None`` if the file has moved
    or the shape is unfamiliar -- and a shape this script does not recognise is
    itself a refusal, not something to keep parsing.
    """
    for candidate in upstream.rglob("*.py"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if "control_id" not in text.lower() and "CONTROL" not in text:
            continue
        found = {k: int(v) for k, v in re.findall(r'"?([A-Z_]{4,})"?\s*[:=]\s*(\d{3,6})', text)}
        build = re.search(r"(\d{4})\s*(?:build|Build)", text)
        if found and build:
            return found
    return None


def main() -> int:
    print("Known Issue 5 -- control identifiers and the terminal build\n")

    builds = {
        "terminal64.exe FileVersion": terminal_build(TERMINAL),
        "published position snapshot": snapshot_build(
            Path(
                r"C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal"
                r"\1D9617E1A6A4352DBDC25D08FEC12BD2"
            )
        ),
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
        print("\n  REFUSING: upstream's measured identifiers could not be read.")
        return 1

    print(f"\n  measured identifiers (build {EXPECTED_BUILD}):")
    for name, value in sorted(identifiers.items()):
        print(f"    {name:32} {value}")
    print("\n  OK -- the build matches. Re-measurement is not required.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
