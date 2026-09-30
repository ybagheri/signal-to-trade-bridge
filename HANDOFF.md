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

**Phases 0 and 1 complete. Phase 2 not started.**

Phase 0 was a repository audit. Phase 1 built the project foundation: packaging,
the domain layer, the ports, configuration, structured logging, and a 126-test
suite that runs without MetaTrader 5 and without either upstream project
installed.

No trading code exists yet, and that is correct: the pipeline that reads a signal
and produces an order is Phases 2 through 8.

```
Last completed phase: 1
Current phase:        2 (not started)
Next phase:           2 — Al Brooks signal adapter
```

---

## Completed Phases

- [x] **Phase 0** — Repository discovery and architecture audit
- [x] **Phase 1** — Project foundation
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

## What Phase 1 Built

### Layout

```
src/signal_to_trade_bridge/
  domain/          models.py  enums.py  errors.py     no external deps at all
  ports/           __init__.py                        six Protocols
  application/     __init__.py                        empty; ProcessSignal is Phase 6
  adapters/        albrooks/ auto_trade/ mt5/ fake/   all empty; filled in Phases 2, 7, 7
  infrastructure/  logging/                           structured.py  events.py
  configuration/   config.py                          BridgeConfig  config_from_env
  cli/             __init__.py                        empty; CLI is Phase 8
  py.typed
tests/
  conftest.py                                      fixtures, PROJECT_ROOT
  unit/            test_domain_isolation.py  test_domain_models.py
                   test_configuration.py     test_logging.py
docs/              architecture.md  integration.md  setup.md
scripts/           setup.ps1  setup.sh  test.ps1
```

### Domain layer

Frozen, slotted, self-validating dataclasses: `AccountBalance`, `SymbolSpec`,
`Signal`, `StopLoss`, `TakeProfit`, `RiskParameters`, `PositionSize`,
`TradeIntent`, `ExecutionRequest`, `ExecutionResult`, `TradeDecision`.

Enums: `Direction`, `SignalAction`, `DecisionAction`, `StopSource`,
`TakeProfitSource`, `RejectionReason` (39 codes).

Errors: two roots, `ConfigurationError` and `TradingError`, with twelve
subclasses. The split is deliberate — a configuration error should stop the
process, a refused trade should be logged and the next signal processed.

### Ports

Six `Protocol`s: `SignalSource`, `MarketDataProvider`, `AccountProvider`,
`SymbolSpecProvider`, `TradeExecutor`, `IdempotencyStore`, `KillSwitch`. Narrow
on purpose, because a test has to fake them.

### Configuration

`BridgeConfig` + `config_from_env`, reading 16 `BRIDGE_*` variables. Defaults are
the safe ones: `execution_enabled=False`, `dry_run=True`, `risk_percent=0.5`,
`reward_risk_ratio=1.0`.

### Logging

`Event` enum with 12 event names, `StructuredLogger`, JSON and text formatters,
and substring-based redaction of 13 sensitive key markers.

### Tests: 126, all passing

| File | Covers |
|---|---|
| `test_domain_isolation.py` | 7 tests. The architectural invariant: the domain imports nothing external, and no upstream project. |
| `test_domain_models.py` | 70 tests. Every value object, its validation, and its invariants. |
| `test_configuration.py` | 33 tests. Defaults, overrides, and failing loudly. |
| `test_logging.py` | 28 tests. Redaction, JSON shape, handler hygiene, event vocabulary. |

Quality gate, all clean: `ruff check`, `ruff format --check`, `mypy` (20 files),
`pytest`.

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

Proposed in Phase 0, scaffolded in Phase 1. Full rationale in
`docs/architecture.md §5`.

```
interfaces/     CLI, wiring, composition root          (Phase 8)
application/    ProcessSignal — the single use case    (Phase 6)
domain/         Signal, TradeIntent, RiskParameters, PositionSize, TradeDecision,
                SymbolSpec, AccountBalance, and the pure calculations   (Phases 1, 3, 4)
ports/          Protocols: SignalSource, MarketDataProvider, AccountProvider,
                SymbolSpecProvider, TradeExecutor, IdempotencyStore  (Phase 1)
adapters/       albrooks/  auto-trade/  mt5/  fake/
                albrooks → Phase 2   auto-trade + mt5 → Phase 7   fake → Phase 7
```

Dependency direction is strictly inward. `domain` imports nothing from any other
layer and nothing from `adapters/`, and this is **enforced by a test** that walks
the domain package's AST rather than being left as a convention. Both upstreams
are private repositories that cannot be installed from an index, so a domain
import of either would make the entire test suite unrunnable on any other
machine. The safety net would only exist where the code was written.

Decisions made in Phase 0 and carried forward:

- **`Decimal` for prices, volumes and money** in the domain. Upstream A uses
  `float`, upstream B uses `Decimal`; a `0.01` lot error on a gold contract is a
  real-money error.
