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

**Phases 0–14 complete. Phase 15 (the One Click Trading guard) is now wired and
unit-tested but NOT verified against a real terminal. Phase 16 (repository hygiene,
test portability, documentation sync) is complete.** Read *Phase 16* below first: it is
the most recent work and it corrects several things older sections still say.

**1184 tests passing, 84 skipped, 93% coverage** on a clean machine with neither
`auto_trade`, `albrooks` nor `MetaTrader5` installed. Lint, format, mypy (48 source
files) and the domain-isolation check are clean. The skips are tests that genuinely
need a missing package or a live terminal, each marked with a reason. *(Older sections
quote 1235 / 1247 passed; those counts were taken on a machine with `auto_trade`
installed and are historical.)*

```
Last completed phase: 16
Current phase:       none open
Next phase:          17 — verify the One Click guard on a real terminal (see ROADMAP.md)
```

**Older text below describes the project as it stood at the time each phase was
written. Where it conflicts with Phase 16, Phase 16 wins** (and the repository wins
over both).

### The blocker is gone, and the live path is armed

Phase 11 refused to assemble the live side because the control identifiers were
measured on build 6184 and this terminal runs 6230. **That refusal is now lifted,
on evidence rather than on a changed number**, and this is the most consequential
change in the project so far.

The measurement was `auto-trade terminal-check` — upstream's own read-only probe. It
opens the order dialog, walks the control tree, reports every expected identifier as
OK / DRIFTED / MISSING, and closes the dialog again. It is read-only by construction,
and its own comment on why it always closes the dialog is worth reading: a dialog
left open over a trading terminal, or a click on an unrecognised one, is a market
order.

```
auto-trade terminal-check
  verdict   OK      checked 13      drifted []      missing []      ambiguous []
```

All thirteen controls present with the identifier they were measured for —
`10325` symbol, `10333` volume, `10334` stop loss, `10336` take profit,
`10408`/`10409` Buy and Sell, `10328` trade grid, plus `New Order` and
`Market Execution` by name. 46 builds moved and **no control this project depends
on moved with it**. The report is at `logs/terminal_check.json`.

`MEASURED_ON_BUILD` and `EXPECTED_BUILD` became **6230** after that, not before it.
A re-measurement that cannot report failure is not a re-measurement, so
`tests/unit/test_control_id_evidence.py` requires the recorded probe to exist, to
name the constant, and to report zero drifted and zero missing — which means the
number cannot be edited without producing the evidence for it.

**What still stops an order, verified one rung at a time:**

| | gate | status on this machine |
|---|---|---|
| 1 | control identifiers match the build | **passes now** |
| 2 | `execution_enabled` | stops it — the shipped default is false |
| 3 | `not dry_run` | stops it — the shipped default is true |
| 4 | ledger readable | checked after the above |
| 5 | account is `DEMO` | checked after the above |
| 6 | kill switch clear | checked after the above |

And the rung that is a **code path rather than a setting**: `build_bridge()` — what
the CLI and every library consumer get — returns `can_execute=False` with no executor
attached, and a decision from it carries no execution result at all. Reaching an
order takes a deliberate `build_live()` call *and* two environment variables, so
nothing here is one flag away from a trade.

**A correction worth keeping.** An intermediate check in this phase read
`ProcessSignal`'s source, found no `Executor` class, and concluded the executor was
unreachable from a signal. That was wrong: `build_live` calls
`pipeline.wire_execution(...)` at runtime, so the module text never mentions the
class while the live pipeline carries one anyway. **A grep of the source is not a
statement about the object.** The table above was produced by building each
configuration and reading what the assembled bridge actually reported.

### Two defects the reconciliation exposed in our own code

Both were invisible while the build mismatch was short-circuiting everything above
them, which is the argument for fixing a blocker rather than working around it.

1. **`ControlIdCheck.refusal()` returned a refusal when the build matched.** It
   branched on whether the *sources* were readable and never asked whether the builds
   agreed, so on a reconciled build it produced the full "a gap of 0 builds"
   paragraph. `doctor` reads `matches` for its verdict and `refusal()` for the text
   underneath, so it would have printed **`ok`** directly above a refusal. A method
   that cannot say "no reason" will eventually be asked to justify a pass.

2. **`check_control_ids` froze its own guard.** `expected` was a default argument,
   which binds the value when the module is imported. So `MEASURED_ON_BUILD` and the
   check that exists to enforce it could disagree, and the only symptom was editing
   the constant — which is precisely what this phase did, and the function kept
   comparing against 6184 after the module said 6230. It now reads the constant at
   call time.

`scripts/control_ids.py` had a third, smaller one: `measured_identifiers()` looked
for a `"NNNN build"` string in upstream and returned `None` when it found none —
**asking upstream for a fact upstream does not keep.** There is no record anywhere in
`auto-trade` of the build its identifiers were measured on; that provenance lives in
*this* repository, backed by the probe report. The function now reads the identifiers
from `control_probe.EXPECTED_FIELDS` (where the order path itself reads them) and the
script says plainly where the build provenance lives, rather than performing a
cross-check against a value that was never there.

### What still needs a person

Nothing in the build gate. The upstream rule asks for a human to confirm each point
against the dialog; the probe produced that evidence and it is recorded, but the
confirmation is the operator's to make before turning execution on. Nothing in this
phase placed an order, and the two ad-hoc checks that assembled the live side forced
`dry_run=True` for exactly that reason.

### Two things noticed and deliberately not fixed

- **The audit log's file handle is never closed.** Assembling the live side and then
  deleting its log directory fails with `PermissionError ... audit.log`. It cost two
  throwaway scripts a confusing traceback at the end, and it would matter for
  `auto-trade`'s long-running `api` and `dashboard` commands, where a held handle
  blocks log rotation. It is upstream's `AuditLogger` and outside this project's
  boundary, so it is recorded rather than patched.
- **`build_bridge` is the safe path and `build_live` is not**, which is a distinction
  carried entirely by which function a caller reaches for. There is no runtime guard
  on the *default* configuration that would stop someone assembling the live side and
  then feeding it signals; the guards are the two environment variables, the demo
  check, and the kill switch. If execution is ever turned on, the thing to review
  first is whether that is still the shape you want.

---

## Completed Phases

- [x] **Phase 0** — Repository discovery and architecture audit
- [x] **Phase 1** — Project foundation
- [x] **Phase 2** — Al Brooks signal adapter
- [x] **Phase 3** — Stop, take-profit and validation policies
- [x] **Phase 4** — Risk management and position sizing
- [x] **Phase 5** — 1:1 risk/reward
- [x] **Phase 6** — Trade validation pipeline
- [x] **Phase 7** — MT5 data adapter and the `auto-trade` execution adapter, both **done**
- [x] Phase 8 — Dry run reporting
- [x] **Phase 9** — Idempotency, and the envelope that made it safe
- [x] **Phase 10** — End-to-end integration, and the composition root
- [x] Phase 11 — live side built; control ids re-measured on build 6230 (see Phase 13/14)
- [x] Phase 12 — CLI, public API, setup documentation
- [x] Phase 13 — Final architecture review
- [x] Phase 14 — armed the machine, placed the first real order
- [~] Phase 15 — root cause found; guard now **wired and unit-tested** (Phase 16); real-terminal verification still open
- [x] Phase 16 — repository hygiene, test portability, guard wiring fix, documentation sync

---

## What Phase 7 Built

```
adapters/mt5/
  __init__.py    the package docstring: the pattern, and what is not here yet
  bindings.py    MT5Bindings, MT5AccountInfo, MT5SymbolInfo   three Protocols
                 MT5Unavailable, load_bindings()   the one import site
  account.py     MT5AccountProvider    AccountProvider
  symbols.py     MT5SymbolSpecProvider SymbolSpecProvider
  positions.py   MT5PositionReader     the indicator's snapshot, on disk
tests/unit/
  test_mt5_data_adapter.py     48 tests, no terminal and no bindings imported
  test_mt5_position_reader.py  48 tests, incl. 3 opt-in against the live terminal
```

**Phase 7 is complete, and it was two halves.** The MT5 data adapter reads real
account facts; the execution adapter wraps `auto-trade`'s real workflow. The second
half was written against the source rather than against a transcription, because the
maintainer supplied the checkout at `D:\Projects\auto-trade` and it turned out to be
installed editable — so it is exercised against the real thing on every suite run.

### The gap Phase 7 opened, and then closed

Phase 7 first shipped `open_positions` hard-coded to zero, and documented why:
`account_info()` has no position count, so a configured `BRIDGE_MAX_OPEN_POSITIONS`
gate saw zero, admitted the trade, and the limit did nothing. **A gate that admits
a trade is the wrong direction to be wrong in.**

The maintainer then supplied the terminal path, the data folder, the fact that an
`AutoTradePositionReader` indicator was attached to EURUSD, and the `auto-trade`
checkout. Between them those closed the gap from the far end: **the terminal
already publishes the answer**, as JSON on disk, and the indicator's source is in
the upstream repository.

`adapters/mt5/positions.py` reads it. The contract below was **observed, not
designed** — from a live terminal and from `AutoTradePositionReader.mq5`:

```
<data path>\MQL5\Files\auto_trade_positions_a.json
<data path>\MQL5\Files\auto_trade_positions_b.json
```

**Three things this phase first got wrong by reasoning, and the source corrected.**

1. **The double buffer is not two files.** The writer picks `a` or `b` by
   `sequence % 2` and then **deletes the other one**, so in normal operation
   exactly *one* file exists. The two-file state appears only in the window
   between truncating the target and deleting its predecessor — and that window is
   the hazard the design contains, because `FileOpen` for writing truncates. So
   the guard is **parse failure** and the recovery is the other file. The first
   draft of this module described the protocol as "read both, take the higher
   sequence", which is right as a *reader strategy* and wrong as an explanation.
2. **`complete` is never false.** The indicator writes the literal `true`. The
   check is defensive against a future writer or a hand-edited file — the same
   reasoning `auto-trade`'s own reader gives — and the first draft implied it was
   a state a reader would meet.
3. **The per-position shape was a guess; now it is not.** `positions` was empty on
   this account, so the entry fields were unknowable, and guessing them would have
   produced a count that looked right and described the wrong thing. The writer and
   a captured real entry both give them: `ticket`, `symbol`, `type` (`BUY`/`SELL`),
   `volume`, `price_open`, `sl`, `tp`, `profit`, `magic`, `opened_at`. Only the
   first four are modelled — the four `auto-trade`'s verifier matches on — with the
   rest kept in `raw` and deliberately absent from the model, so nothing can come
   to depend on a field nobody has thought about.

**The 30-second staleness limit was arrived at independently and matches.** The
indicator writes once per second, so thirty seconds is thirty consecutive missed
writes; `MT5FilePositionSnapshotProvider` uses the same figure the same way. They
must agree or the same terminal looks alive to one reader and dead to the other,
so a test asserts the two constants are equal.

**One cross-check was added because the count could otherwise be confidently
wrong.** The snapshot names the account it was written for, and the account
provider now compares it against `account_info().login`. Two accounts on one
machine -- a stale data folder from another login, or two terminals sharing one
data path -- is ordinary, and the failure it produces is the worst kind: another
account's *flat* book is a zero on the concurrency gate, arriving through the
same code path as a computed answer. **This is the one reader outcome that raises
rather than falling back**, because "not known" and "known to be someone else's"
are different facts and only the first deserves a fallback.

**What remains open:** an unreadable snapshot still returns zero rather than
refusing, because `AccountBalance` cannot be built without a count and refusing
would make an unreadable *indicator* indistinguishable from an unreachable
*terminal* — two faults with different remedies. **Until that changes, an unset
`BRIDGE_MAX_OPEN_POSITIONS` remains the only correct configuration**, since it is
the only setting for which the zero cannot admit a trade.

### A cross-project security contract, found and pinned

`auto-trade`'s `TradeSignal` validates its id against
`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` because the id *"is used as a file name and
must not be able to name a path"*. `../../x` fails it.

That makes it a **security control on our side**: the execution project writes a
file named after the id the bridge supplies, so an id containing a path separator
would let a signal decide where it writes. Phase 2 chose `stb-<32 hex>`, which
satisfies the rule, and **nothing tested that it still did**. It is now asserted
in `test_signal_identity.py`, the pattern copied rather than imported because
`auto_trade` is private and may not be installed.

### The pattern, and why it makes this phase possible here

The bindings module is injected, so every branch is testable without MetaTrader 5
and no test file imports `MetaTrader5` — and the whole file runs on a machine that
does not have the bindings installed.

### Design decisions Phase 7 made

- **`import MetaTrader5` lives in one function,** `load_bindings`, called lazily.
  A test asserts it appears **exactly once** in the package and **not at module
  level** — a stray module-level import would pass every behavioural test and break
  the ones in files that do not inject the module.
- **The subset used is a `Protocol`,** not `Any`. The bindings ship no type
  information, so an untyped import would leave the boundary that converts
  untyped data into typed domain objects itself untyped.
- **The terminal path is a parameter,** never a constant, so no machine's path is
  committed. `MT5PositionReader` refuses to be constructed without a data path for
  the same reason — a default here would be exactly the `AUTO_TRADE_DATA_PATH`
  mistake the Phase 0 audit recorded, and that key's default still points at a
  *different machine's* terminal hash.
- **Nothing launches the terminal.** A test walks the AST and fails on any
  `launch` attribute. `initialize` connects; `launch` starts a process, and a
  process that launches a trading terminal is a process that can launch it by
  accident — and one that launches it without a login is worse.
- **A missing fact raises. It never falls back.** An unreachable terminal, an
  account that answers `None`, an unknown symbol, an unreadable snapshot — all
  raise `MT5Unavailable`.
- **`MT5Unavailable` is a `RuntimeError`, not a domain error.** By the time it is
  raised the bridge is outside the domain: its error vocabulary describes *trading
  refusals*, and "the terminal is not running" is a fact about the machine, not a
  verdict on a trade.
- **Floats become exact decimals via `Decimal(str(value))`,** never
  `Decimal(value)`. The second form copies the binary double's exact value, so a
  `tick_size` of `0.00001` arrives as `0.0000100000000000000002092251` and every
  tick count from it is slightly wrong in a way that looks entirely right.
- **JSON floats are parsed straight to `Decimal`** via `parse_float`. Going through
  `float` would round-trip `volume` through a binary double, and the execution
  verifier matches on exact `Decimal` volume equality — so drift there fails the
  verification of a trade that succeeded.
- **A missing field raises and names itself** rather than becoming zero. A zero
  tick size would be caught downstream as a *sizing* refusal, which points an
  operator at the arithmetic instead of at the terminal that failed to answer.
- **A malformed position entry refuses the whole snapshot** rather than being
  skipped. Skipping under-counts, and an under-count admits a trade the concurrency
  gate should have refused.
- **An empty `currency_profit` is passed through, not raised.** The deliberate
  asymmetry: an unstated profit currency is a fact the *domain* already refuses on,
  so the adapter reports it and stops. Refusing here too would be right for the
  wrong reason, and would move a policy decision into a converter.
- **No cache.** A specification does not change within a session, but a cache is a
  cache that can go stale across a broker changing a contract mid-session, and a
  re-read costs one local IPC call.
- **The two snapshot file names are written out, not globbed.** A glob would
  silently start reading a third file if the upstream project ever adds one, and a
  reader that picks up an unexpected file is a reader nobody reasoned about.

### What Phase 7b Built

```
adapters/auto_trade/
  __init__.py    the package docstring: what is wrapped, and what is refused
  bindings.py    AutoTradeBindings, AutoTradeUnavailable, load_bindings()
                 the one import site; eight upstream names, declared
  executor.py    AutoTradeExecutor    TradeExecutor
                 SENTINEL_PROFILE     the stand-in for a field upstream never reads
adapters/fake/
  executor.py    FakeTradeExecutor    the other TradeExecutor
tests/unit/
  test_auto_trade_executor.py  50 tests
```

**The executor is built, and it is tested against the real thing rather than a
transcription.** The maintainer supplied `D:\Projects\auto-trade`, and it turned
out to be installed editable -- so `TestTheRealPackageIfItIsInstalled` runs the
real `ExecutionWorkflow`, the real `RiskEngine`, a real `JsonExecutionLedger` and a
real `DryRunTerminalAdapter` on every suite run. No terminal is opened and no
control can be reached, which is asserted rather than assumed, because that
assumption *is* the safety model.

**It is a wrapper, not a second execution layer.** `ExecutionWorkflow` already
owns the state machine, the ledger ordering, the kill-switch check, the
`demo_only` policy, and the classification of a failed click as `UNKNOWN`. Those
are the mechanisms standing between a decided trade and a duplicate position.
Re-implementing any of them would be a second opinion about whether a trade
happened -- which is the exact shape of work Phases 4 and 5 found real bugs in.
So the mapping is thin and two-way:

```
ExecutionRequest  ->  TradeSignal        one direction enum, four numbers
ExecutionResult   ->  ExecutionResult    one status, one ticket
```

### Four decisions in the mapping, and why each one is pinned by a test

**The evidence score is not sent as `confidence`.** `TradeSignal.confidence` is
typed and validated as a probability in `0..1`. The bridge's `evidence_score` is
explicitly *not* one -- Phase 2 established that. It travels in `metadata` under
`bridge_evidence_score` instead. The strongest form of the test sends a score of
`4.2`: if the adapter ever starts passing it through, the double's own `0..1`
check rejects it and the test fails naming the regression. Rule 9 exists to
prevent exactly this misreading, and the adapter is where it would have been
committed silently.

**`CLOSED` maps to `UNKNOWN`, never to `REJECTED`.** Upstream has six statuses
and the bridge knows five. A closed position means the order *was* placed and later
closed, so re-sending is the duplicate rule 5 forbids -- whereas `REJECTED` says
"definitely not placed", which is a licence to retry. `UNKNOWN` is the only honest
answer. The bridge never closes, so this should never arrive; it is handled rather
than ignored because a status from nowhere is the signal that something upstream
changed.

**`execute` is not exception-free, and that shaped the whole `submit`.**
Upstream's `except Exception` around the click deliberately *re-raises*
`ExecutionUnknownError`, so an adapter assuming a return value would crash on the
one outcome that most needs a record -- and the crash would take the record with
it. `submit` catches broadly and maps everything to `UNKNOWN`: not retried,
escalated, recorded. The test drives a real `UnknownExecutionError` and asserts
`is_retryable is False`.

**`executed_price` is always `None`.** Upstream's `VerificationEvidence` carries
`baseline`, `observed` and `position_id` -- references to the snapshots it
compared -- and no fill price. A click is not a fill, which is why
`execute_order` returns `REQUESTED` and never `ACCEPTED`. So there was never a
price to report, and putting the requested price there would be the most
confident-looking fabrication in the adapter.

### Three things the source corrected that reasoning alone would have got wrong

- **`profile` is required and never read.** The field appears once in the whole
  315-line class, in the assignment, and upstream's own tests pass
  `type("Profile", (), {})()` -- an object with no attributes at all. The bridge
  passes a named `SENTINEL_PROFILE` for the same reason: inventing
  plausible-looking terminal attributes would be asserting facts about the machine
  that nothing has established.
