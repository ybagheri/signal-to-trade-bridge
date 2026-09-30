# HANDOFF

> **Read this before touching anything.** It is the persistent context for whoever
> continues this project, human or AI. But it is a *continuity aid, not an
> authority over the codebase.*
>
> **If this file disagrees with the repository, the repository is right and this
> file must be corrected.** A stale handoff is a bug. Verify with `git status`,
> `git log --oneline -n 10` and the source before trusting any line below.

---

## Current Status

**Phase 0 complete. Phase 1 not started.**

Phase 0 was a repository audit. No trading code exists in this repository yet, and
that is deliberate: the audit found that the design decisions required by the brief
depend on facts that had to be read out of the two upstream source trees rather than
assumed. `docs/architecture.md` and `docs/integration.md` are the deliverables.

```
Last completed phase: 0
Current phase:        1 (not started)
Next phase:           1 — project foundation
```

---

## Completed Phases

- [x] **Phase 0** — Repository discovery and architecture audit
- [ ] Phase 1 — Project foundation
- [ ] Phase 2 — Al Brooks signal adapter
- [ ] Phase 3 — Internal trading domain
- [ ] Phase 4 — Risk management
- [ ] Phase 5 — 1:1 risk/reward
- [ ] Phase 6 — Trade validation pipeline
- [ ] Phase 7 — auto-trade adapter
- [ ] Phase 8 — Dry run / simulation
- [ ] Phase 9 — Idempotency / duplicate protection
- [ ] Phase 10 — End-to-end integration
- [ ] Phase 11 — MT5 / demo validation
- [ ] Phase 12 — Documentation
- [ ] Phase 13 — Final architecture review

---

## The audit's five findings that shape everything

Read `docs/architecture.md` for the full detail with verbatim code. These are the
ones that change how the work must be done.

**1. Position sizing does not exist anywhere, and cannot be delegated.**
Neither upstream project knows the account balance or a symbol's contract
specification, so neither can implement a 0.5%-of-balance rule. This is the
substance of the project, not an adapter detail. The audit verified the absence:
`trade_contract_size`, `volume_step`, `trade_tick_value`, `balance`, `equity` and
`margin` appear **nowhere** in `auto-trade`'s `src/`, `docs/` or `mql5/`, and
`albrooks` treats a symbol as an opaque `str`.

**2. `auto-trade` does not use the MetaTrader5 Python package.** It is a Windows
desktop UI-automation bridge — `pywinauto` drives the MT5 order dialog and clicks
`Buy by Market` / `Sell by Market`. It never launches or logs into the terminal, and
it cannot read a number from it. So the bridge needs its *own* MetaTrader5 data
adapter for account and symbol facts, built on the injectable pattern
`albrooks.adapters.mt5.MT5Feed` already demonstrates.

**3. The Al Brooks engine has no `Signal` class and no signal identity.** The
signal is a `dict` at `AnalysisResult.decision`, and there is no callback, observer
or event bus. Critically, there is **no stable signal identifier** — but the
downstream dedup ledger is keyed on `signal_id`. So the bridge must derive a
*deterministic* `signal_id` from signal content, or the whole dedup mechanism
silently fails across restarts. This is a design obligation, not a nicety.

**4. The execution layer has no side-of-entry check.** `auto-trade` writes the stop
loss into a dialog field and reads it back; it does not verify the stop is on the
correct side of entry, and it has no stop-level, freeze-level or digits handling.
`albrooks` reports the problem as a plan `issue`, but a plan crosses a process
boundary here. **The bridge is the last place this can be caught locally** instead
of being handed to the broker.

**5. A stop exists upstream, and it carries its provenance.** `TradePlan.stop` is
an absolute price with a `stop_basis`, and the engine exposes
`has_structural_stop` — false for `ATR_FALLBACK` and `NONE`. The engine's own
docstring calls an ATR-fallback stop "arithmetically sound and structurally empty".
The bridge should use a structural stop when one exists and refuse otherwise,
which is exactly what the brief asks, and it is now grounded in the real API rather
than in an assumption.

---

## Current Architecture

Proposed in Phase 0, implemented from Phase 1. Full rationale in
`docs/architecture.md §5`.

```
interfaces/     CLI, wiring, composition root
application/    ProcessSignal — the single use case
domain/         Signal, TradeIntent, RiskParameters, PositionSize, TradeDecision,
                SymbolSpec, AccountBalance, and the pure calculations
ports/          Protocols: SignalSource, MarketDataProvider, AccountProvider,
                SymbolSpecProvider, TradeExecutor, IdempotencyStore
adapters/       albrooks/  auto-trade/  mt5/  fake/
```

