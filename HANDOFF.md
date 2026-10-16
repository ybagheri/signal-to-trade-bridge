# HANDOFF

> **Read this before touching anything.** It is the persistent context for whoever
> continues this project, human or AI. But it is a *continuity aid, not an
> authority over the codebase.*
>
> **If this file disagrees with the repository, the repository is right and this
> file must be corrected.** A stale handoff is a bug. Verify with `git status`,
> `git log --oneline -n 10` and the source before trusting any line below.
>
> Per-phase narrative lives in [`docs/history/`](docs/history/) now (moved
> verbatim on 2026-10-16). This file is the live state only: where the project
> stands, what is proven, what is still open, and the rules.

---

## Current status

**Phases 0–17 complete. The order path is verified end to end on a real demo
terminal.** The last live order (2026-10-15, `stb-demo-003`) carried the
approved volume, stop and target exactly; the measurement and the fill are
recorded under [`docs/measurements/`](docs/measurements/) and pinned by
`tests/unit/test_field_commit_evidence.py`.

**Suite at 100%** (1385 passed, 6 skipped — every skip carries its stated
reason), with CI running the full gate
(`ruff check`, `ruff format --check`, `mypy`, `pytest`, domain isolation) on
Python 3.11 + 3.12 without either private repository.
`tests/integration` passes against the real `albrooks` engine (9 tests).

```
Last completed phase: 17
Current phase:       none open
Next phase:          see ROADMAP.md ("Later" and the remaining hygiene items)
```

**What works today:** a signal goes all the way to a decision and, when the
machine is deliberately armed, to a demo order carrying the approved levels.
The default configuration cannot trade (`build_bridge` returns
`can_execute=False` with no executor; arming is `BRIDGE_EXECUTION_ENABLED=true`
plus `BRIDGE_DRY_RUN=false`, set by hand in `.env`, never by a command).

**Two findings that changed the project's understanding of itself, newest first:**

1. **Dialog writes do not commit; keystrokes do** (2026-10-15). `set_edit_text`
   displays without committing to MT5's order state; only real keystrokes move
   it. Proven with read-only probes, fixed upstream, waited on via MT5's own
   committed-state labels (`10881`, `11138`, `11139`) before every click.
2. **The One Click premise was wrong** (2026-10-15). Fills at panel defaults
   happened with Algo Trading on. The `trade_allowed` guard stays as a cheap
   pre-click refusal, not the primary protection. Verification now also
   compares the new position's stop and target against the approval.

## Known issues (upstream, not ours to fix unilaterally)

1. **`albrooks` has a packaging defect.** `pythonpath = ["src"]` sits at the top
   level after `[project.optional-dependencies]`, so setuptools parses it as an
   extra requirement group. Never install the `pythonpath` extra.
2. **`auto-trade` has a dead `SymbolInfo` class.** Never constructed, never read.
   Do not design against it.
3. **`auto-trade`'s `max_open_positions` risk gate is inert.** No env var, no
   config wiring, `open_positions` never populated by the real adapter. Until
   that changes, an unset `BRIDGE_MAX_OPEN_POSITIONS` remains the only correct
   configuration.
4. **`auto-trade`'s `MT5BridgeSignalProvider` is exported but never wired.** A
   viable decoupling for a later phase; an option, not a design.
5. **Control identifiers were measured on build 6184; this project re-measures
   per build.** The gate refuses on drift by design. Current measurement: build
   6230, 16 of 16 controls OK including the three commit witnesses.
6. **Upstream defaults hard-code another machine's absolute paths.** The bridge
   reads none of the execution project's configuration (separate `BRIDGE_`
   prefix by design).
7. **Neither upstream is on PyPI.** Both are private git repositories; the
   dependency strategy in `docs/architecture.md` §7 exists because of this.

## Open questions

- **Q2. Whether the MT5 data code is shared with `albrooks` or lives here.**
  Still the maintainer's call. `adapters/fake/` pins the contract either way,
  so this is a smaller decision than it was.
- **Q6. Whether the default `RR_FALLBACK` should carry a floor.** Deliberately
  open: `BRIDGE_MINIMUM_REWARD_RISK_RATIO` is opt-in. Changing the default is a
  trading decision no test can settle; it needs the maintainer.
- Settled, kept here so nobody reopens them by accident: **Q1** (identity is a
  bar hash, prices excluded), **Q4** (`WAIT` and `NO_TRADE` stay distinct but
  behave identically), **Q5** (the bridge never places orders through the
  `MetaTrader5` bindings; the execution project owns execution), **Q3** (live
  validation target is the Alpari demo account named in `.env.example`; the
  terminal is read, never launched, never toggled).

## Verification state

- Control identifiers: re-measured per build via `auto-trade terminal-check`
  (read-only by construction); `docs/measurements/control_ids.json` is the
  committed record and `test_control_id_evidence.py` forbids moving
  `MEASURED_ON_BUILD` without one.
- Dialog commit behavior: `docs/measurements/field_commit.json` records the
  SetValue-never-commits finding plus the live fill that proved the
  keystroke-write fix; `test_field_commit_evidence.py` pins it.
- Live orders: verified by a person on the demo account per
  `docs/demo-execution-test.md` (one signal file in, at most one order out,
  never re-run a non-zero exit). The ledger is the authority on what was
  attempted; the terminal is the authority on what exists.
- **Out of scope, always:** closing positions, any second order or retry
  logic, toggling terminal settings, trading a non-demo account.

## Protocol for future AI agents

> Any AI agent continuing development of this repository **MUST** read this file
> before making changes. It must then verify the actual repository state using Git
> and the source code.
>
> `HANDOFF.md` is a continuity aid, not an authority over the codebase. When it
> conflicts with the repository, **the repository state takes precedence and this
> file must be corrected.**

This rule is also stated in `README.md`, in both the English and Persian versions.