- **There is no factory.** No `build_workflow`, no `create_workflow`; the only
  assembly upstream is two private CLI functions. So the bridge's composition root
  must build the workflow, which is *why* `AutoTradeExecutor` takes an
  already-assembled one and refuses to construct its own -- a test walks the AST
  for an `ExecutionWorkflow(...)` call and fails on it.
- **A dry run has two independent guards,** not one: the workflow refuses to call
  `execute_order` at all (`workflow.py:102-110`), *and* `prepare_order` closes the
  dialog when the gate says dry-run (`window_manager.py:644-645`). The CLI passes
  `ExecutionGate(enabled=False)` regardless of configuration. The bridge keeps no
  way to reach either guard: a test fails if the adapter module references
  `MT5DesktopAdapter`, `ExecutionGate` or `CloseGate` **by name** -- checked over
  the AST, because both appear in these docstrings explaining why they must not be
  used, and a text search would fail on the documentation of the rule it enforces.

### What Phase 7b deliberately did not do

**It is not wired into `ProcessSignal`, and that test still passes on purpose.**
Execution arrives with the ledger and the kill switch around it, not before.
Wiring an executor in while the idempotency store is absent is precisely the
failure the architecture was drawn to prevent.

**One bug this phase's own tests found, which is the argument for having them.**
`load_bindings()` was initially called *outside* the `try` that converts a
translation failure into an `UNKNOWN` result. So on a machine without the
checkout, a missing package raised out of `submit` -- and an exception out of
`submit` loses the trade record, on exactly the situation where an operator most
needs to see which signal did not go out. The fix moved the load inside the
`try`; the test that catches it simulates the `ImportError` rather than relying on
the package being absent, because a test that only passes because a dependency is
missing stops testing the moment somebody installs it.

### One design choice that was rejected

`AutoTradeBindings` originally verified the upstream subset in `__post_init__`.
That is wrong: the check imports the real package, so a dataclass that imports on
construction cannot be built from doubles -- which made the mapping untestable on
exactly the machine where it most needs testing. `verify()` is now explicit and
`load_bindings()` calls it, so the guarantee holds on the path that matters and the
suite stays runnable without the checkout.

### What Phase 8 Built

```
application/
  dry_run.py     DryRunReport, DownstreamVerdict
                 build_execution_request(), report_for()
adapters/auto_trade/
  preflight.py   DownstreamLimits, ask_downstream_risk()
tests/unit/
  test_dry_run.py  39 tests
```

**Phase 6 made `DRY_RUN` an honest placeholder. This phase makes it an answer.**
"Every check passed, nothing was sent, and nothing could have been" is true and it is
not much use: a dry run exists to answer one question before real money is involved,
and the question is *what exactly would this system do?*

### The missing link, and it was genuinely missing

`ExecutionRequest` — the type the executor accepts — was **never constructed anywhere
in this repository** before this phase. The whole mapping from a sized intent to an
order existed only as a hand-written fixture in the executor's tests. So the boundary
the execution project is handed had no producer outside a test, and nothing in
`src/` had ever exercised the numbers a real trade would carry.

:func:`build_execution_request` is that producer, and it **copies every number rather
than recomputing any**. A dry run that re-derived the volume or the stop would be a
second sizing pass, and two passes that disagree produce a report describing an order
the bridge would never send — which is worse than no report, because it looks
authoritative.

### The disagreement this phase was actually about

The bridge validates against its own rules and `auto-trade` validates against its
own rules again. **The two rulebooks disagree by default:**

| Check | Owner | Default |
|---|---|---|
| risk per trade | this bridge | 0.5% of balance |
| minimum volume | this bridge | refuse below broker minimum |
| allowed symbols | `auto-trade` | `EURUSD,XAUUSD,YM` |
| maximum volume | `auto-trade` | `1.0` |
| orders per minute | `auto-trade` | `5` |

So **a signal on `GBPJPY` passes every check in this bridge and is refused
downstream.** Same for a 2.0-lot order, which the 0.5% rule will cheerfully compute on
a large balance and `auto-trade`'s absolute cap will refuse however well sized it is.

Before this phase the only place any of that became visible was a `REJECTED` result
on a live account: in production, on real money, with the reason arriving as a
downstream result rather than as a decision. The dry run now asks `auto-trade`'s own
`RiskEngine` — a pure function of the request and the configured limits — and reports
the answer as a blocker with the reason verbatim.

### Three properties of that answer, each enforced by a test

- **It is a prediction, and the type says so.** `DownstreamVerdict` carries
  `evaluated`, because the real gates also depend on the account type, the kill
  switch, the ledger and the terminal window. **An unevaluated gate reporting as
  accepted is a construction error, not a normalisation** — `accepted=True` with
  `evaluated=False` raises, because an unevaluated gate reporting as accepted is
  indistinguishable downstream from one that ran and passed.
- **No engine wired means `evaluated=False`, never `accepted=True`.** A missing
  package, a renamed class and a bug all look identical from here, and all three mean
  the check did not happen. That is the most expensive possible silence: a green tick
  on the one gate that would have refused the trade.
- **The report names every blocker, not the first.** An operator fixing them one at a
  time, each fix revealing the next, is the slowest way to answer "can this trade ever
  run here?".

### What this module still may not do

**It holds no `TradeExecutor`.** Not an import, not a type hint, not a call — and
that is awkward, because an executor exists one package away and is perfectly usable.
`test_this_module_holds_no_executor` asserts it, and so do three tests in
`test_dry_run.py`: the dry run imports none, and `preflight.py` references neither
the executor nor `MT5DesktopAdapter`, `ExecutionGate` or `ExecutionWorkflow`.

The checks are over the AST rather than the raw source, because all four names appear
in `preflight.py`'s own docstrings explaining precisely why it may not use them. A
text search would fail on the documentation of the rule it enforces — and a structural
test that fires on correct code gets deleted rather than fixed.

`preflight.py` also constructs the `TradeSignal` itself rather than reaching through
the executor for one. The duplication is deliberate (two tables, one test asserting
they agree) because the alternative is inheriting the executor's ability to send.

### One known gap, deliberately not worked around

`ExecutionRequest.take_profit` is **required**; `TradeIntent.take_profit` is optional
and so is `auto-trade`'s `TradeSignal.take_profit`. `TakeProfitSource.NONE` is a
supported, documented, tested configuration, so **this combination is reachable in
production and currently cannot be expressed.**

Two options were considered. Making the boundary field optional is correct and is
**Phase 9's work**; refusing the trade is the fail-closed direction but means an
operator who deliberately disabled targets gets every trade refused at the last step
with a message about a model. Rather than invent a second code path, the refusal
names the gap and says which phase fixes it. The report still exists and still carries
the arithmetic.

### A bug this phase's own tests caught, and one they prevented

`_ORDER_ACTIONS` in `preflight.py` was written as an **empty dict** on the first pass
— the direction mapping the engine checks `symbol` and `volume` against would have
been missing entirely. The type annotation was a `dict` so nothing complained, and the
first test to run it failed. It is now populated, and
`test_the_two_direction_tables_agree` keeps the two copies from drifting.

The second is not a bug but a rule: the file's most important property is an
*absence*, and the first draft of the structural test was a text search that would
have failed on the correct code.

### What Phase 9 Built

```
application/
  execution_envelope.py  ExecutionEnvelope, build_execution_envelope()
adapters/auto_trade/
  ledger.py        AutoTradeLedger, open_ledger()   the two-phase port, translated
domain/
  models.py        ExecutionRequest.take_profit is now optional
ports/
  __init__.py      IdempotencyStore is two-phase, not one-shot
tests/unit/
  test_execution_envelope.py  33 tests, incl. 8 against the real JSON ledger
  test_execution_wiring.py     27 tests
  test_signal_identity.py      +5, the collapse this phase found
```

**Phase 9 did one structural thing and it is the thing that mattered: it turned an
absence into a type.**

For three phases the rule was "the pipeline must not import an executor", enforced by
a test reading a source file. That test did its job — and it had to be retired,
because **an absence cannot also authorise.** It could forbid the mistake and say
nothing about whether the right thing was in place, so Phase 9 could not say "yes,
and here is why that is safe."

### The envelope

`ExecutionEnvelope` has **no public constructor.** Its `__init__` exists only to
refuse, the three fields are set through `object.__setattr__` by
`build_execution_envelope(executor, idempotency, kill_switch)`, and every field is
required. `ProcessSignal.wire_execution` takes the envelope and nothing else, so
there is no way to hand the pipeline an executor without the ledger beside it.

Four checks in the factory, each a case where the object *looks* complete and is
not. The one worth naming: **the same object passed as two collaborators is
refused.** A stub satisfying both the executor and the ledger would pass a naive
"all three present" check while recording nothing and stopping nothing — which is
worse than an obviously incomplete envelope, because it looks complete.

*(One detail recorded because it cost time: `slots=True` cannot be combined with
`object.__new__` plus `object.__setattr__` — it raises. So this is a plain class with
read-only properties and a refusing `__setattr__`, not the tidier frozen dataclass.)*

### The ledger, and the two properties that make it worth wrapping

Upstream's `JsonExecutionLedger` already guarantees what this project needs, so the
adapter translates three calls and adds no behaviour. Read from
`application/ledger.py`, because none of it is obvious from the interface:

1. **the write is atomic** — a `.tmp` sibling then `replace()`, so a crash mid-write
   leaves the previous file rather than a truncated one;
2. **the attempt is recorded before the click** — upstream calls `record_attempt`
   *before* `adapter.execute_order`, so a process that dies between them leaves a
   pending record an operator can settle rather than no record at all;
3. **a second attempt cannot take over the first one's record** — `record_result`
   returns silently on a mismatched `execution_id`.

And the fourth, which is the one the adapter exists to preserve:

4. **an unreadable ledger is a refusal, not an empty one.** Upstream's `_read`
   raises for a corrupt or wrongly-shaped file, and `open_ledger` does not catch it.
   **A ledger that silently read as empty would answer "no, this signal has not been
   acted on" for every signal ever recorded** — a green light on a duplicate. Every
   failure in `AutoTradeLedger` raises `AutoTradeUnavailable`, and all of them carry
   one message, because an unreadable ledger cannot answer *any* question and three
   messages differing in a noun would read as three different faults.

### The port had to change shape, and Phase 2's key had a hole

**`IdempotencyStore.record(key, mapping)` became `record_attempt` + `record_outcome`.**
The single-call shape cannot express the two-phase write above, and the collapse is
invisible until the first crash — at which point the ledger says the signal was never
attempted and the trade is free to repeat. `execution_id` ties the halves together and
is required on both.

**The `signal_id` key can collapse two trades into one**, and the handoff asked Phase 9
to find out. It could not check against real engine output — `albrooks` is not
installed here — so it checked what the key is built from instead:

```
_bar_index(result)  ->  result.last_closed_bar, or -1
_bar_time(result)   ->  None unless a bar feature matches that index
```

Both fallbacks are **permitted**: ordinary attribute lookups, nothing raised, nothing
logged. And `Signal.bar_index` **defaults to `-1` on the model itself**, so the
mapper's fallback and the model's default agree. A signal carrying `bar_index=-1` and
`bar_time=None` produces a key with **no bar in it at all** — and the bar is the whole
unit of identity. Two such readings hash to the same value, so the second is refused
as a duplicate even though it is a different trade.

The blast radius is bounded, and the tests say by how much: different symbols do not
collide, different setups do not collide, and either `bar_index` **or** `bar_time`
alone is enough to separate. It takes both being absent. That makes it a serious
defect on one symbol and one timeframe, not a systemic one — and it is the failure the
ledger exists to prevent, arriving *through* the ledger.

**Not fixed here**, because the fix is a decision about what identity means when the
engine reports no bar, and Phase 10 is where a composition root exists to make it.
The options are refusing the signal (fail-closed, and correct), or folding the
reading's own timestamp in — which would make every re-read of the same unbarred
reading a new trade, which is the opposite defect.

### The retryable / ledger disagreement, written down rather than resolved

`ExecutionResult.is_retryable` is `True` for a clean rejection. Upstream's ledger
disagrees: `contains()` is a plain key lookup, so an entry written for a `REJECTED`
still matches and the same signal id is refused with `"duplicate signal id"`.

**Both are true and they are about different moments**, so neither was changed.
`is_retryable` describes what a caller may do with a result it already holds;
`contains` describes what a *new* attempt would meet. But an operator who retries on
`is_retryable` will be refused, and pretending otherwise would be worse than the
refusal — so it is in the port's docstring, in `docs/risk-management.md`, and here.

### What Phase 10 Built

```
composition.py            Bridge, CompositionRefusal, build_bridge()
adapters/albrooks/
  source.py                _guard_bar_identity(), _newest_bar()
domain/enums.py            SIGNAL_BAR_UNKNOWN
tests/unit/
  test_composition.py           31 tests
  test_signal_bar_identity.py   16 tests
```

**Nine phases built the pieces and every one of them was tested against a
stand-in. This is the first place they are wired to each other** -- and the three
things only an assembly can find are: a wiring that is wrong while every individual
component passes, the default configuration's inability to execute *behaviourally*
rather than as a flag someone reads, and whether the refusals compose.

### The `signal_id` collapse is closed, and it is a repair rather than a refusal

Phase 9 left the decision to here. The answer is better than refusing: the bar is
**knowable at exactly one place** -- the adapter that read the bars and asked the
engine to analyse them -- and at that point the fallback bars are still in hand.

So `_guard_bar_identity` does not refuse. It recovers:

```
engine reported a bar (index >= 0 OR time is not None)  ->  left exactly as it was
neither, and the series has them                        ->  the series answers
neither, and the series has neither                     ->  SIGNAL_BAR_UNKNOWN
```

**"Left exactly as it was" includes the mixed case.** A signal with
`bar_index=-1` and a real `bar_time` is *not* repaired, because either field alone
separates two readings and repairing the index would mix the engine's authority with
ours in one key. When the engine said something, it wins; the recovery only fills a
total blank.

The index is the **position from the end**, not from the start, because the engine
indexes that way and the two must agree -- a recovery that disagreed would put the
reading on a bar the engine never saw, which is worse than refusing.

### The composition root, and why the live path is refused rather than degraded

`build_bridge` assembles the MT5 providers, the position reader, the ledger, the
preflight and the pipeline, and **refuses the live path outright**:

> execution is enabled and dry-run is off, so this bridge would place real orders --
> but the live execution side is assembled by Phase 11 and not yet by this module.
> Refusing rather than degrading to a dry run, because a silent degradation is how an
> operator's setting gets ignored.

That sentence is the phase. An operator who enables execution today is told so,
rather than handed a pipeline that quietly reports and looks like it obeyed.

**The ledger is opened before anything that could write**, and refused rather than
replaced. ``JsonExecutionLedger`` raises on a file it cannot read, and an unreadable
ledger must stop the process while there is still nothing to undo. It is opened even
on a dry run, deliberately: a dry run is exactly when somebody wants to know the
ledger is readable, and finding out at the first real order is the worst time.

### What the composition root deliberately does not assemble

* **The terminal adapter and the ``ExecutionWorkflow`` with it.** Both need the
  private execution package, and making a *dry run* depend on it would mean a missing
  dependency silently disables reporting rather than trading. The two are independent
  and conflating them is the mistake.
* **The signal source.** The bridge reads signals; choosing them would be a strategy
  in disguise.
* **A CLI.** ``cli/`` stays empty until Phase 12.

### Two things this phase's tests found, both about assumptions

**The stub was shaped like the wrong thing.** The first version of this file's
bindings double carried the *bridge's* ``SymbolSpec`` fields, and every test failed
with ``SYMBOL_SPEC_UNAVAILABLE``. The adapter maps ``trade_contract_size`` and the
rest one for one, so a double carrying ``contract_size`` tests a conversion that does
not happen. **That failure is the argument for this file existing**: every other test
drives a real adapter with a correct double, and only an assembly exercises the seam
where a mis-shaped double looks like a broken adapter.

**The preflight cannot show a disagreement through the composition root.** The root
builds the preflight's limits from the *bridge's* ``allowed_symbols``, so the two
projects agree by construction -- deliberately, since a root that configured the
downstream limits independently would be a second policy nobody maintains. The test
that claimed to prove a `GBPJPY` refusal was therefore asserting something untrue,
and was corrected to state the limitation rather than kept for its green tick. **The
genuine disagreement tests live in ``test_dry_run.py``, where the caller supplies the
limits.**

### The concurrency limit, seen through the composition

Phase 7's residual case is now visible from the outside. Without a data path the
count is zero, so a limit of **one** admits a trade on a number that was never read --
and the report still says the count was not read. With a limit of **zero** the trade
is refused at the concurrency gate. And with a *dead terminal* the refusal moves
earlier, to the account gate, as soon as the limit is configured. Three cases, three
different refusals, and the safe configuration -- leave the limit unset -- is stated
in `docs/risk-management.md` rather than discovered.

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
terminal title bar   10000001 - Alpari-MT5-Demo: Demo Account - Hedge - Alpari - [USDInd,H1]
account_info().login  10000001
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

* the terminal is running and logged in as `10000001`
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
`account: 10000001` — the demo target Q3 asks for, and no real money is in reach.


---

## What Phase 6 Built

```
application/
  process_signal.py  ProcessSignal.process(signal) -> TradeDecision
                     STAGES, PASSED, budget_to_account
  risk_service.py     budget_for() / size_for()   the resolve halves, added here
tests/unit/
  test_process_signal.py   50 tests: the order, the snapshot, the refusals
```

**This is the phase five others were deferring.** Every stage existed and was
tested; nothing in `src/` had ever called two of them in sequence. A signal
arriving today went nowhere, not because a piece was missing but because no
function connected the pieces.

### What it does

```
signal → validate_signal → resolve_stop → resolve_take_profit → validate_geometry
       → validate_policy → validate_against_spec → budget → position size
       → TradeIntent → TradeDecision
```

The order is the design, and it is specified rather than incidental: cheap and
decisive before expensive and arithmetic. Signal validity costs nothing and
rejects the commonest outcome in the system; the stop comes next because without
one there is nothing to size against; geometry precedes policy because a malformed
trade is not a policy question; and the account and specification come **last**,
because they are the only stages that reach outside the process — so a signal
with no stop costs no round trip to a terminal. There are tests for that
specifically, not only for the refusals.

### Design decisions Phase 6 made

- **It stops at the first refusal.** Running a later check on a signal that has
  already failed produces arithmetic on meaningless inputs — a position size
  computed from a stop that does not exist is arithmetically fine and completely
  wrong, which is the failure mode this project keeps running into. `diagnostics`
  names the stage that refused, because "no trade" without "which check" is an
  answer nobody can act on.
- **Every outcome is a `TradeDecision`, never an exception.** An abstention, a
  malformed signal and an unreachable terminal are ordinary outcomes, and the
  commonest one is "the engine found nothing to trade". A loop that died on any
  of them would stop processing the signals it could still act on.
- **A passing trade is `DRY_RUN`, never `EXECUTE`,** and that is not a
  placeholder. There is no executor wired in, so `EXECUTE` would be a lie; and
  with the defaults the bridge cannot execute anyway. A trade that passed every
  check and sent nothing must never be able to read as a fill, or a log becomes
  evidence of an order that does not exist. **A test asserts the module imports no
  `TradeExecutor`, `IdempotencyStore` or `KillSwitch`** — an execution path that
  appeared before Phase 7 would be one without the kill switch, the idempotency
  ledger and the audit log around it, which is the failure the architecture exists
  to prevent. The constraint is structural, not left to review.