Dependency direction is strictly inward. `domain` imports nothing from any other
layer and nothing from `adapters/`. Every upstream project is reached only through a
protocol in `ports/`.

Decisions already made in Phase 0 and carried forward:

- **`Decimal` for prices, volumes and money** in the domain. Upstream A uses
  `float`, upstream B uses `Decimal`; a `0.01` lot error on a gold contract is a
  real-money error.
- **Tick-value-based sizing**, not a Forex pip formula, so gold, indices, CFDs and
  futures-like instruments work.
- **The worse of `tick_value_profit` / `tick_value_loss`** is used, because a size
  that is safe on paper must be safe on the losing side.
- **Volume below `volume_min` is a refusal, never a floor-up.** The single most
  dangerous line in a position sizer.
- **Rounding to `volume_step` is always down.**
- **`auto-trade`'s `ExecutionWorkflow` is wrapped, never bypassed.** Calling its
  adapter directly would skip the risk engine, the kill switch, the ledger, the
  state machine and the audit log.
- **A fake `TradeExecutor` implements the same port as the real one**, so a test
  asserting on recorded orders exercises the real pipeline.

---

## Important Decisions

Recorded with their reasons, because these are the ones a future phase is most
likely to re-litigate by accident.

| Decision | Reason |
|---|---|
| Build sizing in the bridge; do not look for it upstream | Verified absent in both trees. It is the project's substance. |
| Build a separate MT5 data adapter | `auto-trade` cannot read account or symbol data; `albrooks` has the injectable `MT5Feed` pattern to copy. |
| Reuse `JsonExecutionLedger`; do not build a second store | Already durable, atomic and cross-process. A second store is a second source of truth. |
| Refuse an `ATR_FALLBACK` stop by default | The engine calls it "structurally empty"; the brief forbids inventing a stop to make the system trade. |
| `Decimal` in the domain | Float drift in position sizing is a monetary error. |
| Both `WAIT` and `NO_TRADE` map to the bridge's no-trade, but the distinction is preserved | They are different upstream claims: an abstention versus a condition not met. Losing that loses traceability. |
| Read the decision dict with `.get()` everywhere | The degenerate path returns a 3-key dict, not the normal 13-key one. Strict access would raise on exactly the input that most needs a clean refusal. |
| Editable local path dependencies, not git or submodule | Offline, exact, and identical on every machine. See `docs/architecture.md §7`. |
| Bridge dry run short-circuits before the executor | Guarantees downstream configuration cannot cause an order during simulation. |
| The domain test suite requires neither upstream installed | Enforced by asserting the domain package's import closure. Makes the suite runnable on a laptop with no upstreams cloned. |

---

## Implemented Features

None. Phase 0 produced documentation only, which is the correct output for an
audit phase.

Delivered:

- `docs/architecture.md` — the design and the gap analysis
- `docs/integration.md` — verbatim upstream API contracts and a do-not-assume
  checklist
- `README.md` — English, authoritative
- `README_FA.md` — Persian, linked from the English README
- `HANDOFF.md` — this file

---

## Tests

None yet. The test harness arrives in Phase 1.

What the audit established about the upstream suites, for reference:

- `albrooks`: ~825 tests over 40 files, pytest, plus a 5-chart golden fixture set
  and an MQL5 parity harness. Live-terminal tests **skip** without a terminal — and
  its own `HANDOFF.md` states that **a skip is not a pass**.
- `auto-trade`: pytest with a `scripts/test.ps1` gate. MT5 access is mocked
  throughout; no test requires a live terminal.

---

## Known Issues

Issues found **in the upstream repositories** during the audit. They are recorded
so a bridge phase does not rediscover them mid-implementation. None is the bridge's
to fix without the maintainer's agreement.

1. **`albrooks` has a packaging defect.** In its `pyproject.toml`,
   `pythonpath = ["src"]` sits at the top level *after*
   `[project.optional-dependencies]`, so setuptools parses it as **an extra
   requirement group named `pythonpath`**, not as pytest config. Generated metadata
   confirms `Provides-Extra: pythonpath` and `Requires-Dist: src; extra ==
   "pythonpath"`. Installing `albrooks[pythonpath]` would try to resolve a package
   named `src` from PyPI. Its tests only work because `tests/conftest.py` inserts
   `src` on `sys.path` by hand. **The bridge must not install the `pythonpath`
   extra.**