- **Tick-value-based sizing**, not a Forex pip formula, so gold, indices, CFDs and
  futures-like instruments work.
- **The worse of `tick_value_profit` / `tick_value_loss`** is used, because a size
  that is safe on paper must be safe on the losing side.
- **Volume below `volume_min` is a refusal, never a floor-up.** Enforced in
  `SymbolSpec.clamp_volume` (clamps *down* to zero, forcing the caller to notice)
  and again in `PositionSize.__post_init__`, which raises if
  `clamped_to_minimum` is ever set. Two independent guards, because this is the
  single most dangerous line in a position sizer.
- **Rounding to `volume_step` is always down.**
- **`auto-trade`'s `ExecutionWorkflow` will be wrapped, never bypassed.** Calling
  its adapter directly would skip the risk engine, the kill switch, the ledger,
  the state machine and the audit log.
- **A fake `TradeExecutor` implements the same port as the real one**, so a test
  asserting on recorded orders exercises the real pipeline.

Decisions made in Phase 1:

- **Two error roots, not one.** `ConfigurationError` (stop the process) and
  `TradingError` (refuse this trade, keep going). Flattening them would mean
  either halting on a routine refusal or ignoring a broken configuration.
- **`BRIDGE_` prefix for every environment variable.** The execution project uses
  `AUTO_TRADE_`, several of whose defaults are absolute paths on another machine.
  A distinct prefix makes it legible in a process listing that the bridge reads
  none of them.
- **Configuration fails loudly.** An unparsable value raises rather than falling
  back to a default, because a risk percentage that quietly reverted to 0.5
  because of a typo would leave a system trading at a level nobody chose.
- **The evidence threshold is named `minimum_evidence_score`, not
  `minimum_signal_confidence`.** See the audit's fifth finding.
- **Redaction is substring-based and broad.** A key merely *containing* `token` is
  redacted. The cost of redacting a harmless field is a slightly less informative
  log line; the cost of missing one is a leaked credential in a file that
  eventually gets pasted into a bug report.

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

Phase 1 produced a working foundation rather than a trading capability. What
exists and works:

- The **domain layer**, importable and testable with zero external dependencies.
- The **ports**, as the only thing the inner layers may depend on.
- **Configuration** that defaults to safe and fails loudly on a mistake.
- **Structured logging** with a fixed event vocabulary and automatic redaction.
- A **126-test suite** that runs in under a second, with no MetaTrader terminal,
  no network and no upstream project installed.
- **Setup scripts** for Windows, Linux and macOS, with no committed drive letter.
- **Documentation**: architecture, integration contracts, setup, bilingual README.

Not yet implemented, and not to be assumed: any part of the decision pipeline. A
signal still goes nowhere. Phases 2 through 8 build it.

---

## Tests

**126 tests, all passing.** The suite runs in under a second and requires no
MetaTrader terminal, no network access, and neither upstream project installed.

| File | Tests | Covers |
|---|---|---|
| `tests/unit/test_domain_isolation.py` | 7 | The architectural invariant: the domain imports nothing external and no upstream project. Walks the AST rather than importing. |
| `tests/unit/test_domain_models.py` | 70 | Every value object, its validation, and its invariants — including the floor-up refusal and the unknown-status normalisation. |
| `tests/unit/test_configuration.py` | 33 | Defaults, overrides, fail-loudly behaviour, dotenv parsing, repr redaction. |
| `tests/unit/test_logging.py` | 28 | Redaction cannot be bypassed, JSON shape, handler hygiene, event vocabulary completeness. |

The full gate, all clean:

```
ruff check .            All checks passed!
ruff format --check .   34 files already formatted
mypy                    Success: no issues found in 20 source files
pytest                  126 passed
```

`mypy` reports one informational note — an unused `[[tool.mypy.overrides]]` block
for `MetaTrader5` / `albrooks` / `auto_trade`. It becomes used in Phases 2 and 7
when the adapters that import them are written. Not an error, and deliberately
left in place rather than added later, so the ignore exists before the import
does.

The test counts here are a claim that every later change falsifies. **CI is the
authority on what passes**; this table is a convenience.

### Tests deliberately not written yet

The brief's Phase 1 does not require them, and writing them before the code they
test exists would be theatre:

* position-sizing tests — Phase 4
* signal adapter tests — Phase 2
* idempotency tests — Phase 9
* end-to-end tests — Phase 10
* live MT5 tests — Phase 11, and only behind an explicit opt-in marker

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

### Phase 2 — Al Brooks signal adapter  ← next

The first adapter, and the one that proves the anti-corruption layer works.

- `adapters/albrooks/source.py` — `AlBrooksSignalSource`, implementing
  `SignalSource`. Wraps `Analyzer.analyze(...)` and converts
  `AnalysisResult` into the internal `Signal`.
- `adapters/albrooks/identity.py` — the deterministic `signal_id`. See Open
  Question 1; this is the phase that has to settle it.