- **The account and the specification are read once each.** The same account read
  feeds the concurrency gate at stage 5 and the budget at stage 7, because two
  reads can straddle a close — and then the open-position count that admitted the
  trade would not be the count that sized it. This is why `RiskService` grew a
  resolve half (`budget_for`, `size_for`) alongside its read-and-resolve
  convenience methods: the pipeline reads, the service resolves. The old
  single-call methods are kept and are now three-line wrappers.
- **A concurrency limit that cannot be checked is refused, not skipped.** If
  `max_open_positions` is set and the terminal does not answer, the trade is
  refused. A limit that quietly stops applying when the account is unreachable is
  a risk control that switches off exactly when it is most needed — and with no
  limit configured the unreadable account is *not* fatal at that stage, because
  there is no gate to evaluate; it refuses later at sizing, which is a different
  moment and a different remedy.
- **It uses geometry's validated pair from stage 4 onwards,** not the two values it
  passed in. They are the same objects today; the difference is the point. The
  stage exists to say "these two agree", and continuing to use the un-validated
  copies would make it a function whose answer is ignored, which is how a check
  stops being one.
- **The clock is injected,** so a test can pin "now" and get an identical decision
  twice. Phase 9's idempotency ledger depends on that determinism and cannot
  provide it.
- **`STOP_RESOLVED` and `TAKE_PROFIT_RESOLVED` are finally emitted** — two events
  reserved in Phase 1 and still un-emitted after Phases 3 and 4, because Phase 4
  established that the domain has no logger and must not acquire one. This is the
  first layer that may hold one. `TAKE_PROFIT_RESOLVED` fires under
  `TakeProfitSource.NONE` too: "a trade with no target because the operator
  disabled targets" is a decision somebody will want to find in a log.

### A finding worth recording

**The geometry stage cannot refuse a signal that reaches it.** Every condition
`validate_geometry` checks has already been checked by an earlier stage on the
same objects — `resolve_stop` verified the stop price, its distance and its side,
and `resolve_take_profit` verified the target's side, both building their results
through models that refuse a non-positive distance at construction.

It is kept, because the Phase 0 rationale still holds: the two functions have
different callers, the geometry can come from configuration as well as from a
signal, and a check that trusts its input to have been verified elsewhere fails
the first time somebody calls it directly. But the honest position is recorded in
`process_signal.py` and pinned by a test, rather than left for a reader to work
out from the call sequence — a stage in the pipeline that can never fire is
either dead code or a bug, and the code cannot tell you which.

The consequence for coverage: `process_signal.py` is at **99%**, the one uncovered
line being that refusal. The companion refusal — an incomplete specification, at
the `spec` stage — *is* reachable and *is* tested, because a specification arrives
from outside the process and may have been deserialised by someone else's code,
which is a real difference from the stop and the take profit.

---

## What Phase 5 Built

```
models.py           canonical_ratio()          one ratio, one representation
                    achieved_ratio()           the single ratio implementation
                    RiskParameters.minimum_reward_risk_ratio   the opt-in floor
                    RiskBudget invariant       reward_amount must match amount*ratio
                    TradeIntent.take_profit    now optional (NONE is supported)
take_profit.py      signal_target_ratio()      the ratio a signal's target implies
                    resolve_take_profit        delegates all ratio arithmetic
configuration/      _require_decimal()         a configured 0 is no longer swallowed
tests/unit/
  test_reward_risk_ratio.py   44 tests, the floor, the reporting and the wiring
```

### The finding that made this phase worth doing

Phase 4's handoff described Phase 5 as *"largely already implemented — verify the
wiring and add the missing coverage, not build a second mechanism"*, and that was
**wrong about the first half**. A full audit of the chain — env → config →
`RiskParameters` → `resolve_take_profit` → `RiskBudget` → `TradeIntent` — found
that under the **default** policy the configured 1:1 was not a floor at all.
Nothing anywhere compared the signal's implied ratio against anything. A
structurally sound engine target at **0.2:1** was accepted silently, and the only
trace was an `achieved_ratio` field in a log nobody was reading.

The resolver's own docstring said the precise thing — *"use the target when
structurally defensible"* — and the interpretation paragraph two lines below said
*"I want 1:1 as the floor"*. Two descriptions, one implementation, and nobody
noticed for three phases because the code did exactly what its primary docstring
said.

**This was escalated to the maintainer rather than decided**, because the fix
changes trading behaviour. Three options were put: make the configured ratio an
implicit floor (silently changes the default policy), add an opt-in floor, or
change nothing. **The opt-in floor was chosen** — a new
`BRIDGE_MINIMUM_REWARD_RISK_RATIO`, default unset, meaning `RR_FALLBACK` behaves
exactly as it did. Turning it on replaces engine-measured targets with the
configured distance, which is a trading decision nobody should have made for
them by upgrading a config file's meaning.

**The unresolved half is not Phase 5's to close.** Whether the *default* should
have a floor is still an open question, and it is recorded as one below rather
than quietly resolved in either direction.

### Bugs Phase 5 found

Three, plus one dead guard removed. All fixed.

1. **`config.py` swallowed a configured `0` for both money settings.**
   `risk_percent=_decimal(...) or Decimal("0.5")` and the same for
   `reward_risk_ratio`. `Decimal("0")` is falsy, so `BRIDGE_RISK_PERCENT=0`
   started the bridge at **0.5%** and `BRIDGE_REWARD_RISK_RATIO=0` at **1:1**,
   after the operator had asked for neither. Phase 1 had already identified this
   exact idiom as a bug and fixed it for `BAR_COUNT` (`_require_int`), and left
   it in the two fields where it costs money. Now `_require_decimal`.
2. **`achieved_ratio` was absent under `TakeProfitSource.NONE`.** The key simply
   did not exist on that path, so `details["achieved_ratio"]` raised `KeyError`
   on a resolution that had **already succeeded** — the worst moment for a
   missing key, because the surrounding code has decided the trade is fine. Now
   present and `None`, which says "there is no target" rather than "0:1".
3. **`TradeIntent` could not represent `NONE` at all.** `take_profit` was
   mandatory while the take-profit resolver correctly returns `None` for that
   policy, and `to_dict()` evaluated `reward_to_risk` unconditionally. A
   supported, documented, tested configuration made the decision object
   **unconstructible**. Whoever wrote Phase 6 would have hit this and plausibly
   resolved it by refusing trades an operator had deliberately enabled — a silent
   policy change wearing the costume of a type error.
4. **The same ratio was computed in five places.** Four inline
   `distance / stop.distance` divisions in `resolve_take_profit`, plus
   `TradeIntent.reward_to_risk`. Now one `achieved_ratio()` in `models.py`, with
   an AST test asserting the resolver contains **no division at all** — the
   enforcement being that a fifth copy cannot quietly appear.

Also: **`_MIN_RATIO` was deleted.** A module constant in `take_profit.py` whose
docstring explained that a "minimum-ratio clamp" protected the target
computation — and which nothing referenced. A guard that cannot fire is worse
than no guard: it reads as though it handles a case it does not, and a
maintainer trusting that docstring would believe a clamp existed.

### A reporting inconsistency worth naming

The same ratio was written to the log as `"1"` or `"1.0"` depending on which
arithmetic produced it, because `Decimal` carries its own exponent. A log query
for one missed the other. `canonical_ratio()` normalises it — **with a guard**,
because `Decimal("100").normalize()` is `1E+2` and a 100:1 configuration would
have been logged in exponent notation. Only the representation changes; the
numeric value is untouched.

### Design decisions Phase 5 made

- **The floor is opt-in, and off by default.** See above. It filters the signal's
  target only — under `RR_DERIVED` there is nothing to filter, which is what that
  policy is for, and a test pins the interaction so it is deliberate.
- **Under `SIGNAL` a below-floor target refuses the trade**, rather than being
  replaced by the ratio. There is no fallback under the strict policy, and
  silently substituting a different exit plan for the one the trader demanded
  would be the worst version of the feature. The asymmetry between the two
  policies is intentional and tested.
- **The floor is checked *last*,** after presence, side and basis. A target wrong
  on two grounds reports the one with the upstream remedy: a volatility-multiple
  basis is the engine's own defect; a poor ratio is this bridge's opinion about
  it.
- **`reward_amount` is now an enforced invariant of `RiskBudget`,** not a field
  that happens to be derived. Nothing consumed it, so an inconsistent pair would
  have passed every test while the decision log advertised a planned gain the
  bridge never aimed for. Compared numerically, not as text — `50` and `50.00`
  are the same money, and `amount * ratio` preserves whatever exponent the
  operands had.
- **The upstream engine's `reward_to_risk` is deliberately discarded**, and this
  is now documented rather than merely true. The engine ranks candidates partly
  on it, so a decision record cannot say *why* the engine chose a candidate. The
  engine documents its own targets as unvalidated, and the bridge computes the
  same ratio from distances it has already validated. The cost is one line of
  diagnostic detail; the benefit is provenance.

---

## What Phase 4 Built

```
domain/
  risk.py       resolve_risk_budget()         the risk amount, and its refusals
                check_currency_compatibility() can the money be divided at all
  sizing.py     resolve_position_size()       the tick-value sizer
                check_broker_constraints()   min / max / step, as their own policy
models.py       RiskBudget                    the new value object
application/
  risk_service.py  RiskService               obtains the facts, emits the events
adapters/fake/  account.py  FakeAccountProvider
                symbols.py  FakeSymbolSpecProvider + eurusd/gold/unusual specs
tests/unit/
  test_risk_budget.py        35 tests
  test_position_sizing.py    42 tests
  test_risk_service.py       36 tests
docs/risk-management.md      rewritten: steps 7 and 8 are now documented
```

`RiskBudget` is the twelfth value object, and it exists for the same reason
`PositionSize` keeps every input: "risk $50" cannot be checked against anything,
while "0.5% of a $10,000 balance" can be recomputed by hand.

### The bug Phase 4 found

One, and it is the most consequential bug this project has had. **It was in
`SymbolSpec.conservative_tick_value` from Phase 1, and it under-sized every
position on an instrument whose two tick values differ.**

Phase 1 implemented the "worse of the two" as `min(profit, loss)`, on the stated
reasoning that *"a size that is safe on paper has to be safe on the losing side."*
The conclusion was right and the implementation was its mirror image, because

```
volume = risk_amount / (ticks × tick_value)
```

so **volume is inversely proportional to the tick value**. The *smaller* tick
value gives the *larger* position, which makes `min` the least conservative
choice available. On a hedging symbol reporting $1 a tick in profit and $2 in
loss, a $50 budget over a 300-tick stop came out at **0.16 lots** — and if that
stop were hit the loss would be `300 × $2 × 0.16 = $96`, **twice the budget**.
The arithmetic downstream was correct throughout; the divisor was wrong.

Now `max(...)`, which bounds the loss from above in both directions. Two Phase 1
tests that pinned the old behaviour were corrected rather than deleted, with the
arithmetic in the comment, because the reasoning they encoded is exactly what a
future contributor would otherwise re-invert.

**Why Phase 4 found it and Phase 3 did not:** Phase 3 never used the property.
It sat on a model, exercised by two unit tests that asserted the value the
implementation produced — which is the shape of a test that confirms rather than
checks. It only became visible once a sizer divided by it and the consequence
could be counted in lots.

Recorded because it is a failure mode a later phase could reintroduce: *a test
written against what the code does is not a test of what the code should do.*

### Design decisions Phase 4 made

- **The conservative tick value is a bound, not a prediction.** The exact
  per-direction value is knowable (`tick_value_loss` for a long, `tick_value_profit`
  for a short) and would size both sides optimally. It was rejected: it makes the
  size depend on direction, which means two more code paths, two more
  combinations, and a way for a long to be sized with a short's number.
- **Currency coherence is checked before any division, not after.** MT5 quotes
  `tick_value_profit` in the account currency; dividing dollars by euros produces
  a number that looks exactly like a volume and is not one. An *unstated* profit
  currency is refused rather than assumed — a check that passes on missing data
  is not a check, and the Phase 7 adapter always populates the field. The
  **margin** currency is recorded but not refused: EURUSD margin in EUR on a USD
  account is normal, and refusing it would refuse every forex pair.
- **Three separate "no invented input" AST tests**, one each in `stops.py`,
  `risk.py` and `sizing.py`. `risk.py`'s additionally asserts that the module
  never constructs an `AccountBalance` (it *reads* the one it is given) and that
  the budget's amount comes from `RiskParameters.risk_amount` rather than a
  second implementation of the percentage calculation.
- **Two guards in the sizer are deliberately unreachable and are tested anyway.**
  A size whose planned loss exceeded its budget, and a volume that failed its own
  broker-constraint check, cannot occur given the guards above them — but both are
  one edit away, and each test drives its branch by making the bad edit for real
  (`monkeypatch` on `round_volume`). A test proves the invariant today; the guard
  proves it on the day somebody changes the rounding direction.
- **Two guards were *deleted* rather than kept unreachable.** The sizer had a
  `risk_per_unit <= 0` branch that could not fire, because a positive distance, a
  positive tick size and a positive tick value cannot produce a zero product. An
  unreachable guard is worse than none: it reads as though it handles a case it
  does not. The reasoning it embodied is now in a comment explaining why the
  division cannot fail.
- **The reserved events `RISK_CALCULATED` and `POSITION_SIZED` are emitted from
  the application layer, not the domain.** The domain has no logger and must not
  acquire one — a domain that wrote its own log lines would be a domain whose
  behaviour changed with the logging configuration. Both are emitted **on refusals
  too**, at WARNING, because a size refused for being too small is one of the most
  useful lines in the log and an event emitted only on success would leave a
  silence indistinguishable from a signal that never arrived.
- **`RiskService` catches broadly and returns `None`.** The port says "raises when
  the terminal is unreachable" without saying which exception; the MetaTrader
  bindings raise several unrelated types. Narrowing the `except` would mean a new
  failure mode escaped as an exception into a trading loop.
- **The fakes live in `adapters/`, not in `tests/`.** A fixture returning a tuple
  would test a function that does not exist in production. These satisfy the same
  ports the MT5 adapter will, which makes them the specification Phase 7 is written
  against — and both obey the two rules that make its hardest cases testable: an
  unreachable source *raises*, and an unknown symbol *raises* rather than
  returning a default contract.
- **Margin checking is still not implemented, and is recorded as a known gap.**
  A position can pass every check in this project and still be refused by the
  broker for margin. It needs live account state and has no home yet.

### A gap Phase 4 left deliberately

`application/risk_service.py` exists only to close the gap the layering forces:
`domain` is forbidden from importing `ports`, so it cannot ask an
`AccountProvider` for a balance, and `RiskService` is the piece that does. It has
no signal, no stop resolution and no executor — those are Phase 6's
`ProcessSignal`. It was not built speculatively; without it the Phase 4 fakes
would have had no caller.

---

## What Phase 3 Built

```
domain/
  resolution.py   Resolution[T]             the two-outcome result type
  stops.py        resolve_stop()            stop policy
  take_profit.py  resolve_take_profit()     take-profit policy
  validation.py   validate_signal()         four validation layers
                                          validate_geometry()
                                          validate_policy()
                                          validate_against_spec()
tests/unit/
  test_resolution.py        22 tests
  test_stop_resolution.py   56 tests
  test_take_profit_policy.py 50 tests
  test_validation.py        48 tests
  test_defensive_guards.py   7 tests
docs/risk-management.md
```

`Resolution` is modelled once rather than four times. Failures are **values, not
exceptions** — a missing stop is the commonest outcome in the system, not an
exceptional condition.

### Design decisions Phase 3 made

- **Success is an explicit flag on `Resolution`, not `value is not None`.** A
  take-profit step can legitimately succeed with the answer "there is no take
  profit", and inferring success from the value made that indistinguishable from a
  refusal. A log full of refusals for a deliberate configuration trains people to
  ignore the reason codes.
- **A missing stop and a zero stop share one reason code**, because the upstream
  engine uses `0.0` to mean "no stop". Two codes would make an alert fire twice
  for one fault.
- **A level at the entry is a distance problem, not a side problem** — for both
  the stop and the take profit. Reported as "wrong side" it would point an
  operator at a sign error that is not there.
- **An unrecognised basis is refused even when volatility fallbacks are
  permitted.** The flag names one specific alternative, not a general licence, and
  the refusal message says so because the two cases have different remedies.
- **`Resolution.details` is a read-only mapping.** A decision record that could be
  edited after the fact would not be a record.
- **Geometry validation duplicates the stop-side check deliberately.** The two
  functions have different callers, and a check that trusts its input to have been
  verified elsewhere fails the first time somebody calls it directly.
- **The stop, target and tick-size vocabularies moved from the adapter into the
  domain.** Deciding what counts as a defensible stop is a trading policy, and a
  policy living in an adapter would change whenever someone edited a mapping. The
  adapter now reads upstream strings; the domain interprets them.

### Bugs Phase 3 found

Three, all fixed. Recorded because each is a failure mode a later phase could
reintroduce.

1. **A missing stop escaped as a `ValueError` instead of a reason code.**
   `resolve_stop` passed a zero price straight to `StopLoss`, whose own validation
   raises. So the *commonest outcome in the system* — a signal with no stop —
   arrived as an exception. A trading loop that caught `ValueError` here would be
   handling an expected condition as a fault; one that did not would crash on a
   quiet market.
2. **`Resolution` could not express "succeeded with no value".** Found by the
   `TakeProfitSource.NONE` tests, which is the only policy that resolves to
   nothing. Fixed by the explicit `succeeded` flag described above.
3. **A wrong-side stop discarded its computed distance** on the way out, because
   the distance was calculated after the side check. The distance is the first
   thing anyone looks at when asking why a stop was rejected, so it is now
   computed before every check that can refuse.

One test was wrong rather than the code, twice, and both times for the same
reason — an assertion written against a value that Python silently rounds:

* it expected five distinct refusal codes from the stop resolver and got four,
  because a missing stop and a zero stop are the same fault. The test was
  asserting a distinction the design deliberately does not make.
* it compared a `Decimal` distance as a string, but `str(Decimal)` switches to
  exponent notation below `1e-6` — `1E-11`, not `0.00000000001`. Both round-trip
  exactly, so this is a test-formatting choice, not a defect, and the numeric
  comparison is the honest one.

---

## What Phase 2 Built

```
adapters/albrooks/
  identity.py   compute_signal_id()            deterministic, content-derived
  mapper.py     map_result_to_signal()         the anti-corruption layer itself
  source.py     AlBrooksSignalSource           implements SignalSource
  __init__.py   the only names the rest of the project imports
tests/
  stubs.py                                     shaped like the real engine's output
  unit/test_albrooks_mapper.py                 72 tests
  unit/test_albrooks_source.py                 23 tests
  unit/test_signal_identity.py                 16 tests
  unit/test_ports.py                           22 tests
  integration/test_albrooks_real.py            9 tests, real engine
docs/signal-flow.md                            what happens, and where it stops
```