2. **`auto-trade` has a `SymbolInfo` class that is dead code** — three fields
   (`symbol`, `available`, `digits`), never constructed, never read. Do not design
   against it.
3. **`auto-trade`'s `max_open_positions` risk gate is inert.** It has no env var,
   no config wiring, and `AccountSnapshot.open_positions` is never populated by the
   real adapter.
4. **`auto-trade`'s `MT5BridgeSignalProvider` is exported but never wired** into
   `AppConfig` or any CLI command. It is a viable decoupling for a later phase;
   recorded as an option, not the Phase 7 design.
5. **`auto-trade`'s control ids were measured on Alpari MT5 build 6184.** A
   different build may drift them. `terminal-check` exists for this. This is a live
   risk for Phase 11.
6. **`auto-trade`'s defaults hard-code another machine's absolute paths** —
   `C:\Program Files\Alpari MT5_2\...` and a `C:\Users\BazikadeStore\...` data
   path. The bridge must read none of its configuration.
7. **Neither upstream is on PyPI.** Both are private git repositories. The
   dependency strategy in `docs/architecture.md §7` exists because of this.

---

## Remaining Work

### Phase 1 — project foundation

- `pyproject.toml`: setuptools, src layout, `requires-python = ">=3.11"`, ruff,
  mypy, pytest config. **Both upstreams require ≥3.10 and ≥3.11 respectively, so
  the bridge requires ≥3.11.**
- Directory skeleton: `domain/`, `application/`, `ports/`, `adapters/`,
  `interfaces/`, `tests/`.
- Configuration system: `risk_percent` (default `0.5`), `reward_risk_ratio`
  (default `1.0`), `take_profit_source`, `dry_run`, `execution_enabled`, and the
  rest deferred until a phase justifies them.
- Structured logging foundation.
- Test harness: pytest, plus a test that asserts the domain package's import
  closure contains no `adapters/` import.
- `.env.example`, `.gitignore` covering secrets.
- `scripts/setup.ps1` and `scripts/setup.sh` that generate the local path
  dependencies from `ALBROOKS_PATH` and `AUTO_TRADE_PATH` — **no drive letter
  committed**.
- Install instructions for Windows and Linux.

### Phases 2–13

As laid out in `README.md`. Phase 4 is the substantial one: the tick-value position
sizer with broker constraint handling, and the refusal-not-floor-up rule.

---

## Open questions Phase 0 deliberately did not settle

Recorded rather than guessed. Each needs real data or the maintainer's decision.

1. **Exact `signal_id` composition.** Proposed: SHA-256 over
   `(symbol, timeframe, last_closed_bar, bar_time, action, subject, entry, stop,
   target)`. The unresolved tension: including the prices means a recomputation
   that nudges the stop by one tick is a *new* signal and will trade again. Phase 9
   must settle this against real engine output.
2. **Whether the MT5 account/symbol adapter extends `albrooks.adapters.mt5` or
   stands alone.** Both defensible. Depends on whether `albrooks` would accept a
   new public surface — the maintainer's call.
3. **Live MT5 validation target.** Alpari demo, terminal at
   `C:\Users\bagheri\AppData\Roaming\Alpari MT5\terminal64.exe`, data folder
   `C:\Users\bagheri\AppData\Roaming\MetaQuotes\Terminal\1BFBA8D123B04AAD5E48746348E9B594`.
   Not touched in Phase 0. Phase 11 must confirm the terminal build matches what
   `auto-trade`'s control ids were measured on (Known Issue 5).
4. **The configuration name for the confidence threshold.** The underlying quantity
   is an evidence score, not a probability, and both upstreams say so explicitly.
   The brief calls it `minimum_signal_confidence`; that name is probably wrong and
   should become something like `minimum_evidence_score`.
5. **Whether `WAIT` and `NO_TRADE` should ever differ in bridge behaviour.** They
   are both no-trade, but they are different upstream claims and the mapping is
   currently lossless-but-identical. Worth revisiting if a phase needs the
   distinction.

---

## Environment / Setup Notes

**Current development machine**

- Terminal: `C:\Users\bagheri\AppData\Roaming\Alpari MT5\terminal64.exe`
- Data folder:
  `C:\Users\bagheri\AppData\Roaming\MetaQuotes\Terminal\1BFBA8D123B04AAD5E48746348E9B594`
- Git: `C:\Users\bagheri\Downloads\PortableGit-2.49.0-64-bit.7z\git-cmd.exe`
  (PortableGit 2.49.0; **add its `bin` to `PATH` or call it by full path**)