- `adapters/albrooks/mapper.py` — the decision-dict reading. **Read every key
  with `.get()`**: the degenerate path returns three keys, not thirteen.
- Import `albrooks` lazily, inside the adapter, so that importing the bridge
  still works without it installed.
- Tests with a **stub analyzer**, not the real one, so the suite keeps running
  without the upstream checkout. Add a separate, opt-in integration test that
  runs the real `Analyzer` and is skipped when it is absent.

The mapping rules, from the audit:

| Upstream | Internal |
|---|---|
| `decision["action"]` `BUY`/`SELL` | `SignalAction.BUY`/`SELL` |
| `decision["action"]` `WAIT` | `SignalAction.WAIT` (abstention, `Direction.FLAT`) |
| `decision["action"]` `NO_TRADE` | `SignalAction.NO_TRADE` (abstention, `Direction.FLAT`) |
| `decision["direction"]` ±1/0 | `Direction.from_sign(...)` |
| `decision["plan"]["entry"]` | `Signal.entry` |
| `decision["plan"]["stop"]` + `stop_basis` | `Signal.stop_loss` + `Signal.stop_basis` |
| `decision["plan"]["target"]` + `target_basis` | `Signal.take_profit` + `Signal.take_profit_basis` |
| `decision["subject"]` | `Signal.setup_id` |
| `decision["evidence"]["value"]` | `Signal.evidence_score` — **not a probability** |
| `AnalysisResult.last_closed_bar` | `Signal.bar_index` |
| `Bar.time` of the last closed bar | `Signal.bar_time` (float epoch seconds) |
| missing `plan` | no stop, no target → the risk service refuses in Phase 4 |

`WAIT` and `NO_TRADE` both become abstentions, and **both must be preserved as
different values**. They are different upstream claims, and collapsing them would
lose the ability to explain afterwards why nothing happened.

### Phases 3–13

As laid out in `README.md`. Phase 4 is the substantial one — the tick-value
position sizer with broker constraint handling.

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

Branch `main`, tracking `origin/main`, working tree clean. Local and remote heads
verified identical at the end of Phase 1.

```
dc336bd  feat: project foundation, domain layer, ports, config and test harness
19e4888  docs: record phase 0 commit hash and push status in handoff
68c8b87  docs: phase 0 architecture audit and integration contracts
```

---

## Latest Commit

```
dc336bda4bfa3bf97f34b37675b1422fb2e40365
feat: project foundation, domain layer, ports, config and test harness
```

**Push status: SUCCESS** — `19e4888..dc336bd  main -> main` on
`git@github.com:ybagheri/signal-to-trade-bridge.git`. `origin/main` was read back
afterwards and matches the local head exactly.

---

## Chronological History

- **Phase 0** — audited both upstream repositories by reading their source, not
  their documentation alone. Established that position sizing, account data and
  symbol specification exist in neither, that `auto-trade` is a UI-automation
  bridge rather than an MT5 bindings client, and that `albrooks` emits a dict
  rather than a signal object. Wrote `docs/architecture.md`,
  `docs/integration.md`, `README.md`, `README_FA.md`, `.gitignore` and
  `.gitattributes`. Committed and pushed as `68c8b87`, then `19e4888`.

- **Phase 1** — built the foundation. `pyproject.toml` (setuptools, src layout,
  Python ≥3.11, ruff, mypy, pytest, the `mt5` marker). The domain layer:
  eleven frozen value objects, six enums, a 39-code `RejectionReason`, and
  twelve exception classes under two roots. Seven ports. `BridgeConfig` with 16
  `BRIDGE_*` settings that default to safe and fail loudly. Structured logging
  with a 12-event vocabulary and substring-based redaction. 126 tests, including
  a domain-isolation test that walks the AST. Setup scripts for three platforms
  with no committed drive letter, `docs/setup.md`, and `LICENSE`.

  Three real bugs were found by the tests and fixed, rather than the tests being
  adjusted to pass:
  - `config_from_env` used `bar_count=_int(...) or 300`, so a configured `0` was
    falsy and silently replaced by the default. The `or` idiom hid the very
    value validation was supposed to catch. Now `_require_int`.
  - `SymbolSpec.clamp_volume` documented that it would never raise a volume to
    the broker minimum, but implemented `min(max(v, 0), max)` — which returned a
    sub-minimum volume unchanged. That looks like a usable volume, and a caller
    checking only "is it in range" would pass it to the broker. Now clamps
    sub-minimum to zero, which is unambiguously invalid.
  - The `infrastructure/logging/__init__.py` re-exported event names as module
    constants, but the names are members of a single `Event` enum. The import
    failed at module load.

  One test was also wrong rather than the code, and was corrected: it built a
  modified frozen+slotted model with `{**intent.__dict__}`, which cannot work
  because such a model has no `__dict__`. Now uses `dataclasses.replace`, which
  also re-runs validation.

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