### Design decisions Phase 2 made

These are the ones a later phase is most likely to re-litigate by accident.

- **The prices are excluded from the signal key; the bar is in it.** This is the
  single most debatable decision in the project, and it is deliberate. A
  recomputation over the *same* closed bar that nudges a stop by one tick is the
  same reading with a rounding difference; including the prices would let a
  flapping stop produce a fresh position every cycle. The trade-off: if the
  engine materially revises a stop on the same bar, the revision is suppressed as
  a duplicate. **Phase 9 must revisit this against real engine output.**
- **The direction comes from the action, never from `decision["direction"]`.**
  When the two disagree — an upstream bug — the action wins, because it is the
  coarser and more conservative statement. A long taken from a `SELL` because of a
  stale direction field is the worst available outcome.
- **A `0.0` price means "undefined".** The engine's `TradePlan` uses `0.0` for an
  absent level and its own geometry check *skips* zeros rather than comparing
  them. Preserving that is the difference between "there is no stop" and "the stop
  is at zero", and the second would be wrong by the entire size of the instrument.
- **`Signal.entry` became optional.** Phase 1 made it mandatory, which was wrong:
  the engine returns `plan: None` on every abstention, so requiring an entry would
  have forced the adapter to substitute the last close for one. It is now required
  only for a tradable action, and validated as such.
- **The stop and target bases are carried but not interpreted.** The adapter is a
  converter, and a trading policy inside it would be a policy that changed
  whenever someone edited a mapping. Phase 4 decides what to trust.
- **`WAIT` and `NO_TRADE` stay distinct end to end.** Different upstream claims,
  different log lines, different reasons. Collapsing them would make a quiet day
  unexplainable.
- **The engine is imported lazily, inside a function.** So importing the bridge
  works on a machine that has never heard of `albrooks`, and the error names the
  fix rather than saying "No module named albrooks".

### Bugs Phase 2 found

Four, all fixed. Recorded because each is a failure mode a later phase could
reintroduce.

1. **`Signal.entry` was mandatory in Phase 1**, which made the commonest outcome in
   the system — "the engine found nothing to trade" — unmappable. Found by
   writing the abstention test and watching it return `None` with an empty reason.
2. **An abstention was being reported as `SIGNAL_DIRECTION_UNKNOWN` instead of
   being mapped**, because the shared builder required an entry. Split into two
   construction paths.
3. **`source_direction` read the numeric field for abstentions.** A stray non-zero
   `direction` on a `WAIT` would have produced a tradable signal. Now an
   abstention is `FLAT` unconditionally, and the numeric field is not read at all.
4. **A missing `action` key was reported as an unknown action.** Different
   failures: the first means the upstream shape changed, the second means it grew
   a fifth action. Sending an operator looking for a new enum member instead of at
   the contract is a wasted debugging session.

One test was also wrong rather than the code, and was corrected: it asserted that
two float timestamps differing at the 16th digit produce different keys, but
Python silently rounds `1727740800.0000001` to `1727740800.0`, so the test would
have passed whether or not the renderer worked. It now asserts the fixture values
are distinct doubles *before* checking the keys, so it cannot pass for the wrong
reason again.

---

## The Foundation, Phases 1 to 4

### Layout

```
src/signal_to_trade_bridge/
  domain/          models.py     twelve value objects, achieved_ratio,
                                canonical_ratio                Phase 5
                   enums.py      six enums, 39 rejection codes
                   errors.py     two roots, twelve exceptions
                   resolution.py Resolution[T]           Phase 3
                   stops.py      resolve_stop()          Phase 3
                   take_profit.py resolve_take_profit()  Phase 3
                                signal_target_ratio()      Phase 5
                   validation.py four validation layers  Phase 3
                   risk.py       resolve_risk_budget()   Phase 4
                                check_currency_compatibility()
                   sizing.py     resolve_position_size() Phase 4
                                check_broker_constraints()
                   no external deps at all -- enforced by test_domain_isolation
  ports/           __init__.py                        seven Protocols
  application/     process_signal.py                  ProcessSignal   Phase 6
                   risk_service.py                    RiskService     Phase 4, 6
  adapters/        albrooks/                          Phase 2, complete
                   fake/                              Phase 4, complete; FakeTradeExecutor in 7b
                   mt5/                               Phase 7, complete
                   auto_trade/                        Phase 7b, complete (not yet wired in)
  infrastructure/  logging/                           structured.py  events.py
  configuration/   config.py                          BridgeConfig  config_from_env
  cli/             __init__.py                        empty; CLI is Phase 8
  py.typed
tests/
  conftest.py                                      fixtures, PROJECT_ROOT
  stubs.py                                         shaped like the engine's output
  unit/            test_albrooks_mapper.py    test_stop_resolution.py
                   test_take_profit_policy.py test_validation.py
                   test_domain_models.py       test_resolution.py
                   test_configuration.py      test_albrooks_source.py
                   test_ports.py              test_signal_identity.py
                   test_logging.py            test_defensive_guards.py
                   test_domain_isolation.py
                   test_risk_budget.py        test_position_sizing.py
                   test_risk_service.py       test_reward_risk_ratio.py
                   test_process_signal.py     test_mt5_data_adapter.py
  integration/     test_albrooks_real.py            real Analyzer, opt-in
docs/              architecture.md  integration.md  setup.md
                   signal-flow.md  risk-management.md
scripts/           setup.ps1  setup.sh  test.ps1  test.sh
```

### Domain layer

Frozen, slotted, self-validating dataclasses: `AccountBalance`, `SymbolSpec`,
`Signal`, `StopLoss`, `TakeProfit`, `RiskParameters`, `RiskBudget`, `PositionSize`,
`TradeIntent`, `ExecutionRequest`, `ExecutionResult`, `TradeDecision`.

Enums: `Direction`, `SignalAction`, `DecisionAction`, `StopSource`,
`TakeProfitSource`, `RejectionReason` (39 codes).

Errors: two roots, `ConfigurationError` and `TradingError`, with twelve
subclasses. The split is deliberate — a configuration error should stop the
process, a refused trade should be logged and the next signal processed.

Phase 3 added the **policies** over those values: `Resolution`, the stop
resolver, the take-profit resolver, and four validation layers. Phase 4 added two
more: the risk budget and the position sizer. **All six files are at 100% statement
coverage.**

### Ports

**Seven** `Protocol`s: `SignalSource`, `MarketDataProvider`, `AccountProvider`,
`SymbolSpecProvider`, `TradeExecutor`, `IdempotencyStore`, `KillSwitch`. Narrow
on purpose, because a test has to fake them.

`AccountProvider` and `SymbolSpecProvider` now have **fakes** —
`adapters/fake/account.py` and `adapters/fake/symbols.py`, satisfying the same
ports the MT5 adapter will. The real adapter is Phase 7, written against the
contract these already pin.

### Configuration

`BridgeConfig` + `config_from_env`, reading **17** `BRIDGE_*` variables. Defaults
are the safe ones: `execution_enabled=False`, `dry_run=True`, `risk_percent=0.5`,
`reward_risk_ratio=1.0`.

**A configured `0` is refused, not defaulted.** Phase 5 fixed `_decimal(...) or
default` on the two money settings: `Decimal("0")` is falsy, so
`BRIDGE_RISK_PERCENT=0` used to start the bridge at 0.5%. A blank value still
means "unset", which is the only distinction that matters — between "the operator
said nothing" and "the operator said zero".

### Logging

`Event` enum with **13** event names, `StructuredLogger`, JSON and text formatters,
and substring-based redaction of 13 sensitive key markers.

**Five of the thirteen events are emitted so far**: `SIGNAL_RECEIVED`,
`SIGNAL_REJECTED`, `SIGNAL_SOURCE_UNAVAILABLE`, `RISK_CALCULATED`, `POSITION_SIZED`.
The other eight — `STOP_RESOLVED`, `TAKE_PROFIT_RESOLVED`, `TRADE_VALIDATED`,
`TRADE_REJECTED`, `DRY_RUN_COMPLETED`, `EXECUTION_RESULT`, `DUPLICATE_SUPPRESSED`,
`KILL_SWITCH_ENGAGED` — are defined and reserved, and each is emitted by the phase
that introduces it. A test asserts the vocabulary is complete, so an event cannot be
quietly dropped.

**Phase 4 did not emit `STOP_RESOLVED` or `TAKE_PROFIT_RESOLVED`**, which Phases 2
and 3 introduced. The reason is structural rather than an oversight: those two
resolutions are computed by domain functions, and Phase 4 established that the
domain has no logger and must not acquire one. They will be emitted from
`ProcessSignal` in Phase 6, along with `TRADE_VALIDATED`. Recorded rather than
hidden.

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

  > **Phase 4 closed this one.** `domain/risk.py` and `domain/sizing.py` now
  > implement the rule, and the "no code path invents a balance or a tick value"
  > half of it is enforced by an AST test exactly as the stop rule is. What
  > remains open is only the *source* of the numbers: in production they still
  > have to come from the MT5 adapter of Phase 7.

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

Proposed in Phase 0, scaffolded in Phase 1, first adapter in Phase 2, policies in
Phase 3, sizing in Phase 4. Full rationale in `docs/architecture.md §5`.