- Python: 3.13.12 at
  `C:\Users\bagheri\Downloads\python-3.13.12-embed-amd64\python.exe` — note this
  is an **embeddable** build, where `python -m venv` can misbehave; prefer an
  explicit install if a virtual environment is needed.
- Workspace: `E:\signal-to-trade-bridge`
- Upstream checkouts: `E:\al-brooks-price-action-engine`, `E:\auto-trade`

**None of these paths may appear in application logic.** They are recorded here
because a human needs them, and in `.env` files because configuration is
per-machine by definition. A path in the domain layer is a bug.

`E:\` is the *current* development path, not a requirement. Everything except MT5
itself must work on a laptop where nothing lives on `E:`.

**Upstream revisions at audit time**

| Repo | Branch | Commit |
|---|---|---|
| `al-brooks-price-action-engine` | `main` | `d1fec18` |
| `auto-trade` | `main` | `b88e9e7` |
| `auto-trade` | `feat/observer-verification-and-dashboard` | `3bd3a98` |

---

## Important Repository Information

| | |
|---|---|
| This repository | `git@github.com:ybagheri/signal-to-trade-bridge.git` |
| Local path | `E:\signal-to-trade-bridge` |
| Upstream A | `git@github.com:ybagheri/al-brooks-price-action-engine.git` |
| Upstream B | `git@github.com:ybagheri/auto-trade.git` |
| Python | `>=3.11` (the higher of the two upstreams' floors) |
| Platform | Windows for MT5; the domain and its tests are platform-independent |
| Dependencies | `albrooks` and `auto-trade` as editable local paths; `MetaTrader5` and `pywinauto` in a Windows extra |

---

## Git Status

At the end of Phase 0: branch `main`, working tree clean, tracking
`origin/main`.

```
68c8b87  docs: phase 0 architecture audit and integration contracts
```

---

## Latest Commit

```
68c8b87fb242c9e886773572fa14a2d2475d577e
docs: phase 0 architecture audit and integration contracts
```

**Push status: SUCCESS** — `main` created on
`git@github.com:ybagheri/signal-to-trade-bridge.git`.

---

## Chronological History

- **Phase 0** — audited both upstream repositories by reading their source, not
  their documentation alone. Established that position sizing, account data and
  symbol specification exist in neither, that `auto-trade` is a UI-automation
  bridge rather than an MT5 bindings client, and that `albrooks` emits a dict
  rather than a signal object. Wrote `docs/architecture.md`,
  `docs/integration.md`, `README.md`, `README_FA.md`, `.gitignore` and
  `.gitattributes`. Committed and pushed as `68c8b87`.

---

## How To Continue

**Before doing anything else:**

1. Read `HANDOFF.md` — this file.
2. `git status`
3. `git log --oneline -n 10`
4. Read `docs/architecture.md` §4 (the gap analysis) and §5 (the proposed
   architecture). §9 lists the safety invariants that later phases must not relax.
5. Read `docs/integration.md` §3 and §4 — the consolidated gaps and the
   do-not-assume checklist.
6. Verify the actual repository state against this file. **If they conflict, the
   repository wins and this file must be corrected.**

**Then start Phase 1** from the Remaining Work list above.

**Standards for every phase, without exception:**

```
IMPLEMENT → TEST → DOCUMENT → UPDATE HANDOFF.md → COMMIT → PUSH → CONTINUE
```

A phase is complete only when all of the following hold:

- [ ] Implementation completed
- [ ] Tests added
- [ ] Existing tests still pass
- [ ] New tests pass
- [ ] No regression
- [ ] Documentation updated
- [ ] `HANDOFF.md` updated
- [ ] `git diff` reviewed
- [ ] Commit created
- [ ] Commit pushed

**Do not mark a phase complete if the commit was not created, and do not claim a
push succeeded if it failed.** If a push fails on authentication or network, record
the exact failure here, keep the commit locally, and continue.

**Do not open a live MT5 terminal, and do not place any order, outside Phase 11**,
and in Phase 11 only with the demo account and only with dry run confirmed off
deliberately. Ordinary tests must never require a live trading account.

---

## Protocol for future AI agents

> Any AI agent continuing development of this repository **MUST** read this file
> before making changes. It must then verify the actual repository state using Git
> and the source code.
>
> `HANDOFF.md` is a continuity aid, not an authority over the codebase. When it
> conflicts with the repository, **the repository state takes precedence and this
> file must be corrected.**

This rule is also stated in `README.md`, in both the English and Persian versions.
