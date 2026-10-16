> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

### What Phase 11 Found — and what it could not close

```
live.py               BuildMismatch, ControlIdCheck, check_control_ids(), build_live()
scripts/control_ids.py  the same check as a runnable report
tests/live/
  test_mt5_live.py    21 tests, opt-in; 13 read the terminal, 8 need none
tests/unit/
  test_live_assembly.py  12 tests, the assembly with a matching build
```

**Phase 11's headline is a refusal, and the refusal is correct.**

### Known Issue 5 — closed, by measurement

**Update: the task is done.** The lines below describe the state as Phase 11 found
it, and they are kept because the reasoning is what justifies the constant now
living in `live.py`. The measurement that closed it is at the top of this file.

The build was read from two independent sources and they agree:

| Source | Build |
|---|---|
| `terminal64.exe` FileVersion | **6230** |
| the running terminal's own `terminal_build` | **6230** |

`auto-trade`'s control identifiers were measured on **6184**. The gap was **46
builds**, and upstream's own rule is explicit:

> Never substitute a control identifier you have not measured. A control found once
> is a control whose behaviour is not established. If a build presents something
> different, refuse and report it — do not wire it up.

So `check_control_ids` refused, `build_live` refused before it built anything, and
`python scripts/control_ids.py` printed the whole thing as a report. Only
re-measuring every identifier on build 6230, at this display resolution, could clear
it — and that is what was done, with `auto-trade terminal-check`: 13 of 13 controls
OK, nothing drifted, nothing missing.

**The gate is armed again for the next update**, which is the point of it.

### Three findings that only a live terminal could have produced

**1. `account_info().name` is the account holder's display name, not the server.**

```
terminal title bar   50000001 - Alpari-MT5-Demo: Demo Account - Hedge - Alpari - [USDInd,H1]
account_info().login  50000001
account_info().name   "Example Account Holder"        <- the account holder's name
```

The bridge maps that field to `AccountBalance.server`, so a log line reading
`server=Example Account Holder` is **the adapter reporting faithfully**. The field name is
the terminal's, not this project's, and renaming it would be the bug rather than the
fix. Recorded so nobody later "corrects" it.

**2. The index symbol is `USDInd`, not `USDIndex`.** The terminal's title bar says
`[USDInd,H1]`, and asking it confirms it: `symbol_info("USDInd")` returns a
specification and `symbol_info("USDIndex")` returns `None`. A symbol list written
from intuition would silently never resolve, and every trade on it would be refused
as *unknown* rather than as a typo — the safe direction, and still a bug.

**3. The bindings keep their state in module globals.** A direct
`MetaTrader5.symbol_info` call *after* a `shutdown` answers `None` for every symbol.
That produced a test asserting "this terminal describes no symbol at all", which was
a fact about the binding's lifecycle and not about the terminal. It is now driven
through the fixture's own connection, and the fixture is **function-scoped** for the
same reason: a class-scoped one that calls `shutdown` after its last test leaves
every earlier connection invalid for any test that runs afterwards, with a symptom
that looks exactly like a terminal with no market data.

### What was verified, live and read-only

`BRIDGE_ALLOW_MT5_TESTS=1 python -m pytest tests/live` — **21 passed, 0 skipped.**

* the terminal is running and logged in as `50000001`
* the account provider reads balance, equity, currency and login from it
* the symbol provider reads `trade_contract_size`, `trade_tick_size`,
  `trade_tick_value_profit` and the rest — **the field names Phase 7 could not
  verify, now verified against the real thing**
* the position snapshot parses, is fresh, and reports `terminal_build 6230`
* `open_positions` is readable through the account provider with the reader wired
* the staleness limit agrees with `auto-trade`'s own default, at 30 seconds
* an unknown symbol is refused rather than defaulted

Nothing was clicked. Nothing was launched — the terminal was already up, and a test
asserts the adapter package contains no `launch` attribute anywhere. No order was
placed; this file's scope is reading and refusing.

### One design decision worth stating

`MetaTrader5` **was not installed** on this machine and Phase 11 installed it, which
is why the live tests could run at all. That broke one existing test — the one
asserting the loader's message when the package is *absent* — so it now **hides the
module with `monkeypatch`** rather than relying on its absence. A test whose subject
is "the dependency is not installed" has to be able to arrange that itself, on any
machine, or it stops testing the moment somebody follows the instructions.

### What cannot be verified here at all

The **field names of the bindings**. `trade_tick_size`, `trade_tick_value_profit`
and the rest are MT5's own and are taken from the Phase 0 audit of the contract,
not from the installed package — `MetaTrader5` is **not installed here**. A rename
would surface as an `AttributeError` at runtime, which is loud, but it is a failure.
The conversion logic is tested; the names are not. **Phase 11 exercises the real
bindings** behind its opt-in marker.

### The terminal build, now cross-checked

`auto-trade`'s own `HANDOFF.md` says a build number is a load-bearing fact and must
be confirmed from **at least two sources**. Two independent sources agree:

| Source | Build |
|---|---|
| The indicator's `terminal_build` field, read from the live snapshot | **6230** |
| `terminal64.exe` version resource, `FileVersion` | **5.0.0.6230** |

`auto-trade`'s control ids were measured on **6184**. That is a **46-build gap**,
so Known Issue 5 is now a *confirmed* risk rather than an unknown one, and Phase 11
owns it. Also confirmed from the snapshot: `server: Alpari-MT5-Demo` and
`account: 50000001` — the demo target Q3 asks for, and no real money is in reach.


---