```
interfaces/     CLI, wiring, composition root          (Phase 8)
application/    ProcessSignal — the single use case    (Phase 6)
                RiskService — obtains the account and symbol facts  (Phase 4, done)
domain/         value objects (Phase 1) + policies: stops, take_profit,
                validation, resolution (Phase 3); risk, sizing (Phase 4)
ports/          Protocols: SignalSource, MarketDataProvider, AccountProvider,
                SymbolSpecProvider, TradeExecutor, IdempotencyStore, KillSwitch
adapters/       albrooks/  auto-trade/  mt5/  fake/
                albrooks → Phase 2, COMPLETE
                fake → Phase 4, COMPLETE
                mt5 + auto-trade → Phase 7
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
- **The *larger* of `tick_value_profit` / `tick_value_loss`** is used.
  **Corrected in Phase 4**; Phase 0 recorded it as "the worse of the two" and
  Phase 1 implemented that as `min`, which under-sizes. Volume is inversely
  proportional to the tick value, so the larger value is the conservative divisor.
- **Volume below `volume_min` is a refusal, never a floor-up.** Guarded three times
  over as of Phase 4: `SymbolSpec.clamp_volume` clamps *down* to zero,
  `PositionSize.__post_init__` raises if `clamped_to_minimum` is ever set, and
  `resolve_position_size` refuses. Three independent guards, because this is the
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
- **The evidence threshold is named `minimum_evidence_score`, not the brief's
  `minimum_signal_confidence`.** The upstream engine states in three places that
  its evidence score is not a probability of continuation, and a configuration
  key called "confidence" invites exactly the misreading the number cannot
  support. This is a *naming* consequence of the audit's first finding, not of the
  fifth — the fifth is about stop provenance.
- **Redaction is substring-based and broad.** A key merely *containing* `token` is
  redacted. The cost of redacting a harmless field is a slightly less informative
  log line; the cost of missing one is a leaked credential in a file that
  eventually gets pasted into a bug report.

Decisions made in Phase 3 — the ones that shaped the most code:

- **Failures are `Resolution` values, not exceptions.** A missing stop is the
  commonest outcome in the system; an exception would force every caller into a
  `try` block to handle the normal case.
- **Success on a `Resolution` is an explicit flag, not `value is not None`.** A
  take-profit step can legitimately succeed with the answer "there is no take
  profit", and inferring success from the value made that indistinguishable from a
  refusal.
- **`Resolution.details` is read-only.** A decision record that could be edited
  after the fact would not be a record.
- **The structural-basis vocabularies live in the domain, not the adapter.**
  Deciding what counts as a defensible stop is a trading policy, and a policy in
  an adapter would change whenever someone edited a mapping.
- **Geometry re-checks the stop side even though the stop resolver already did.**
  Deliberate duplication: the two have different callers, and a check that trusts
  its input to have been verified elsewhere fails the first time somebody calls it
  directly.
- **Redaction is substring-based and broad.** A key merely *containing* `token` is
  redacted. The cost of redacting a harmless field is a slightly less informative
  log line; the cost of missing one is a leaked credential in a file that
  eventually gets pasted into a bug report.

Decisions made in Phase 4 — the ones a later phase is most likely to re-invert:

- **The conservative tick value is `max`, not `min`.** Volume is inversely
  proportional to the tick value, so the smaller value gives the larger position.
  Phase 1 had it backwards; see *The bug Phase 4 found* above.
- **A single direction-agnostic tick-value bound, not the exact per-direction
  value.** The exact one is knowable and would size both sides optimally. Rejected
  because it makes the size depend on direction, which means two more code paths,
  two more combinations, and a way for a long to be sized with a short's number.
- **An unstated `currency_profit` is refused, not assumed.** The MT5 adapter always
  populates it, so an empty one means the adapter did not run. A check that passes
  on missing data is not a check.
- **The margin currency is recorded but not refused.** EURUSD margin in EUR on a
  USD account is normal; refusing it would refuse every forex pair.
- **Two unreachable guards are kept and tested; two more were deleted.** A guard
  that is one edit away from being reachable is worth keeping, and a test drives
  its branch by making the bad edit for real. A guard that cannot fire at all is
  worse than none — it reads as though it handles a case it does not — so those
  were removed and the reasoning moved into a comment.
- **The domain has no logger, so `RISK_CALCULATED` and `POSITION_SIZED` are emitted
  from the application layer.** A domain that wrote its own log lines would be a
  domain whose behaviour changed with the logging configuration.
- **Both new events are emitted on refusals as well as successes.** A size refused
  for being too small is information; an event emitted only on success would leave a
  silence indistinguishable from a signal that never arrived.
- **`RiskService` catches broadly.** The port says "raises when unreachable"
  without saying which exception. Narrowing the `except` would let a new failure
  mode escape as an exception into a trading loop.
- **The fakes live in `adapters/`, not `tests/`.** A fixture returning a tuple
  would test a function that does not exist in production; these satisfy the same
  ports the MT5 adapter will and are therefore its specification.
- **A fake provider *raises*; it does not return a sentinel.** "The account has no
  money" and "we could not ask" are different answers, and collapsing them is how a
  system ends up sizing trades against a stale balance.

Decisions made in Phase 5 — the ones most likely to be re-litigated:

- **The floor is opt-in and off by default.** See Q6. The tempting "simplification"
  is to make `reward_risk_ratio` do double duty, and it would silently change what
  the default policy does to an existing deployment.
- **Under `SIGNAL` a below-floor target refuses; under `RR_FALLBACK` it falls
  back.** The asymmetry is the point of having two policies, and substituting a
  computed exit for the one the strict policy demanded would be the worst version
  of the feature.
- **The floor is checked last**, after presence, side and basis, so a target wrong
  on two grounds reports the one whose remedy is upstream.
- **A ratio is normalised, never rounded.** Rounding a ratio for readability is
  how 0.9:1 becomes 1:1 in a log.
- **A dead guard is removed, not kept with a reassuring comment.** `_MIN_RATIO` had
  a docstring describing a clamp that did not exist.

---

Recorded with their reasons, because these are the ones a future phase is most
likely to re-litigate by accident.

| Decision | Reason |
|---|---|
| Build sizing in the bridge; do not look for it upstream | Verified absent in both trees. It is the project's substance. |
| Build a separate MT5 data adapter | `auto-trade` cannot read account or symbol data; `albrooks` has the injectable `MT5Feed` pattern to copy. |
| Reuse `JsonExecutionLedger`; do not build a second store | Already durable, atomic and cross-process. A second store is a second source of truth. |
| Refuse an `ATR_FALLBACK` stop by default | The engine calls it "structurally empty"; the brief forbids inventing a stop to make the system trade. |
| Refuse an *unrecognised* stop basis too, even when fallbacks are allowed | The flag names one specific alternative, not a general licence. The two refusals have different remedies, so their messages say so. |
| `Decimal` in the domain | Float drift in position sizing is a monetary error. |
| The **larger** of the two tick values, not the smaller | `volume ∝ 1 / tick_value`, so the smaller divisor gives the larger position. `min` was the least conservative choice available; Phase 4 corrected it. |
| Both `WAIT` and `NO_TRADE` map to the bridge's no-trade, but the distinction is preserved | They are different upstream claims: an abstention versus a condition not met. Losing that loses traceability. |
| Read the decision dict with `.get()` everywhere | The degenerate path returns a 3-key dict, not the normal 13-key one. Strict access would raise on exactly the input that most needs a clean refusal. |
| Editable local path dependencies, not git or submodule | Offline, exact, and identical on every machine. See `docs/architecture.md §7`. |
| Bridge dry run short-circuits before the executor | Guarantees downstream configuration cannot cause an order during simulation. |
| The domain test suite requires neither upstream installed | Enforced by asserting the domain package's import closure. Makes the suite runnable on a laptop with no upstreams cloned. |
| Failures are values, not exceptions | A missing stop is the commonest outcome in the system, not an exceptional condition. |
| `Resolution` success is an explicit flag | `TakeProfitSource.NONE` legitimately resolves to "no take profit"; inferring success from the value made that look like a refusal. |
| `Resolution.details` is read-only | A decision record that could be edited after the fact would not be a record. |
| A missing and a zero stop share one reason code | The engine uses `0.0` to mean "no stop", so they are one fault. Two codes would make an alert fire twice. |
| A level at the entry is a distance problem, not a side problem | Reported as "wrong side" it would point an operator at a sign error that is not there. |
| A missing evidence score fails the filter | If an absent value passed, any source omitting the field would bypass the filter. |
| The *achieved* ratio is reported, not the configured one | Under `RR_FALLBACK` a usable signal target produces the signal's ratio. Reporting the configured one would be reporting an intention as a fact. |
| Policy vocabularies live in the domain, not the adapter | Deciding what counts as a defensible stop is a trading policy, and a policy in an adapter changes whenever someone edits a mapping. |
| Geometry duplicates the stop-side check | Different callers, and geometry can come from configuration. A redundant check costs a comparison; a missing one costs a rejected broker order. |
| Fakes come in Phase 4, before the real MT5 adapter | Unblocks the sizer's tests immediately, and lets the real adapter be written in Phase 7 against tests that already pin the contract. |
| The reward:risk floor is **opt-in**, not the configured ratio | Turning it on replaces engine-measured targets with a computed distance. That is a trading decision, and the maintainer chose not to have an upgrade make it silently. See Q6. |
| One implementation of the ratio, enforced by AST | Five copies is five places for one to be edited and the rest to be quietly wrong, and a policy that checks one ratio while a log reports another is the exact disagreement this project refuses. |
| `achieved_ratio` is present on every success path, `None` under `NONE` | A key that is *absent* on a successful resolution raises `KeyError` in code that has already decided the trade is fine. `None` says "no target" without claiming 0:1. |
| `TradeIntent.take_profit` is optional | `TakeProfitSource.NONE` is supported, documented and tested, and a model that cannot represent it is a bug waiting to be misdiagnosed as a policy change. |
| `reward_amount` is an enforced invariant of `RiskBudget` | Nothing consumed it, so an inconsistent pair would have passed every test while the log advertised a gain the bridge never aimed for. |
| The upstream engine's `reward_to_risk` is discarded, and that is documented | The engine documents its own targets as unvalidated, and the bridge recomputes the ratio from validated distances. The cost is one line of diagnostic detail; recorded so it is not read as an oversight. |
| A configured `0` is refused, never defaulted | `Decimal("0")` is falsy, so `or default` brought the bridge up at 0.5% and 1:1 after the operator asked for neither. Phase 1 had already fixed this idiom for `BAR_COUNT`. |

---

## Implemented Features

What exists and works:

**Phase 1 — foundation**

- The **domain layer**, importable and testable with zero external dependencies.
  Eleven value objects, six enums, a 39-code `RejectionReason`, twelve exceptions
  under two roots.
- The **ports**, as the only thing the inner layers may depend on.
- **Configuration** that defaults to safe and fails loudly on a mistake.
- **Structured logging** with a fixed event vocabulary and automatic redaction.
- **Setup scripts** for Windows, Linux and macOS, with no committed drive letter.

**Phase 2 — the first adapter**

- A working signal path: bars → `Analyzer.analyze` → internal `Signal`, with the
  upstream's thirteen-key and three-key decision shapes both handled.
- **Deterministic signal identity**, so the downstream deduplication can work
  across a process restart.
- **Stop and target provenance** carried through uninterpreted, ready for the
  Phase 4 policy.
- **245 tests**, in about 1.3 seconds, with no MetaTrader terminal, no network and
  no upstream project required.

**Phase 3 — the policies**

- **Stop resolution**, with the no-invented-stop rule enforced by an AST test
  rather than by convention. Structural bases are used, `ATR_FALLBACK` is refused
  by default, and an unrecognised basis is refused even when the fallback flag is
  set.
- **Take-profit policy** with four modes, where a fallback is never silent and the
  *achieved* ratio is reported rather than the configured one.
- **Four validation layers**: the signal, the geometry, the configured policy, and
  what the symbol can express.
- **A `Resolution` type** for the two-outcome shape, with failures as values,
  success as an explicit flag, and read-only details.
- **432 tests, 94% coverage**; the four new files at 100%.

**Phase 4 — risk management and position sizing**

- **The risk budget**, with the no-invented-balance rule enforced by an AST test
  exactly as the stop rule is. `RiskBudget` keeps every input, because a risk
  amount on its own cannot be recomputed by hand.
- **Currency coherence checked before any division.** An unstated profit currency
  is refused rather than assumed; a mismatch between the account's money and the
  symbol's tick value is refused before the division rather than after.
- **The tick-value position sizer**, instrument-agnostic: EURUSD over 30 pips and
  gold over $3.00 both reach $300 a lot by completely different arithmetic, and
  both are in the test suite on purpose.
- **Broker constraints as their own policy** — sub-minimum, above-maximum and
  off-step, each with its own reason code. `VOLUME_NOT_ON_STEP` had a code and no
  producer until this phase.
- **Three guards on the floor-up rule**, and a sizer that discards a size whose
  planned loss would exceed its budget.
- **`RiskService`**, which obtains the account and symbol facts through the ports
  — the one thing `domain` is forbidden from doing — converts a provider that
  raises into a refusal, and emits `RISK_CALCULATED` and `POSITION_SIZED` on
  refusals as well as successes.
- **The fakes** for `AccountProvider` and `SymbolSpecProvider`, satisfying the same
  ports the MT5 adapter will, with named `eurusd` / `gold` / asymmetric spec
  builders.
- **554 tests, 95% coverage**; the five new files at 100%.
- **One real bug found and fixed**: `conservative_tick_value` was `min`, which
  under-sized every position on an instrument whose two tick values differ. See
  *The bug Phase 4 found* above.

**Phase 5 — the reward:risk policy, finished**

- **`BRIDGE_MINIMUM_REWARD_RISK_RATIO`**, an opt-in floor on the ratio a signal's
  own target must clear to be used. Unset by default, so `RR_FALLBACK` behaves
  exactly as it did; set it equal to the configured ratio to get the behaviour the
  policy has always described. Under the strict `SIGNAL` policy a below-floor target
  refuses rather than being replaced.
- **`achieved_ratio` on every successful resolution**, including the path where it
  used to be missing entirely, and `None` — rather than absent or `0.0` — when
  there is no target at all.
- **One implementation of the ratio**, in `models.achieved_ratio`, with an AST test
  forbidding the resolver from dividing on its own.
- **`canonical_ratio`**, because `Decimal` carries its own exponent and the same
  ratio was logged as both `"1"` and `"1.0"` depending on which arithmetic produced
  it.
- **`TradeIntent` can represent `TakeProfitSource.NONE`**, which it could not
  before.
- **`RiskBudget.reward_amount` is an enforced invariant** rather than a derived
  field nothing checked.
- **A configured `0` is refused, never defaulted**, for the two settings where
  silently defaulting would change what the bridge trades.
- **614 tests, 96% coverage**; every file touched at 100%.

**Documentation**: architecture, integration contracts, setup, signal flow, risk
management, bilingual README.

**Not yet implemented, and not to be assumed.** Everything from reading a signal
through sizing it works. Two things are missing, and they are different in kind:

- **Nothing connects a signal to a size.** `RiskService` takes a symbol, a stop
  and a risk configuration; it does not know what a `Signal` is. That
  composition — `ProcessSignal` — is Phase 6. Until then the pipeline can size a
  trade it has not been asked to size, and no code in `src/` has ever called
  `resolve_stop`, `resolve_take_profit` or `validate_geometry` in sequence.
- **The account and symbol facts come from fakes.** In production they have to
  come from MetaTrader 5, which is Phase 7. Until then the numbers are real
  arithmetic over invented facts, which is exactly the situation the AST tests
  guard against.

Margin checking is also absent, and is recorded as a known gap rather than an
oversight: a position can pass every check in this project and still be refused by
the broker for insufficient margin.

**Phase 6 changed this summary.** A signal now goes all the way to a decision: the
bridge can **understand** a signal, **validate** it, **size** it, and **decide**
about it in one call — and since Phase 7 it can read the account and symbol facts
from a real MetaTrader 5 terminal. What it still cannot do is **act** on the
decision: there is no executor wired into the pipeline, and the default
configuration cannot execute. The honest summary is one sentence: *the bridge can
decide to trade and cannot yet trade.*

---

## Tests

**1222 collected, 1205 passed, 17 skipped in about 13 seconds** (no opt-in) The suite runs
**without `albrooks` or `MetaTrader5` installed** and without a terminal — the
MT5 adapter takes its bindings by injection, which is what makes that possible.

| File | Tests | Covers |
|---|---|---|
| `unit/test_domain_models.py` | 91 | Every value object, its validation, and its invariants. |
| `unit/test_stop_resolution.py` | 56 | The no-invented-stop rule, the basis policy, and every refusal path. |
| `unit/test_process_signal.py` | 50 | **The pipeline**: the order, the snapshot, every stage's refusal, the events. |
| `unit/test_take_profit_policy.py` | 50 | The four policies, the ratio arithmetic, and provenance. |
| `unit/test_mt5_position_reader.py` | 48 | **The indicator snapshot**: schema, freshness, the truncate/delete window, the `Decimal` parse. |
| `unit/test_albrooks_mapper.py` | 49 | The mapping, against stubs shaped like the real engine's output. |
| `unit/test_validation.py` | 48 | The four validation layers, fail-closed behaviour. |
| `unit/test_position_sizing.py` | 46 | The sizer, the three floor-up guards, and the conservative tick value. |
| `unit/test_reward_risk_ratio.py` | 44 | The opt-in floor, the achieved-ratio reporting, one ratio implementation. |
| `unit/test_execution_envelope.py` | 33 | **The envelope and the ledger**: an executor that cannot exist without a ledger, and a ledger that refuses rather than answering. |
| `unit/test_execution_wiring.py` | 27 | **The four sending paths**, and the failure paths that must not lose a decision. |
| `unit/test_dry_run.py` | 39 | **The dry run**: the two rulebooks disagreeing, and an unevaluated gate refusing to read as a pass. |
| `unit/test_auto_trade_executor.py` | 50 | **The execution adapter**: the two-way mapping, and the real upstream workflow where it is installed. |
| `unit/test_risk_service.py` | 35 | The wiring, the two reserved events, and the fakes as port implementations. |
| `unit/test_configuration.py` | 33 | Defaults, overrides, fail-loudly behaviour, dotenv parsing, repr redaction. |
| `unit/test_risk_budget.py` | 31 | The no-invented-balance rule, the refusals, and currency coherence. |
| `unit/test_logging.py` | 28 | Redaction cannot be bypassed, JSON shape, handler hygiene, event vocabulary. |
| `unit/test_albrooks_source.py` | 24 | Fetching, delegating, error paths, and the events emitted. |
| `unit/test_resolution.py` | 22 | The two-outcome type, including success-with-no-value. |
| `unit/test_ports.py` | 22 | Port narrowness, structural substitutability, annotation completeness. |
| `unit/test_signal_identity.py` | 20 | Determinism, the bar-as-unit-of-identity, and the upstream filename contract. |
| `unit/test_domain_isolation.py` | 13 | The architectural invariant, parametrised over every domain module. |
| `unit/test_defensive_guards.py` | 7 | Guards reachable only by bypassing model validation. |
| `integration/test_albrooks_real.py` | 9 | The real `Analyzer`, so the stubs cannot drift unnoticed. **Not collected here** — `albrooks` is not installed on this machine, so the module skips at import. |
| `unit/test_cli.py` | 38 | The exit-code contract, the four commands, the report a human reads, and the public API from outside. |
| `unit/test_layering.py` | 137 | Every import edge in the project, against a table of what each layer may import. |
| `unit/test_cli_refusals.py` | 27 | `check`'s success path, the malformed-signal refusals, and `doctor`'s reporting branches. |
| `unit/test_control_id_evidence.py` | 16 | The measured build is backed by a recorded probe, and the gate still refuses. |
| **Total** | **1205 passed, 17 skipped** without the opt-in; **1218 passed, 4 skipped** with it |

> **The per-file counts in this table were wrong before Phase 4 and are now
> measured rather than remembered.** The previous table claimed 72 tests in the
> mapper and 7 in the isolation test; the real figures are 49 and 13. The totals
> it reported were close to right for the wrong reasons. Nothing about the code
> was affected — only the record of it, which is the thing this file is for.

Coverage: **97%** of statements. The four files Phase 7 added, the five Phase 4
added, the four Phase 3 added, and every line of `models.py`, `take_profit.py`
and `config.py` Phase 5 touched are at **100%**.

**`process_signal.py` is at 99%, and the one uncovered line is deliberate.** It is
the `validate_geometry` refusal, which cannot be reached — see *A finding worth
recording* above. The companion `validate_against_spec` refusal *is* tested,
because a specification arrives from outside the process. Leaving one line
untested with the reason written down is more honest than manufacturing a way to
reach it, which would mean bypassing two constructors that cannot produce an
invalid value.

`ports/` shows 0% line coverage, which is expected for `Protocol` declarations and
says nothing; `unit/test_ports.py` checks their contract instead.

The full gate, all clean:

```
ruff check .            All checks passed!
ruff format --check .   94 files already formatted
mypy                    Success: no issues found in 47 source files
pytest                  1205 passed, 17 skipped
```

With `BRIDGE_ALLOW_MT5_TESTS=1` against a running terminal the suite reports
**1218 passed, 4 skipped** — 13 of the 17 skips disappear. They are the read-only
MT5 tests, never collected by default, so a contributor without a terminal sees a
suite that passes rather than a suite that errors.

**Run it in both environments.** Two defects in Phases 12 and 13 were invisible in a
clean shell and visible in a configured one — a test asserting a *default* while
`os.environ` still held the developer's real values, and a `doctor` branch that
never executed because nothing set a terminal path. A green run in a clean shell is
necessary and not sufficient.

> **`pytest tests/integration` could not be verified on this machine.** The nine
> integration tests against the real `Analyzer` collect only when `albrooks` is
> installed, and it is not installed here — the previous phase verified them
> green. Nothing in Phase 4 touches the adapter, so they are expected to be
> unchanged, but **they have not been run.** Run `pytest tests/integration -v` on
> a machine with the upstream checkout before trusting that claim.

`mypy` reports one informational note — an unused `[[tool.mypy.overrides]]` block
for `MetaTrader5` and `auto_trade`. It becomes used in Phase 7 when the adapters
that import them are written. Deliberately left in place before the import exists,
so the ignore is already there when the import arrives.

**A skip is not a pass.** Both upstream projects make this point about their own
live tests, and it applies here. Run `pytest tests/integration -v` explicitly
before trusting an adapter change.

### Five tests worth knowing about

`test_there_is_no_fallback_that_produces_a_stop` walks `stops.py`'s own AST and
asserts that no numeric literal in it could serve as a price and that every
`StopLoss` is built from the signal's own price.

`test_there_is_no_fallback_that_produces_a_balance` and
`test_there_is_no_fallback_that_produces_a_tick_value` are the same idea for
`risk.py` and `sizing.py`. The balance one additionally asserts that `risk.py`
never constructs an `AccountBalance`, and that both the budget's amount and its
planned gain come from `RiskParameters` rather than from a second implementation
of either calculation.

All three are the enforcement behind one rule stated three times: **nothing in
this project invents a stop, a balance or a tick value.** The architectural test
covers imports; these cover the *absence of behaviour*, which is the thing a
reviewer has to be told about and the thing no import rule would catch. Read them
before adding anything to `stops.py`, `risk.py` or `sizing.py`.

`test_resolve_take_profit_never_divides` walks the take-profit resolver's AST and
asserts it performs **no division of its own**. Phase 5 removed four inline
`distance / stop.distance` copies that had accumulated alongside a fifth in
`TradeIntent`. The rule is that the ratio has exactly three named entry points —
`signal_target_ratio` for a target the resolver has not yet accepted,
`achieved_ratio` for one it has, and `target_from_ratio` for deriving a price from
a configured ratio — and the test is how "exactly three" stays true.

`test_this_module_holds_no_executor` walks `process_signal.py`'s AST and asserts
it imports no `TradeExecutor`, `IdempotencyStore` or `KillSwitch`. The rule is
that **execution belongs to Phase 7**, where the kill switch, the idempotency
ledger and the audit log arrive with it — and the assertion is structural rather
than behavioural because the failure mode it prevents is not a wrong answer but a
whole missing safety envelope. It should keep passing until the ledger does.

`test_a_missing_evidence_score_is_refused_not_allowed_through` pins the
fail-closed direction of the evidence filter. A missing value counting as a pass
would make the filter trivially bypassable by any source that omitted the field.

### Tests deliberately not written yet

Writing these before the code they test exists would be theatre:

* the end-to-end pipeline — Phase 6
* idempotency tests — Phase 9
* end-to-end tests — Phase 10
* live MT5 tests — Phase 11, and only behind an explicit opt-in marker

**A margin check has no test and no implementation.** Recorded as a known gap: a
position can pass every check in this project and still be refused by the broker
for insufficient margin. It needs live account state and has no home yet.

---


---

## Phase 12 — CLI, public API, documentation

**What shipped.** A runnable command, an importable public surface, and a setup guide
that was written by running the thing rather than by describing it.

| Piece | Where |
|---|---|
| `main()` returning an exit code, never raising `SystemExit` | `src/signal_to_trade_bridge/cli/main.py` |
| `doctor`, `check`, `signal`, `config` | same |
| Public API, upstream internals deliberately not exported | `src/signal_to_trade_bridge/__init__.py` |
| Console entry point | `pyproject.toml`, `[project.scripts]` |
| 35 tests, mostly the exit-code contract | `tests/unit/test_cli.py` |
| Install-to-first-command guide | `docs/setup.md` §5, §8 |

### The exit codes are the contract

```
0  it did what it was asked (a DRY_RUN counts)
1  a refusal — the bridge worked and declined
2  a fault — unreachable terminal, unreadable file, refused config
3  UNKNOWN — must not be retried automatically
```

**`3` exists so a caller's retry logic has something to key on.** A caller told
"refused" would reasonably resend, and a resend of an unknown-outcome signal may
open a second position. `EXIT_UNKNOWN != EXIT_REFUSED` is asserted in the suite
because that inequality is the entire point.

### What running the CLI found — four real defects

Every one of these was invisible to the unit tests that already existed, and every
one was found by executing the command or by writing a test that used the real
model. This is the argument for the phase.

0. **The public API was unusable from outside the package.** `build_bridge` takes a
   `BridgeConfig`, and `BridgeConfig` was not exported — so the one documented way
   to call the library was "import a name the package does not export". Twelve
   phases had shipped before anyone noticed, because every test reached past the
   package root into a private module to get what it needed, and a test that
   reaches past the API cannot notice the API is missing something. There are now
   three tests that cannot: one asserting every name in `__all__` exists, one
   building a config from the public surface, and one running `python -c` with
   `import signal_to_trade_bridge as stb` and nothing else.

1. **`--side SELL` silently produced a BUY.** The example builder branched on
   `args.symbol` where it meant `args.side`, and `args.symbol` is never `"SELL"`.
   The `else` branch was therefore the only reachable path, so the short side of
   the example had never been produced. A test asserting the mirrored stop landed
   on it immediately.

2. **The emitted signal used the wrong key for its identity.** It wrote
   `signal_id`. `auto_trade.domain.models.TradeSignal.from_dict` requires `id` —
   the *constructor* takes `signal_id` and the *deserialiser* reads `id`, an
   asymmetry in upstream that is easy to miss. A file emitted by `signal` would
   have been rejected by the project it exists to feed. The payload now carries
   both, and `_signal_from` accepts either.

3. **A Windows redirect produced a file `check` refused.** PowerShell redirects
   through UTF-16, so `signal > sig.json` wrote something a strict UTF-8 reader
   rejected with `'utf-8' codec can't decode byte 0xff in position 0` — the
   project's own documented command, failing on the platform most users are on.
   `_read_text_any` now tries UTF-8-sig, UTF-16, UTF-8, cp1252, in that order. The
   last one is the reason the chain can still fail: `latin-1` would decode any byte
   sequence and turn corrupt input into nonsense instead of a refusal.

4. **`config` could not print its own configuration.** `BridgeConfig.risk` is a
   `RiskParameters`, and `json.dumps` raises on it. Two further issues sat behind
   that one: a `Decimal` must become a **string** (writing `0.00500` as `0.005`
   would make the printed configuration differ from the effective one in the
   digits that matter), and a `Path` must **not** be escaped before `json.dumps`
   sees it, or every separator is escaped twice and the output no longer round-trips.

### Two smaller things

- `prog="stb"` in argparse, while the installed command is
  `signal-to-trade-bridge`. The usage line named a command the user had not typed,
  and it is the line people paste into scripts. Now derived from `argv[0]`.
- The `config` help said "redacted". Nothing is redacted, **deliberately**: a
  heuristic that guesses which values look like secrets either over-redacts a risk
  percentage or under-redacts a token, and the second is the failure that matters.
  The help now says what the command actually does.

### What the example signal deliberately does *not* contain

No `volume`, no `price`. Those are the bridge's to compute — the volume from the
account balance and the symbol's contract, the entry from the signal's own
resolution. A template carrying a plausible `0.10` would be a number nobody
computed, accepted by `auto-trade` and refused by this bridge with no explanation
of which number was invented. The `check` output shows the real computed volume
(`1.66` against a $10,000 account at 0.5%).

### Two pre-existing tests were environment-dependent

Not a Phase 12 defect, but found by running the full suite **twice** — once in a
clean shell and once with the real terminal paths exported, which is how a Phase 11
operator actually runs it.

`clean_environment` snapshots and restores `os.environ` but **never clears it**, and
that is deliberate (so it cannot delete a developer's real settings mid-session).
The consequence is that a test asserting something about the *defaults* runs
against the ambient environment. `test_no_machine_specific_path_is_baked_in` —
"no machine path is hard-coded" — passed on a machine with nothing configured and
**failed on the machine that configures a terminal**, which is the worst possible
pairing. It now clears the two variables it is about.

My own new test had the same flaw and was caught by the same double run: it counted
escaped backslashes across the whole `config` document, so it also counted
`mt5_terminal_path` and `mt5_data_path`. It now compares against `json.dumps` of the
expected value, which is exact and cannot be moved by an unrelated setting.

**Both the test and the fixture were right in isolation and wrong in combination.**
Run the suite in a configured shell before trusting a green run in a clean one.

### Test infrastructure changed

`StubBindings` moved to `tests/conftest.py` as a shared double, with a
`bridge_factory` fixture. `tests/unit/test_composition.py` had its own copy; three
modules now need it, and a copy per module is three places for a wrong MT5 field
name to hide. `pythonpath` gained `"tests"` so a test module can import the double
by name.

`build_bridge()` **without** bindings refuses on a machine with no terminal — which
is correct production behaviour and useless as a test default, hence the factory.
A CLI test that reached for `build_bridge(BridgeConfig())` and got `MT5Unavailable`
was hitting the right behaviour in the wrong place.

### Still true after Phase 12

- **No command can place an order.** `build_live()` refuses on the build mismatch,
  and the CLI reaches no order path at all. A test asserts the CLI module contains
  no call to `build_live`.
- **`control ids REFUSED`** on `doctor` on this machine: measured on build 6184,
  terminal is 6230, a gap of 46 builds. Unchanged by this phase and unfixable from
  a script.
- **`albrooks` is still not installed**, so the engine-side integration tests
  remain unrunnable here.
- Phase 11's live suite is still opt-in behind `BRIDGE_ALLOW_MT5_TESTS=1`.

---

---

## Phase 13 — Final architecture review

**Method.** Built the import graph, read the diagram against it, measured every
module's coverage, and looked for code nothing reaches. Three things came out of
it: one design bug, one documentation lie, and one layer rule that had never been
checked.

### The finding that mattered: a diagnostic that diagnosed nothing

`doctor` built a careful `problems` list, with a reason attached to each entry, and
then **never printed it**. `_render_doctor` drew the status block only. So a
machine with a typo in `BRIDGE_MT5_TERMINAL_PATH` exited 2 and said nothing about
the missing file — the operator got a complaint about control identifiers and a
`REFUSED` mark, neither of which is why the bridge cannot start.

This is the Phase 12 story again, one layer up. `doctor` is the command
`docs/setup.md` tells a reader to run first, so the first thing it did with a broken
configuration was conceal it. Fixed: the problems block is now rendered, and it is
rendered **first**, because the `ok` / `REFUSED` marks are corroboration for a
conclusion the operator has already been given.

The same review found that `doctor` reported the configured terminal path and
silently ignored the configured **data** path. That is the path a reader has to go
and find by hand — the hash-named folder under `MetaQuotes\Terminal`, which
`docs/setup.md` explicitly walks them through locating — so the one value most
likely to be wrong was the one not shown. It is now reported and checked, and the
absence is phrased as what it is: a lost confirmation, not a fault, since a build
can still be read from the executable.

Both branches had **zero** test coverage, which is why neither was noticed.

### The documentation was describing a different project

`docs/architecture.md` §5.1 drew five layers and said "dependency direction is
strictly inward". The code has eight layers plus two root modules. `cli/` was
called `interfaces/`. `configuration/`, `infrastructure/logging/`, `composition.py`
and `live.py` were absent entirely. And the strictness claim was not true:
`application` and `adapters` both import `infrastructure.logging`.

None of that is a criticism of the Phase 0 audit — it could not know what thirteen
phases would need. But a diagram that has drifted is worse than no diagram, because
it is what a new reader trusts. Rewritten to the architecture as built, with the
two edges that are deliberately not inward named in a table rather than left for
somebody to rediscover.

### The rule that was written down and never checked

Only one direction was enforced: `test_domain_isolation.py`, which is the important
one, since a domain import of a private upstream would break the suite on any
machine that could not clone them. Every other edge was documented and unenforced.

`tests/unit/test_layering.py` now walks the whole graph and fails on any edge not
in a permission table, with the failure message naming what was allowed. It also
fails when a **new top-level package appears on disk without being added to the
table** — otherwise a new layer's imports would be unchecked, which is the same as
having no rule.

**The most consequential direction it now guards is the one nobody would have
written a test for:** only `cli/` and the package root may import `composition` or
`live`. If `application.process_signal` imported `composition`, the pipeline would
be assembling its own collaborators — the dependency inversion this architecture
exists to prevent — and every other test in the project would still pass, because
the offending edge points outward and the domain rule does not care.

Two things about writing that test are worth recording:

- **It reported "no violations" the first time it ran, on an empty graph.** The
  module keys were built without the package prefix while the imports carried it, so
  nothing resolved. A test that cannot fail is indistinguishable from one that
  passes, and an architecture test that cannot fail is worse than none, because it
  is trusted. There is now a test whose only job is to assert the graph was built
  (>50 edges, ≥5 layers reached), and a parametrised case per edge so a failure
  names the file and line.
- **It flagged correct code twice.** `live → composition` (both are root modules)
  and `adapters/albrooks/source.py → infrastructure.logging` (the named exception).
  A test that fails on correct code gets disabled, so both were fixed in the test
  rather than worked around, and the exception is now visible in the permission
  table itself instead of hidden in a branch.

### The logging exception, kept and bounded

`application` importing `Event`, `StructuredLogger` and `get_logger` from a concrete
implementation is a real dependency-inversion violation, and `ports/` has no logging
Protocol to legitimise it.

It is being kept, and the reasoning is recorded so nobody "fixes" it by reflex:
logging is a vocabulary, not a capability. `Event` is the same category as
`RejectionReason`, which the domain is entitled to define. A Protocol with one
implementation and no plausible second is ceremony rather than inversion.

What is **not** acceptable is the same import reaching `configure_logging`, which
mutates global handler state. That is forbidden by name and checked, because
application code that reconfigures logging is exactly how this exception becomes a
doorway.

### Also found

- **`live.py` imported `composition._ledger`,** a private name across a module
  boundary. Renamed to `resolve_ledger` and made public, because it is a policy
  decision — the single place that decides whether an idempotency store is available
  — and the live side has to make exactly that decision. Depending on a private name
  is the kind of coupling that looks harmless until the function is renamed and the
  live assembly breaks with no test pointing at the seam.

- **No dead code.** Every module-level name in the package is referenced by
  something. `cli/main.py` was the only file meaningfully below par at 85%, and the
  gap was entirely the paths that refuse bad input — which is the part of a
  trading CLI most worth testing. Now 95%, with `check`'s success path, the
  malformed-signal refusals, and `doctor`'s reporting branches all covered.

- **One more refusal, found by writing a fixture.** A signal carrying a stop price
  with no `stop_basis` is refused with `NO_VALID_STOP`. That is correct behaviour
  and it is now pinned by a test, because "fill in the missing provenance" is the
  obvious tempting shortcut and it is the line that would let this project invent
  where a stop came from.

### Where it stands

**1205 passing, 98% coverage.** Lint, format, type check, domain isolation and the
full layer graph all clean, in a clean shell and in one with the real terminal paths
exported.

**What a review cannot do.** None of this closes Phase 11. The control identifiers
are still measured on build 6184 against a terminal on 6230, and that still needs a
human at the keyboard. This phase made the refusal well-reported; it did not and
could not make the identifiers valid.

---

---

## Phase 14 — Arming the machine, and the first real order

**This is the phase where the project actually traded.** Everything before it built,
proved, documented and refused. This section records what happened, including two
defects that only a live click could have found and one that was mine.

### The order

`BRIDGE_EXECUTION_ENABLED=true` and `BRIDGE_DRY_RUN=false` in a gitignored `.env`,
and a new `trade` command with three separate permissions: `--confirm-demo`, an
arming configuration, and control identifiers measured on this build. `--what-if`
previews by substituting a recorder for the executor and nothing else.

The first send, EURUSD 0.03 lots at 0.01% risk -- $10 risked on a $100k demo
account -- produced:

```
ticket 384819188  EURUSD  BUY  0.01  entry 1.12614  sl 0.0  tp 0.0
comment 'stb EURUSD LONG smoke-test-1'
```

**A position opened. The click path works end to end.** The state machine went
`SIGNAL_RECEIVED → VALIDATING → VALIDATED → LOCATING_TERMINAL → PREPARING_UI →
ORDER_READY → EXECUTING → EXECUTION_DETECTED → VERIFYING → VERIFICATION_FAILED`.

### What was wrong with it, and why the system said so

The decision came back `UNKNOWN` / `VERIFICATION_FAILED`, "no new matching position
detected". The evidence in the ledger:

```
baseline  ui positions=none
observed  ui positions=384819188:EURUSD:BUY:0.01
```

The position was there and the system still refused to call it proven, because the
observed volume was **0.01** and the requested volume was **0.03**. And the
stop loss and take profit were **both 0.00**.

So three fields did not reach the terminal: volume, stop loss, take profit. The
symbol and the BUY click did. That is a coherent single failure -- the *input*
fields were not written -- and it is the most important thing this project has
learned about the execution layer.

**`UNKNOWN` was the correct verdict.** The order went out and the outcome could not
be proven to match the request, and the ledger exists precisely to stop a retry
against something that may already be live. The refusal is the feature working.

**A position with no stop loss is the failure this project exists to prevent**, so it
was closed immediately rather than left to be studied. `auto-trade close-position`
refused, correctly: the ticket was not in the ledger with an order reference, so
the command would not close something it could not prove it owned. It was closed
through `MT5DesktopAdapter.close_position()` -- the same method, the same UI route,
the same independent verification -- bypassing only the ledger precondition.

**Algo Trading was not enabled, and was not needed.** The MT5 API close returned
`AutoTrading disabled by client`, which is correct behaviour for a system whose
whole design is to open and close positions by clicking the terminal. Nothing in
this project depends on that API.

### The `--what-if` defect, which is the worst kind

The first version of the preview flag built the live side -- which wires the real
executor -- processed the signal, and then printed "nothing was sent". **The order
had already gone out.**

It was found by an `--what-if` run that came back `UNKNOWN`. The fix is to
substitute the recorder *before* the signal is processed, so everything that decides
*what* would be sent stays real and only the final click is replaced.
`tests/unit/test_cli_whatif.py` asserts the executor in place at the moment the
signal is handled, with no terminal involved.

**A flag whose entire contract is "this sends nothing", tested only by its printed
output, is tested by the thing it is not responsible for.**

### The ledger protocol defect

`ExecutionWorkflow` calls `record_result`, `record_attempt` and `contains` on the
object it is given as `ledger`. `AutoTradeLedger` only offered this project's port,
whose `record_outcome` takes three loose arguments -- so the call raised
`AttributeError` **after** the click, which is the worst possible moment, because
the outcome then genuinely is not known. That was the cause of the first `UNKNOWN`.

`tests/unit/test_ledger_protocol.py` now reads upstream's source, extracts every
`self.ledger.*` call, and asserts the wrapper has all of them. It found **three**
methods rather than the two expected -- `record_attempt` is called too, and the
wrapper happens to have it because this project's port has the same shape. Written
from expectation rather than from the source, that near miss would have stayed
invisible.

### A leak that armed every test after it

`config_from_env(apply=True)` seeds `os.environ` from whatever `.env` it finds, so
any test that read the configuration armed every test that ran after it. Two tests
asserting the **shipped defaults** failed on the day this machine armed itself, and
neither had anything to do with the CLI. `tests/conftest.py` now restores the
environment around every test through an autouse fixture -- an autouse fixture
rather than a reminder, because the reminder is what failed.

### Where it stands

**1235 passing, 97% coverage.** 1248 with the live opt-in and a terminal running.

**Open, and it is the next thing to fix: the order dialog's input fields are not
being written.** Symbol and the final control work; volume, stop loss and take
profit do not land. Until that is understood, this project can open a position it
cannot protect, which is worse than not opening one at all. The account is flat,
the balance is 100001.54, and no position is open.

---

## Phase 15 — Stopped mid-investigation; here is exactly where

**Read this first. The headline is not a fix, it is a diagnosis plus an unfinished
guard.**

**Account state: flat. Balance 100000.91, no positions, no pending orders, one
terminal (pid 11228, account 10000001). No order is open.**

### The root cause, established with evidence

Every order this bridge placed came back the same way: **0.01 lots where 0.03 was
asked for, and no stop loss and no take profit**, while the order dialog read back
exactly what had been requested and MT5 had applied all of it.

That rules out a write race and a focus problem, and a screenshot of the terminal
settled it: the toolbar shows **Algo Trading in red**, which on this Alpari build
means the terminal is in **One Click Trading** mode. In that mode `Buy by Market`
does not send the order ticket's fields at all — it sends the **Toolbox Trade
panel's** values, and that panel has no stop loss. That is precisely the shape of
every fill.

So `confirm_dialog_matches` was never wrong. It reads the dialog, the dialog was
right, and **no reading of the dialog can reveal which panel the click will use.**

Measured, for the record: `account_info().trade_mode == 0` and
`terminal_info().trade_allowed is False` on this account.

### What is committed, and what each piece is actually worth

**`auto-trade` (D:\Projects\auto-trade) — the settle wait, uncommitted.**

`prepare_order` now polls every written field until MT5 reports it, through
`_await_fields_applied`. MT5 genuinely does apply order fields asynchronously —
measured here at **250 ms / 500 ms / 800 ms** for volume, stop loss and take profit —
and the old code read each field once, which proves only that the write reached the
control. `tests/unit/test_order_field_settling.py` (14 tests) drives the settling
behaviour rather than the write.

**It is a real defect and a correct fix, but it was NOT the cause of the bad fills.**
It was written while still believing the cause was a race. Keep it; do not credit it
with anything.

Also changed in that repo, uncommitted: `_same_number` compares order-field readings
numerically, because MT5 reformats what it displays and a string comparison would
refuse correct orders.

**`signal-to-trade-bridge` — the mode check, uncommitted and NOT WORKING.**

`src/signal_to_trade_bridge/adapters/mt5/execution_mode.py` reads the mode and
`refuse_one_click_order()` produces the refusal message.
`tests/unit/test_execution_mode.py` (11 tests) covers both, including "an unreadable
terminal is not assumed safe".

**It is inert on the live path, and this is why:**

```
>>> _looks_like_mt5_bindings(load_bindings())
False
```

`AutoTradeExecutor.__init__`'s `bindings` parameter is the **`auto-trade`** bindings
namespace (`module`, `ExecutionWorkflow`, `TradeSignal`, …), not the MT5 bindings. The
duck-type check added to keep the executor's own tests honest therefore returns
`False` in production too, so the refusal never runs. This was found in the step
immediately before stopping, and it is the first thing to fix.

`live.py` was also edited to pass `live_bindings` (the MT5 ones) into `_finish`, and
`_finish` passes them as `bindings=` — which is the wrong parameter, and is part of
the same mistake rather than a fix for it.

### The work that remains

1. **Give the executor the MT5 bindings separately.** Add a distinct parameter —
   `mt5_bindings` — rather than reusing `bindings`, because the two are different
   packages and conflating them is what caused this. `live.py` already has
   `live_bindings` in hand; pass it there.
2. **Delete `_looks_like_mt5_bindings`.** It exists only to stop the check breaking
   the executor's own tests, and once the bindings are the right object it is dead
   code that would silently disable the guard again.
3. **Decide whether the guard belongs in the executor at all.** It currently sits
   immediately before `workflow.execute`, which is the right place in this codebase,
   but upstream owns the click and upstream is what knows which panel the click uses.
   A refusal here is honest and cheap; the argument for upstream is that a rule about
   a terminal capability belongs beside the code that uses the terminal capability.
4. **Test it against the real terminal.** `check_control_ids` already reads the build
   from the same bindings, so `execution_mode` should join that seam rather than
   opening its own.
5. **Only then re-send.** With Algo Trading still off, a correct order cannot carry a
   stop, and the guard will refuse. That refusal is the expected result and is the
   proof the guard works.
6. **Enabling Algo Trading is the operator's decision**, taken deliberately and by
   hand — it changes what the terminal is permitted to do. No script in this project
   should toggle it on the way past.

### State of the suites

| Suite | Result |
|---|---|
| `signal-to-trade-bridge`, clean env | **1247 passed, 17 skipped** |
| `signal-to-trade-bridge`, MT5 + opt-in | **1247 passed, 4 skipped** |
| `auto-trade` | 615 passed, 1 failed, 1 skipped |

**The one `auto-trade` failure is pre-existing and not from this phase:**
`test_a_reviewed_env_file_may_enable_execution` asserts `this test needs the project's
own .env`, and that file does not exist in that checkout. Verified by stashing this
phase's changes — it fails identically without them.

`ruff check` and `mypy` are clean on every file this phase touched. `ruff format
--check` reports 35 files in `auto-trade` as unformatted, **all pre-existing** —
verified by stashing; neither of the two files changed here is among them.

### What this phase cost, plainly

Four real positions were opened on the demo account and every one filled with no stop
loss, because the mode was wrong and nothing checked it. All were closed; the account
is flat and the cost is round-trip spread on 0.01 lots, visible as the balance moving
from 99999.96 to 100000.91.

The failure that mattered was never failing to write the values into the dialog. They
were in the dialog every single time.

---

## Phase 16 — Hygiene, portability, and finishing the Phase 15 guard

This session started from the Phase 15 hand-off ("stopped mid-investigation") and a
repository whose test suite, on a clean machine, was **27 failed and 6 errors**. It
ended at **1184 passed, 84 skipped, 0 failed**. Nothing here placed an order or opened
a terminal.

### What was wrong, and what was done

1. **The suite was not portable (the 33 failures).** Three causes, all of them tests
   asserting things that only hold on the author's machine:
   * **Tests needing the private `auto_trade` package ran unconditionally.** They are
     now marked `@pytest.mark.requires_auto_trade` (registered in `pyproject.toml`) and
     skipped, with a reason, by `pytest_collection_modifyitems` in `tests/conftest.py`
     when the package is absent. `tests/unit/test_live_assembly.py` is marked as a whole
     module: it patches `auto_trade.*` dotted paths and builds the real
     `TerminalProfile`, so its docstring's old claim that it needs no private package was
     wrong and has been corrected.
   * **`build_bridge` could not be built without `auto_trade`**, because it refuses when
     the ledger is unavailable. `build_bridge` gained an optional `ledger=` argument (an
     injected ledger is trusted, exactly as an injected binding set already was; omitting
     it behaves as before). `tests/stubs.InMemoryLedger` satisfies the port, and the
     `bridge_factory` fixture uses it, so the CLI-report tests no longer depend on the
     private package. *This proves the bridge is wired correctly, not that upstream's
     ledger is durable — `TestAgainstTheRealLedger` still covers that where the package
     exists.*
   * **Tests pointed at the author's real terminal.** They now use a synthetic terminal:
     `tests/terminal_fixtures.py` (shared helpers) and the `fake_terminal` fixture. The
     two assertions that are only meaningful against a real install
     (`6230` read from the real executable and snapshot) are gated by
     `@needs_real_terminal`, driven by `BRIDGE_TEST_*` variables (see `.env.example`).
2. **Personal data was committed.** A demo account login, the account holder's display
   name, a Windows profile name, install paths and a terminal-instance hash appeared in
   source, tests, docs and this file. All were replaced with neutral placeholders; the
   live tests, and `scripts/control_ids.py`, now read their machine-specific values from
   the environment and refuse or skip with a message when unset. **Git history still
   contains the originals.** If this repository is, or will become, public, rewrite
   history (`git filter-repo`) and consider the demo login and profile name disclosed.
3. **The Phase 15 guard was inert, and is now wired.** `AutoTradeExecutor` takes a
   separate `mt5_bindings` parameter (typed by the new `TradeModeSource` protocol in
   `adapters/mt5/execution_mode.py`); `live.py` passes the execution project's bindings
   and the terminal's bindings **by name** to `_finish`. `_looks_like_mt5_bindings` was
   deleted. Five new tests (`TestTheOneClickGuard`) assert an order with a stop is
   refused *before* the workflow is reached, that an unreadable terminal refuses, and
   that the two binding sets are distinct parameters.
4. **The guard's mode test was reading the wrong field.** `is_one_click_trading` treated
   `account_info().trade_mode == 0` as "trading disabled". In MetaTrader 5 that field is
   `ENUM_ACCOUNT_TRADE_MODE` (0 DEMO, 1 CONTEST, 2 REAL): it is the account *type*. So
   every demo account read as one-click, and a real account with Algo Trading off read
   as fine. The decision now rests **only** on `terminal_info().trade_allowed`; anything
   other than an exact `True`, or an unreadable terminal, reads as one-click. Tests were
   rewritten to assert that `trade_mode` cannot change the answer either way.
   *(The Phase 15 text above, and its measured `trade_mode == 0`, record what the
   terminal reported; the interpretation of that number is what was wrong.)*
5. **Documentation was stale.** `README.md` and `README_FA.md` said "Phase 7 half done",
   quoted old test counts, described the One Click defect as "input fields not being
   written", and rendered every ✅ as a garbled glyph. All corrected; `ROADMAP.md` was
   added; `docs/setup.md` gained "what is skipped and why", the trading-mode gate and the
   re-measurement variables.

### What is still not verified (do not read this phase as closing Phase 15)

* **The guard has never run against a real terminal.** Its tests use doubles. In
  particular, whether `terminal_info().trade_allowed` is `False` in exactly the state
  that produced the stop-less fills — and `True` once Algo Trading is enabled — is an
  inference from one machine's behaviour, not a documented MT5 guarantee. Check it with
  a read-only probe before relying on it. Note also that MT5's One Click Trading is a
  separate setting (Tools → Options → Trade); the Phase 15 diagnosis ties the symptom to
  the Algo Trading button because that is what the screenshot showed. If the two ever
  diverge on a build, the guard must be revisited.
* **Phase 15 steps 4–6 remain**: join `execution_mode` to the `check_control_ids` seam,
  test against the real terminal, and only then re-send with Algo Trading still off,
  expecting a refusal.
* `tests/integration` (real `albrooks`) and `tests/live` have not run here.
* The upstream `AuditLogger` file-handle leak (Phase 14) is still recorded, not fixed.

### Verification (this session)

```
ruff check .            All checks passed
ruff format --check .   clean
mypy                    Success: no issues found in 48 source files
pytest                  1184 passed, 84 skipped, 93% coverage
```

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
5. **`auto-trade`'s control ids were measured on Alpari MT5 build 6184. This machine
   runs 6230 — a 46-build gap, now confirmed.** The build was read from two
   independent sources that agree: the live indicator snapshot's `terminal_build`
   field, and `terminal64.exe`'s `FileVersion` (`5.0.0.6230`). So the drift risk is
   no longer hypothetical. `terminal-check` exists for this, and Phase 11 must
   re-measure every control id against 6230 before anything is clicked. **Do not
   trust an id captured on 6184.**
6. **`auto-trade`'s defaults hard-code another machine's absolute paths** —
   `C:\Program Files\Alpari MT5_2\...` and a `C:\Users\YourUser\...` data
   path. The bridge must read none of its configuration.
7. **Neither upstream is on PyPI.** Both are private git repositories. The
   dependency strategy in `docs/architecture.md §7` exists because of this.

---

## Remaining Work

### Phases 5–13

As laid out in `README.md`.

#### Phase 5 — 1:1 risk/reward  ← **done, and it was not a no-op**

The handoff for this phase said the work was "largely already implemented — verify
the wiring and add the missing coverage". The wiring turned out to be *present but
not connected*, and the default policy did not do what its own documentation said.
See *The finding that made this phase worth doing* above.

**Do not read this entry as confirmation that a phase can be skipped because its
output looks finished.** Three of the four bugs fixed in this phase were in code
that Phases 1 and 3 wrote, had 200+ passing tests, and was described here as
complete.

#### Phase 6 — trade validation pipeline  ← **done**

`ProcessSignal.process(signal) -> TradeDecision`, running every stage in order and
stopping at the first refusal. See *What Phase 6 Built* above.

#### Phase 7 — adapters  ← **done, both halves**

**Done:** the MT5 data adapter and the `auto-trade` execution adapter. See *What
Phase 7 Built* and *What Phase 7b Built* above. The data half is tested without a
terminal at all, which is the whole reason the bindings-injection pattern was chosen
in Phase 0. The execution half is tested against the **real** `ExecutionWorkflow`,
`RiskEngine` and ledger, with a `DryRunTerminalAdapter`, so no terminal is opened and
no control can be reached.

`AutoTradeExecutor` wraps `auto-trade`'s `ExecutionWorkflow`. **Wrapped, never
bypassed**, per a Phase 0 decision that still stands: calling the terminal adapter
directly would skip the risk engine, the kill switch, the ledger, the state machine
and the audit log.

**Why the earlier refusal was right even though the blocker is gone.** The
executor was held back because `auto_trade` was a private repository not installed
here, so the adapter would have had to be written against a transcription of its API
with nothing to check it against. That would have produced code which looks
finished, has passing tests asserting my own assumptions back at me, and has never
been compared to the thing it calls — precisely the shape of work Phases 4 and 5
each found real bugs in, while believed complete. The checkout arrived; the adapter
was written against the source; and the transcription is no longer load-bearing.

**The contracts are recorded where the work will look for them:** `docs/integration.md`
§2.14 is the `ExecutionWorkflow` reference, written from the source in Phase 7b
rather than transcribed in Phase 0. §2.2–2.5 still has the `TradeSignal` and
`ExecutionResult` shapes, and §2.14.3 has the six-status vocabulary including the
`UNKNOWN` state that must never be auto-retried and the `CLOSED` one that maps onto
it.

**It arrives with the ledger, not before.** `test_this_module_holds_no_executor` in
`tests/unit/test_process_signal.py` asserts the pipeline imports no `TradeExecutor`,
`IdempotencyStore` or `KillSwitch`, and **it should keep passing until the ledger
does** — not merely until an executor exists.

#### Phase 8 — dry run reporting  ← **done**

See *What Phase 8 Built* above. `application/dry_run.py` turns a validated intent
into the request that *would* be sent and asks `auto-trade`'s own risk engine what
it would say.

#### Phase 9 — idempotency  ← **done**

See *What Phase 9 Built* above. The envelope, the ledger over the real JSON ledger,
the two-phase port, and the `ExecutionRequest.take_profit` fix Phase 8 left open.

#### Phase 10 — end-to-end integration  ← **done**

See *What Phase 10 Built* above. The composition root, and the `signal_id` repair
Phase 9 left for here.

#### Phase 11 — MT5 / demo validation  ← **done**

Closed by measurement, not by editing a number: `auto-trade terminal-check` reported
13 of 13 controls OK on build 6230 and `MEASURED_ON_BUILD` / `EXPECTED_BUILD` moved to
6230 afterwards (see Phases 13–14). The heading and the three-step list that used to
sit here described the state *before* that and were removed in Phase 16.

Phases 12–14 are done. **Outstanding work now lives in `ROADMAP.md`**, which is kept
current; this section is history and is not updated.

---

## Open Questions

### Resolved

**Q1. `signal_id` composition — settled in Phase 2, to be revisited in Phase 9.**

SHA-256 over `(symbol, timeframe, bar_index, bar_time, action, direction,
setup_id)`. **The prices are excluded, and the bar is the unit of identity.**

The reasoning: re-delivery of the same reading must be recognised, a
recomputation after new bars close must be a new trade, and a recomputation over
the *same* bar that nudges a stop by one tick is the same reading with a rounding
difference. The bar satisfies the first two; excluding the prices satisfies the
third. Including them would let a flapping stop produce a fresh position every
cycle.

**The accepted trade-off:** if the engine materially revises a stop on the same
closed bar, the bridge treats the revision as the same signal and suppresses it.
That is a real limitation, stated rather than hidden. Phase 9 must check it
against real engine output, and if a material revision turns out to be common the
key will need a revision counter rather than a price hash.

**The key format is `stb-<32 hex>`, and the field order is part of the
contract.** Changing the order changes every key, which would make every
previously-recorded signal look new and re-enable duplicates against a live
ledger. A change needs a migration note here, not just a test update.

### Still open

**Q2. Whether the MT5 account/symbol adapter extends `albrooks.adapters.mt5` or
stands alone.** Both defensible. Depends on whether `albrooks` would accept a new
public surface — the maintainer's call, and it is not the bridge's to make.

Phase 4 narrowed this without answering it. `adapters/fake/` now pins the contract
the real adapter has to satisfy — `AccountProvider.balance() -> AccountBalance`,
`SymbolSpecProvider.spec(symbol) -> SymbolSpec`, an unknown symbol raising, an
unreachable terminal raising — so the remaining question is only whether the MT5
data code is shared with the upstream project or lives here. That is a smaller
decision than it was, which was most of the point of writing the fakes first.

**Q3. Live MT5 validation target.** Alpari demo. On the current machine:
terminal `C:\Program Files\<Broker> MT5\terminal64.exe`, data folder
`C:\Users\YourUser\AppData\Roaming\MetaQuotes\Terminal\0123456789ABCDEF0123456789ABCDEF`,
build **6230** (confirmed twice, see Known Issue 5), account `10000001` on
`Alpari-MT5-Demo`. The terminal was **not** opened or clicked, and **must not be
touched outside Phase 11**. Reading its published snapshot file is not touching it.

**Q4. `WAIT` and `NO_TRADE` in bridge behaviour.** Settled in Phase 2 as *stay
distinct, behave identically*. Both are abstentions, both are refused, and the
upstream reason travels with the signal, so a log can always say which one
occurred. Reopen only if a phase needs the distinction to change a decision rather
than a message.

**Q5. Whether the bridge should eventually place its own orders through the
`MetaTrader5` bindings**, bypassing the execution project's UI automation. It
should not — the execution project owns execution, and the brief is explicit. Noted
only so nobody later "simplifies" it away. The bridge's *data* adapter uses the
bindings; its *execution* path does not.

**Q6. Whether `RR_FALLBACK` should have a floor by default.** **Raised in Phase 5,
decided for now, still open.**

The default policy documents itself as "I want 1:1 as the floor" and does not
implement one: nothing compares the signal's implied ratio against anything, so a
structurally sound 0.2:1 target is accepted silently. Phase 5 made the floor
*possible* — `BRIDGE_MINIMUM_REWARD_RISK_RATIO`, unset by default, so behaviour is
unchanged — because the choice between the alternatives changes what trades the
bridge takes and is the maintainer's to make.

The maintainer chose **opt-in** over the two other options on the table:

| Option | Rejected because |
|---|---|
| Make `reward_risk_ratio` an implicit floor | Silently changes what the default policy means for any existing deployment, and the change is invisible: a trade that used to take the engine's target now takes a computed one. |
| Refuse a below-floor target under `RR_FALLBACK` | Turns a fallback policy into a filter. `RR_FALLBACK` would refuse trades that `SIGNAL` accepts, which is the opposite of what "fallback" means. |
| **Add an opt-in floor** *(chosen)* | Nothing changes until someone asks for it, and the setting is documented as the way to get the behaviour the docs have always described. |

**Still open, deliberately.** Whether the *default* should eventually carry a floor
is a trading decision that no test can settle. If it is ever revisited, the argument
to have is: the default says 1:1, a 0.2:1 trade contradicts what the operator asked
for, and a floor is the only mechanism that makes the two agree. The argument
against: the upstream engine ranks candidates on its own ratio, so a floor silently
overrides an engine that already thought about it — and the trader chose the engine.
Recorded rather than resolved, and it should not be resolved without the maintainer
saying so.

---

## Environment / Setup Notes

**Current development machine — corrected in Phase 4.** The previous version of
this section described a different machine and different paths. It is replaced
rather than appended, because a stale environment section is worse than none: it
sends the next person to a directory that does not exist.

- Terminal: `C:\Program Files\<Broker> MT5\terminal64.exe`
- Data folder:
  `C:\Users\YourUser\AppData\Roaming\MetaQuotes\Terminal\0123456789ABCDEF0123456789ABCDEF`
- Workspace: `D:\Projects\signal-to-trade-bridge`
- Python: 3.12.9, the standard install at
  `C:\Users\YourUser\AppData\Local\Programs\Python\Python312\`
- Upstream checkouts: **not present on this machine.** `albrooks` is not
  installed, which is why `tests/integration/test_albrooks_real.py` skips at
  import rather than running its nine tests.

**None of these paths may appear in application logic.** They are recorded here
because a human needs them, and in `.env` files because configuration is
per-machine by definition. A path in the domain layer is a bug.

`D:\Projects\` is the *current* development path, not a requirement. Everything
except MT5 itself must work on a laptop where nothing lives on `D:`.

> **The terminal build is 6230, confirmed from three sources that agree:** the
> running terminal's own `terminal_info().build`, the live indicator snapshot's
> `terminal_build` field, and `terminal64.exe`'s version resource.
> `auto-trade`'s control ids were originally measured on **Alpari build 6184**, so
> the 46-build gap was real and Known Issue 5 was *confirmed*, not merely suspected.
>
> **It is now closed.** `auto-trade terminal-check` re-measured every control on
> 6230 and reported 13 of 13 OK, nothing drifted, nothing missing; the report is at
> `logs/terminal_check.json` and `MEASURED_ON_BUILD` is 6230. The gap being real and
> the gate now being satisfied are both true, and the second is a measurement rather
> than an edit — `tests/unit/test_control_id_evidence.py` fails if the constant moves
> without a recorded probe to match it.

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
| Local path | wherever you cloned it (no path is recorded in the repository) |
| Upstream A | `git@github.com:ybagheri/al-brooks-price-action-engine.git` |
| Upstream B | `git@github.com:ybagheri/auto-trade.git` |
| Python | `>=3.11` (the higher of the two upstreams' floors) |
| Platform | Windows for MT5; the domain and its tests are platform-independent |
| Dependencies | `albrooks` and `auto-trade` as editable local paths; `MetaTrader5` and `pywinauto` in a Windows extra |

---

## Git Status

> **Stale by design (Phase 16).** This list stops at Phase 7's commit and the upload this
> session worked from carried no `.git` directory, so no hash could be verified or
> recorded. Phase 16's changes are **uncommitted in the delivered tree**. The only
> authoritative state is `git status` and `git log` on your machine.

Branch `main`, tracking `origin/main`. The list below is the early history.

```
e559a4a  feat: the MT5 data adapter -- account and symbol facts from a real terminal
4f931f3  feat: the decision pipeline -- the first thing that calls the others
27bad30  docs: record the phase 5 commit hash, push status, and the floor question as open
3f8d21e  feat: finish the reward:risk policy, and fix four ways it was not what it said
7c9bb99  docs: record the phase 4 commit hash, push status, and what this machine could not verify
f250a4f  feat: risk management and position sizing, and fix the tick value divisor
631ec6a  docs: record the documentation-sync commit and note the one-commit lag
d7f093d  docs: correct stale sections in the handoff and sync both READMEs
2ea3954  docs: record the phase 3 commit hash, push status and the settled signal-id question
2ea3c93  feat: stop, take-profit and validation policies in the domain layer
683fbda  docs: record the phase 2 commit hash, push status and the settled signal-id question
6e264a2  feat: al brooks signal adapter, deterministic signal identity, mapping tests
8e4431e  docs: record the phase 1 commit hash and push status in handoff
dc336bd  feat: project foundation, domain layer, ports, config and test harness
19e4888  docs: record phase 0 commit hash and push status in handoff
68c8b87  docs: phase 0 architecture audit and integration contracts
```

---

## Latest Commit

```
e559a4a82f608f4ecf5cbce9b65ce8535d391eaf
feat: the MT5 data adapter -- account and symbol facts from a real terminal
```

**Push status: SUCCESS** — `51a84f1..e559a4a  main -> main` on
`git@github.com:ybagheri/signal-to-trade-bridge.git`. `origin/main` was read back
afterwards and matches the local head exactly.

> **This section is always one commit behind by construction.** It records the
> commit that contained the previous phase's work; recording the hash of the
> commit that records the hash is not possible. The authoritative head is
> `git log --oneline -n 1`, and the `## Git Status` list above is the one to
> trust for history.

### What could not be verified on this machine

Recorded rather than glossed over, because the alternative is a handoff that
claims more than anyone checked. Unchanged through Phases 4–6 — none of them
touched an adapter — and now partly relevant to Phase 7:

* **`pytest tests/integration` has not run in four phases.** The nine tests against
  the real `Analyzer` collect only when `albrooks` is installed, and it is not
  installed here. Phase 3 verified them green. Phases 4–7 touch no `albrooks`
  code, so they are *expected* to be unchanged — and they have **not** been run.
* **The MT5 field names are unverified.** `trade_tick_size`,
  `trade_tick_value_profit` and the rest are MT5's own and are taken from the
  Phase 0 audit of the bindings' contract, not from the installed package —
  `MetaTrader5` is **not installed here**. A rename would surface as an
  `AttributeError` at runtime, which is loud, but it is still a failure. The
  conversion logic is tested; the names are not. **Phase 11 exercises the real
  bindings** behind its opt-in marker.
* **The MT5 terminal build is 6230, and the control ids have been re-measured on
  it.** The 46-build gap from build 6184 was real, and it is now closed by
  measurement rather than by editing a number: `auto-trade terminal-check` reported
  13 of 13 controls OK on 6230, the report is at `logs/terminal_check.json`, and
  `MEASURED_ON_BUILD` is 6230. The gate is live again and will refuse the next MT5
  update, which is the design. No order was placed and no final control was clicked.
* **The `auto-trade` checkout is now here** at `D:\Projects\auto-trade`,
  which is what unblocks Phase 7b. `albrooks` is still absent, so `scripts/setup.ps1`
  has not been run and the nine real-`albrooks` integration tests cannot run.

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
  Python ≥3.11, ruff, mypy, pytest, the `mt5` marker). The domain layer: eleven
  frozen value objects, six enums, a 39-code `RejectionReason`, and twelve
  exception classes under two roots. Seven ports. `BridgeConfig` with 16
  `BRIDGE_*` settings that default to safe and fail loudly. Structured logging
  with a 13-event vocabulary and substring-based redaction. 126 tests, including
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

- **Phase 2** — built the first adapter. `adapters/albrooks/` with
  `identity.py` (deterministic `signal_id`), `mapper.py` (the anti-corruption
  layer), and `source.py` (`AlBrooksSignalSource`). `Signal.entry` became
  optional, because the engine returns `plan: None` on every abstention and
  requiring one would have forced the adapter to substitute the last close for an
  entry. The abstention path was split out so it needs no plan at all. Added
  `tests/stubs.py` built from the audit's verbatim quotes, 22 port-contract
  tests, 9 integration tests against the real `Analyzer`, and
  `docs/signal-flow.md`. 245 tests, 90% coverage.

  Four bugs found and fixed:
  - `Signal.entry` was mandatory in Phase 1, which made the commonest outcome in
    the system — "the engine found nothing to trade" — unmappable. It returned
    `None` with an empty reason.
  - An abstention was reported as `SIGNAL_DIRECTION_UNKNOWN` instead of being
    mapped, because the shared builder required an entry.
  - `source_direction` read the numeric `decision["direction"]` for abstentions,
    so a stray non-zero value on a `WAIT` would have produced a tradable signal.
    Now an abstention is `FLAT` unconditionally and the field is not read.
  - A missing `action` key was reported as an unknown action, sending an
    operator to look for a new enum member instead of at the upstream contract.

  One test was wrong rather than the code: it asserted that two float timestamps
  differing at the 16th digit produce different keys, but Python silently rounds
  `1727740800.0000001` to `1727740800.0`, so it would have passed whether or not
  the renderer worked. It now asserts the fixture values are distinct doubles
  *before* checking the keys.

  Settled the architecture audit's open question about `signal_id`: the prices
  are excluded and the bar is the unit of identity. Reasoning and the accepted
  trade-off are recorded above, and Phase 9 must revisit it against real engine
  output.

- **Phase 3** — gave the domain its policies. `domain/resolution.py` (one
  `Resolution[T]` for every two-outcome step), `domain/stops.py` (the stop
  policy, with the no-invented-stop rule enforced by an AST test),
  `domain/take_profit.py` (four policies, a fallback never silent), and
  `domain/validation.py` (four validation layers). Moved the structural-basis
  vocabularies out of the adapter and into the domain, because deciding what
  counts as a defensible stop is a trading policy. Added
  `docs/risk-management.md`. 432 tests, 94% coverage, the four new files at 100%.

  Three bugs found and fixed:
  - A missing stop escaped as a `ValueError` instead of a reason code.
    `resolve_stop` passed a zero price straight to `StopLoss`, whose own
    validation raises — so the commonest outcome in the system arrived as an
    exception, and a trading loop that did not catch it would crash on a quiet
    market.
  - `Resolution` could not express "succeeded with no value". Found by the
    `TakeProfitSource.NONE` tests, the only policy that resolves to nothing.
  - A wrong-side stop discarded its computed distance on the way out, because the
    distance was calculated after the side check — and the distance is the first
    thing anyone looks at when asking why a stop was rejected.

  Two tests were wrong rather than the code, both for the same reason: an
  assertion written against a value Python silently rounds. One expected five
  distinct refusal codes and got four, because a missing stop and a zero stop are
  the same fault by design. The other compared a `Decimal` distance as a string,
  but `str(Decimal)` switches to exponent notation below `1e-6` — `1E-11`, not
  `0.00000000001`.

- **Phase 4** — built the sizing the whole project exists for. `domain/risk.py`
  (the risk budget, with the no-invented-balance rule enforced by an AST test
  exactly as the stop rule is, plus the currency-coherence check that has to run
  before any division can mean anything), `domain/sizing.py` (the tick-value
  sizer, the broker constraints, and three independent guards on the floor-up
  rule), `RiskBudget` as the twelfth value object, `application/risk_service.py`
  to obtain the account and symbol facts through the ports the domain may not
  import, and `adapters/fake/` with two provider fakes and three named symbol
  specifications. `docs/risk-management.md` rewritten around steps 7 and 8. 555
  tests, 95% coverage, the five new files at 100%.

  One real bug found and fixed, and it was in Phase 1's code:
  `SymbolSpec.conservative_tick_value` was `min(tick_value_profit,
  tick_value_loss)`. Because volume is inversely proportional to the tick value,
  the *smaller* value produces the *larger* position, so `min` was the least
  conservative choice available and under-sized every position on an instrument
  whose two values differ — by 2× in the worked example. Now `max`. The two
  Phase 1 tests that pinned the old behaviour were corrected rather than deleted,
  with the arithmetic in the comment, because they encoded exactly the reasoning a
  future contributor would otherwise re-invert.

  Two tests were wrong rather than the code: several assertions compared a
  `Decimal` as a string, and `Decimal` arithmetic both preserves an exponent
  (`10000 × 0.5 / 100` is `50.0`, not `50`) and runs division to 28 significant
  digits, so `0.50 / 300` is a long tail of 6s. They now compare numerically.

  **Two unreachable guards were deleted rather than kept.** The sizer had a
  `risk_per_unit <= 0` branch that could not fire, because a positive distance, a
  positive tick size and a positive tick value cannot produce a zero product. An
  unreachable guard reads as though it handles a case it does not; the reasoning
  became a comment explaining why the division cannot fail. Two guards that
  *are* one edit away — an over-budget size, and a volume failing its own
  broker-constraint check — were kept, and each has a test that drives its branch
  by making the bad edit for real.

  `STOP_RESOLVED` and `TAKE_PROFIT_RESOLVED` are still **not** emitted, and this
  was a deliberate consequence rather than an oversight: Phase 4 established that
  the domain has no logger and must not acquire one, so the two Phase 3 events
  wait for Phase 6's `ProcessSignal`, which is the first place that can emit them.

- **Phase 5** — finished the reward:risk policy, and found it was not finished. A
  full audit of the chain from `BRIDGE_REWARD_RISK_RATIO` through configuration,
  `RiskParameters`, `resolve_take_profit`, `RiskBudget` and `TradeIntent` found
  that under the **default** policy the configured 1:1 was not a floor: nothing
  compared the signal's implied ratio against anything, so a structurally sound
  target at 0.2:1 was accepted silently. Added `BRIDGE_MINIMUM_REWARD_RISK_RATIO`
  as an **opt-in** floor (unset by default, so behaviour is unchanged), plus
  `achieved_ratio`, `canonical_ratio` and `signal_target_ratio`. 614 tests, 96%
  coverage, every file touched at 100%.

  The floor was escalated to the maintainer rather than decided here, because it
  changes which trades the bridge takes. Three options went on the table and
  **opt-in** was chosen; see Q6, which also records why the other two were
  rejected and why the question is left open rather than quietly settled.

  Four bugs found and fixed, three of them in code Phases 1 and 3 had written and
  called complete:
  - `config.py` used `risk_percent=_decimal(...) or Decimal("0.5")` and the same
    for the reward ratio. `Decimal("0")` is falsy, so `BRIDGE_RISK_PERCENT=0`
    started the bridge at **0.5%** and `BRIDGE_REWARD_RISK_RATIO=0` at **1:1**.
    Phase 1 had already identified this idiom as a bug and fixed it for
    `BAR_COUNT`; it was left in the two fields where it costs money.
  - `achieved_ratio` did not exist on the `TakeProfitSource.NONE` path, so indexing
    it raised `KeyError` on a resolution that had already succeeded.
  - `TradeIntent` required a `take_profit`, so a supported, documented, tested
    policy made the class unconstructible — and `to_dict()` divided by a take
    profit it assumed was there.
  - The ratio was computed in five places. Now one `achieved_ratio()`, with an AST
    test asserting `resolve_take_profit` performs no division of its own.

  Also removed: `_MIN_RATIO`, a constant whose docstring described a minimum-ratio
  clamp that did not exist and which nothing referenced. A dead guard is worse
  than no guard, because it reads as though it handles a case it does not.

- **Phase 6** — assembled the pipeline. `application/process_signal.py`:
  `ProcessSignal.process(signal) -> TradeDecision`, running every stage in order
  and stopping at the first refusal. `RiskService` gained a resolve half
  (`budget_for`, `size_for`) so the account and the specification are one snapshot
  per decision rather than two. `STOP_RESOLVED`, `TAKE_PROFIT_RESOLVED` and
  `TRADE_VALIDATED` are finally emitted — the first two had been reserved since
  Phase 1 and un-emitted for two phases, because Phase 4 established that the
  domain must not hold a logger. 665 tests, 96% coverage.

  No new bug in the domain, and that is worth noting: this phase found no defect
  in code the earlier phases wrote. It found the *absence of a connection*, which
  is the one class of problem only a phase that composes can find, and which five
  phases of individually-correct functions could not.

  One finding recorded rather than engineered around: **the geometry stage cannot
  refuse a signal that reaches it**, because every condition it checks was already
  checked by an earlier stage on the same objects. It is kept — the Phase 0
  rationale for the duplication still holds — and the honest position is written
  down and pinned by a test. `process_signal.py` is therefore at 99%, the one
  uncovered line being that refusal.

- **Phase 7** — both adapters, and they are the two halves of the same idea.

  **The data half**, `adapters/mt5/`: `bindings.py` (three Protocols,
  `MT5Unavailable`, and the project's single `import MetaTrader5`), `account.py`,
  `symbols.py` and `positions.py`. 92 tests against an injected bindings module: no
  terminal, no bindings import, and the whole suite runs on a machine that has
  neither.

  **The execution half**, `adapters/auto_trade/`: `bindings.py` (eight declared
  upstream names and the project's single `import auto_trade`), `executor.py` and
  `FakeTradeExecutor` in `adapters/fake/`. 50 tests, of which six run the **real**
  `ExecutionWorkflow`, `RiskEngine`, `JsonExecutionLedger` and
  `DryRunTerminalAdapter` -- because the maintainer's checkout is installed editable,
  so the adapter is verified against the thing it calls on every suite run rather
  than against a transcription of it.

  850 tests, 98% coverage, every new file at 100%.

  **One bug the tests caught in this phase's own code,** which is the argument for
  having written them: `load_bindings()` sat outside the `try` that turns a
  translation failure into an `UNKNOWN` result, so on a machine without the checkout
  a missing package raised out of `submit()` -- and an exception out of `submit()`
  loses the trade record, on exactly the situation where an operator most needs to
  see which signal did not go out.

  **The `open_positions` gap is closed, with a residual case.** It was always zero,
  because `account_info()` does not carry a position count — and a configured
  `BRIDGE_MAX_OPEN_POSITIONS` therefore admitted a trade it should have refused. The
  fix reads the terminal's own published snapshot
  (`MQL5\Files\auto_trade_positions_[ab].json`), written by the
  `AutoTradePositionReader` indicator and confirmed against that indicator's source.
  What remains: **an unreadable snapshot still yields zero**, because refusing would
  make an unreadable indicator indistinguishable from an unreachable terminal. So an
  unset `BRIDGE_MAX_OPEN_POSITIONS` is still the only correct configuration, and a
  set one is a gate that has a known fail-open path. That case is stated in the
  config docs rather than hidden.

---

- **Phase 8** — dry run reporting. `application/dry_run.py`, plus
  `adapters/auto_trade/preflight.py`. 39 tests.

  **The content of the phase is one disagreement made visible.** This bridge
  validates against its own rules and `auto-trade` validates against its own rules
  again, and the two disagree by default: its allow-list is `EURUSD,XAUUSD,YM` with a
  1.0 volume cap, while this project's come from the operator's configuration. **A
  signal on `GBPJPY` passes every check here and is refused downstream** — and the
  only place that used to become visible was a `REJECTED` result on a live account.
  The dry run now asks `auto-trade`'s own `RiskEngine`, which is a pure function of
  the request, and reports the answer as a blocker with the reason verbatim.

  **A prediction is marked as one.** `DownstreamVerdict` carries `evaluated`, because
  the real gates also depend on the account type, the kill switch, the ledger and the
  terminal window. With no engine wired it reports `evaluated=False` and **never**
  `accepted=True`; `accepted=True` with `evaluated=False` raises, because those two
  are indistinguishable downstream and only one of them is true.

  **`ExecutionRequest` had no producer.** The type the executor accepts was never
  constructed anywhere in `src/` before this phase — the whole mapping existed only as
  a hand-written test fixture. `build_execution_request` is that producer, and it
  copies every number rather than recomputing any: a dry run that re-derived the volume
  would be a second sizing pass, and two passes that disagree describe an order the
  bridge would never send.

  **A gap left open on purpose.** `ExecutionRequest.take_profit` is required while
  `TradeIntent`'s is optional, so `TakeProfitSource.NONE` — supported, documented,
  tested — cannot be expressed. Phase 9 fixes the type alongside the ledger.

  Still no `TradeExecutor` anywhere in the pipeline. The tests enforcing that check
  the AST rather than the source text, because all four forbidden names appear in
  `preflight.py`'s docstrings explaining why it may not use them.

  850 tests, 98% coverage, both new files at 100%.


## How To Continue

**Before doing anything else:**

1. Read `HANDOFF.md` — this file.
2. `git status`
3. `git log --oneline -n 10`
4. Run the suite: `.\scripts\test.ps1`, or `python -m pytest -q` if the
   virtual environment is not set up. **1184 passed, 84 skipped on a machine without the private projects or a terminal** (see Phase 16). If
   they do not, the repository is not in the state this file describes, and the
   repository wins.
5. Read `docs/architecture.md` §4 (the gap analysis), §5 (the design) and **§9
   (the safety invariants — later phases must not relax them)**.
6. Read `docs/signal-flow.md` for what currently works and where the path stops.
7. Read `docs/risk-management.md` for the policies, steps 7 and 8, and what is
   still missing.
8. Read `docs/integration.md` §3 and §4 — the consolidated gaps and the
   do-not-assume checklist.
9. Verify the actual repository state against this file. **If they conflict, the
   repository wins and this file must be corrected.**

**Then pick up from `ROADMAP.md`.** The next piece of work is Phase 17: verifying the
One Click Trading guard on a real terminal (Phase 15 steps 4–6). It needs a Windows
machine with a running MT5 terminal logged into a **demo** account, the private
`auto_trade` checkout, and a person watching; nothing in it can be done from a script
or from a clean machine. Do not enable Algo Trading on the operator's behalf.

**On a machine without those**, the useful work available is documented in
`ROADMAP.md` under "Doable without a terminal" — none of it needs, or may touch, a
live terminal.

**Five AST tests before writing anything that touches money, ratios, or the
pipeline order:**

* [`tests/unit/test_stop_resolution.py::test_there_is_no_fallback_that_produces_a_stop`](tests/unit/test_stop_resolution.py)
* [`tests/unit/test_risk_budget.py::test_there_is_no_fallback_that_produces_a_balance`](tests/unit/test_risk_budget.py)
* [`tests/unit/test_position_sizing.py::test_there_is_no_fallback_that_produces_a_tick_value`](tests/unit/test_position_sizing.py)
* [`tests/unit/test_reward_risk_ratio.py::test_resolve_take_profit_never_divides`](tests/unit/test_reward_risk_ratio.py)

The first three enforce one rule stated three times — *nothing in this project
invents a stop, a balance or a tick value* — and will fail if the corresponding
module gains a numeric literal that could serve as one. The fourth enforces that
the reward:risk ratio is computed in exactly one place, so a policy cannot check
one ratio while a decision log reports another.

The fifth is not an AST test but is the same kind of rule:

* [`tests/unit/test_process_signal.py::test_this_module_holds_no_executor`](tests/unit/test_process_signal.py)

It asserts `process_signal.py` imports no `TradeExecutor`, `IdempotencyStore` or
`KillSwitch`. It is the enforcement behind *execution belongs to Phase 7, where the
kill switch, the ledger and the audit log come with it* — and **it should keep
passing until the ledger does**, not merely until an executor exists.

**A warning about this file's own claims, written after Phase 5 and still true.**
Phases 4 and 5 were each described here as "largely already implemented" or
"verify the wiring and add the missing coverage". Both were wrong, and both hid
real bugs in code with 200+ passing tests — a tick-value divisor that under-sized
positions, and a configuration idiom that silently swallowed a configured `0`.
**Where this file says a phase's output looks finished, read the source before
believing it.** A handoff that says "already done" is a claim to be checked,
exactly like a claim about a path.

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

---

## Pre-submit pause + order-comment policy (2026-10-08, after Phase 16)

Two execution/UI settings, both off by default; default behavior unchanged.

- `BRIDGE_PRE_SUBMIT_DELAY_ENABLED=false`, `MIN_MS=1000`, `MAX_MS=5000`: a fresh
  `randint(MIN, MAX)` pause per submission, taken in `AutoTradeExecutor.submit`
  after every bridge-side refusal (kill switch, translation, One Click mode
  check) and immediately before `workflow.execute` -- the first call that
  touches the terminal, so no UI state is held during the pause. Rolled
  duration logged as `PRE_SUBMIT_DELAY_APPLIED`. Sleeper and RNG injectable;
  the suite never sleeps. This is UI pacing, not anti-detection: no mouse
  movement, no fake input, no timing anywhere else.
- `BRIDGE_ORDER_COMMENT_ENABLED=false`: empty comment when off, the existing
  `default_comment` value (`stb SYMBOL LONG/SHORT setup-id`, <=64 chars) when
  on. Gated in `build_execution_request`, so all paths (live, dry-run,
  what-if) agree. Upstream already skips an empty comment (`window_manager.py`
  `if request.signal.comment`), so no upstream change was needed.
- `--what-if`/dry-run never sleep: the recorder (`FakeTradeExecutor`) rolls
  and records the pause it would have taken (`pre_submit_delays`), and the
  what-if output prints it. No order is submitted by tests or previews.
- Validation fails loudly: negative min, max < min, non-integers, and anything
  above one hour (`MAX_PRE_SUBMIT_DELAY_MS`) are refused at construction;
  `MIN_MS=0` is legitimate and survives env loading (no `or`-default).
- New files: `application/pre_submit.py`, `tests/unit/test_pre_submit.py`.
  Touched: `domain/models.py` (`PreSubmitDelay`), `configuration/config.py`,
  `adapters/auto_trade/executor.py`, `adapters/fake/executor.py`,
  `application/dry_run.py`, `application/process_signal.py`, `composition.py`,
  `cli/main.py`, `infrastructure/logging/events.py`, `.env.example`,
  `docs/setup.md`, `ROADMAP.md`. No strategy, risk, stop/TP, or safety-gate
  change. `E:\auto-trade` untouched: intra-dialog pacing would require an
  upstream change and was deliberately not made.