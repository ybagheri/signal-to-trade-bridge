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

**Phases 0, 1, 2 and 3 complete. Phase 4 not started.**

Phase 0 audited the upstream repositories. Phase 1 built the foundation. Phase 2
built the first adapter. Phase 3 gave the domain its **policies**: stop
resolution, take-profit policy, and validation.

**432 tests passing, 94% coverage.** The four files this phase added have
**100% statement coverage**. Lint, format, type check and the domain-isolation
check all clean. The suite still runs **without `albrooks` installed**.

```
Last completed phase: 3
Current phase:        4 (not started)
Next phase:           4 — risk management and position sizing
```

---

## Completed Phases

- [x] **Phase 0** — Repository discovery and architecture audit
- [x] **Phase 1** — Project foundation
- [x] **Phase 2** — Al Brooks signal adapter
- [x] **Phase 3** — Stop, take-profit and validation policies
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

## The Foundation, Phases 1 to 3

### Layout

```
src/signal_to_trade_bridge/
  domain/          models.py     eleven value objects
                   enums.py      six enums, 39 rejection codes
                   errors.py     two roots, twelve exceptions
                   resolution.py Resolution[T]           Phase 3
                   stops.py      resolve_stop()          Phase 3
                   take_profit.py resolve_take_profit()  Phase 3
                   validation.py four validation layers  Phase 3
                   no external deps at all -- enforced by test_domain_isolation
  ports/           __init__.py                        seven Protocols
  application/     __init__.py                        empty; ProcessSignal is Phase 6
  adapters/        albrooks/                          Phase 2, complete
                   auto-trade/  mt5/  fake/           Phase 4 fakes, Phase 7 real
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
  integration/     test_albrooks_real.py            real Analyzer, opt-in
docs/              architecture.md  integration.md  setup.md
                   signal-flow.md  risk-management.md
scripts/           setup.ps1  setup.sh  test.ps1  test.sh
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

Phase 3 added the **policies** over those values: `Resolution`, the stop
resolver, the take-profit resolver, and four validation layers. All four files are
at 100% statement coverage.

### Ports

**Seven** `Protocol`s: `SignalSource`, `MarketDataProvider`, `AccountProvider`,
`SymbolSpecProvider`, `TradeExecutor`, `IdempotencyStore`, `KillSwitch`. Narrow
on purpose, because a test has to fake them.

`AccountProvider` and `SymbolSpecProvider` have **no implementation yet** — they
are Phase 4's dependency, and the reason position sizing cannot be written
without either fakes or the real MT5 adapter.

### Configuration

`BridgeConfig` + `config_from_env`, reading 16 `BRIDGE_*` variables. Defaults are
the safe ones: `execution_enabled=False`, `dry_run=True`, `risk_percent=0.5`,
`reward_risk_ratio=1.0`.

### Logging

`Event` enum with 12 event names, `StructuredLogger`, JSON and text formatters,
and substring-based redaction of 13 sensitive key markers.

**Three of the twelve events are emitted so far**: `SIGNAL_RECEIVED`,
`SIGNAL_REJECTED`, `SIGNAL_SOURCE_UNAVAILABLE`. The other nine — `STOP_RESOLVED`,
`TAKE_PROFIT_RESOLVED`, `RISK_CALCULATED`, `POSITION_SIZED`, `TRADE_VALIDATED`,
`TRADE_REJECTED`, `DRY_RUN_COMPLETED`, `EXECUTION_RESULT`, `DUPLICATE_SUPPRESSED`
— are defined and reserved, and each is emitted by the phase that introduces it.
A test asserts the vocabulary is complete, so an event cannot be quietly dropped.

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

Proposed in Phase 0, scaffolded in Phase 1, first adapter in Phase 2, policies in
Phase 3. Full rationale in `docs/architecture.md §5`.

```
interfaces/     CLI, wiring, composition root          (Phase 8)
application/    ProcessSignal — the single use case    (Phase 6)
domain/         value objects (Phase 1) + policies: stops, take_profit,
                validation, resolution (Phase 3); sizing and risk (Phase 4)
ports/          Protocols: SignalSource, MarketDataProvider, AccountProvider,
                SymbolSpecProvider, TradeExecutor, IdempotencyStore, KillSwitch
adapters/       albrooks/  auto-trade/  mt5/  fake/
                albrooks → Phase 2, COMPLETE
                fake → Phase 4 (unblocks the sizer's tests)
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
| Refuse an *unrecognised* stop basis too, even when fallbacks are allowed | The flag names one specific alternative, not a general licence. The two refusals have different remedies, so their messages say so. |
| `Decimal` in the domain | Float drift in position sizing is a monetary error. |
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

**Documentation**: architecture, integration contracts, setup, signal flow, risk
management, bilingual README.

**Not yet implemented, and not to be assumed: position sizing.** Everything up to
and including validation works. The moment a trade would be sized, the pipeline
stops, because there is no account balance and no symbol specification to size it
from — neither upstream project has either. Phases 4 and 7 build those.

The honest summary: the bridge can **understand** a signal, **validate** it, and
say exactly what it would need in order to trade it. It cannot yet size it, and
therefore cannot trade it.

---

## Tests

**432 tests, all passing, in about 1.5 seconds.** The suite runs **without
`albrooks` installed** and without MetaTrader 5, and the integration tests against
the real engine skip cleanly.

| File | Tests | Covers |
|---|---|---|
| `unit/test_albrooks_mapper.py` | 72 | The mapping, against stubs shaped like the real engine's output. |
| `unit/test_stop_resolution.py` | 56 | The no-invented-stop rule, the basis policy, and every refusal path. |
| `unit/test_domain_models.py` | 70 | Every value object, its validation, and its invariants. |
| `unit/test_take_profit_policy.py` | 50 | The four policies, the ratio arithmetic, and provenance. |
| `unit/test_validation.py` | 48 | The four validation layers, fail-closed behaviour. |
| `unit/test_configuration.py` | 33 | Defaults, overrides, fail-loudly behaviour, dotenv parsing, repr redaction. |
| `unit/test_logging.py` | 28 | Redaction cannot be bypassed, JSON shape, handler hygiene, event vocabulary. |
| `unit/test_resolution.py` | 22 | The two-outcome type, including success-with-no-value. |
| `unit/test_albrooks_source.py` | 23 | Fetching, delegating, error paths, and the events emitted. |
| `unit/test_ports.py` | 22 | Port narrowness, structural substitutability, annotation completeness. |
| `unit/test_signal_identity.py` | 16 | Determinism, the bar-as-unit-of-identity, and the encoding. |
| `unit/test_defensive_guards.py` | 7 | Guards reachable only by bypassing model validation. |
| `unit/test_domain_isolation.py` | 7 | The architectural invariant: the domain imports nothing external. |
| `integration/test_albrooks_real.py` | 9 | The real `Analyzer`, so the stubs cannot drift unnoticed. |
| **Total** | **432, 1 skipped** | |

Coverage: **94%** of statements. The four files Phase 3 added —
`resolution.py`, `stops.py`, `take_profit.py`, `validation.py` — are at
**100%**. `ports/` shows 0% line coverage, which is expected for `Protocol`
declarations and says nothing; `unit/test_ports.py` checks their contract instead.

The full gate, all clean:

```
ruff check .            All checks passed!
ruff format --check .   53 files already formatted
mypy                    Success: no issues found in 27 source files
pytest                  432 passed, 1 skipped
pytest tests/integration  9 passed  (verified green against the real engine)
```

`mypy` reports one informational note — an unused `[[tool.mypy.overrides]]` block
for `MetaTrader5` and `auto_trade`. It becomes used in Phase 7 when the adapters
that import them are written. Deliberately left in place before the import exists,
so the ignore is already there when the import arrives.

**A skip is not a pass.** Both upstream projects make this point about their own
live tests, and it applies here. Run `pytest tests/integration -v` explicitly
before trusting an adapter change.

### Two tests worth knowing about

`test_there_is_no_fallback_that_produces_a_stop` walks `stops.py`'s own AST and
asserts that no numeric literal in it could serve as a price and that every
`StopLoss` is built from the signal's own price. It is the enforcement behind the
project's central rule, and it is the test a future contributor should read before
adding anything to that module.

`test_a_missing_evidence_score_is_refused_not_allowed_through` pins the
fail-closed direction of the evidence filter. A missing value counting as a pass
would make the filter trivially bypassable by any source that omitted the field.

### Tests deliberately not written yet

Writing these before the code they test exists would be theatre:

* position-sizing tests — Phase 4
* the end-to-end pipeline — Phase 6
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

### Phase 4 — risk management and position sizing  ← next

**The substantial phase, and the one the whole project exists for.** Neither
upstream project can supply any of it — verified in the Phase 0 audit — so this
is new code, not adaptation.

Files to create, all in `domain/`:

| File | Contents |
|---|---|
| `risk.py` | The risk amount from a balance and a percentage, and the refusals when a balance or a specification is unavailable. |
| `sizing.py` | The tick-value position sizer, step rounding, and the broker constraints. |

The formula, from `docs/architecture.md §5.6`:

```
risk_amount      = balance × risk_percent / 100
ticks            = |entry − stop| / tick_size
risk_per_unit    = ticks × conservative_tick_value
raw_volume       = risk_amount / risk_per_unit
volume           = round_down_to_step(raw_volume)   then clamp to [min, max]
```

**Why tick-value based rather than a pip formula.** It is what makes the sizer
correct for gold, indices, CFDs and futures-like instruments with no special
cases. A 5-digit EURUSD and a 2-decimal XAUUSD both reach `$300` per lot over a
stop, by completely different arithmetic — `0.00300 / 0.00001 × $1` and
`3.00 / 0.01 × $1`. A sizer assuming a 100000 contract size and a 5-digit pair
would be wrong on gold by two orders of magnitude.

**Use the worse of `tick_value_profit` and `tick_value_loss`.** They can differ on
a hedging account and on some CFDs. A size that is safe on paper has to be safe on
the losing side.

### The rules Phase 4 must not break

These are already enforced in Phases 1 and 3, and Phase 4 is where they become
load-bearing:

1. **A volume below `volume_min` is a refusal, never a floor-up.** Enforced twice:
   `SymbolSpec.clamp_volume` returns zero below the minimum, and
   `PositionSize.__post_init__` raises if `clamped_to_minimum` is ever set. A
   sizer that can exceed its own budget is the failure this project exists to
   prevent.
2. **Round to `volume_step` down.** Rounding up can exceed the budget; down leaves
   it fractionally under, which is the safe direction.
3. **Clamping down to `volume_max` is allowed and must be recorded** — the real
   risk is then *below* the budget, which is a fact the log should state.
4. **`PositionSize` keeps every input.** A volume alone is not auditable; the
   balance, percentage, stop distance, tick size and tick value that produced it
   can be recomputed by hand.

### The adapter work Phase 4 also needs

`AccountProvider` and `SymbolSpecProvider` have no implementation yet, and the
sizer cannot be tested end to end without them. Two options, and the decision
belongs to the maintainer (Open Question 2):

* a `FakeAccountProvider` / `FakeSymbolSpecProvider` in `adapters/fake/`, which
  unblocks Phase 4's tests immediately
* the real MT5 adapter in `adapters/mt5/`, which is Phase 7 work

**Recommendation: write the fakes in Phase 4.** They are small, they unblock the
tests that matter, and the real adapter in Phase 7 can then be written against
tests that already pin the contract. Doing the real adapter first would mean
writing it with nothing to check it against.

### Phases 5–13

As laid out in `README.md`. Note that Phase 5 (1:1 R:R) is **largely already
implemented** — the take-profit policy in Phase 3 applies the configured ratio.
Phase 5 should verify the wiring and add the missing coverage, not build a second
mechanism.

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

**Q3. Live MT5 validation target.** Alpari demo, terminal at
`C:\Users\bagheri\AppData\Roaming\Alpari MT5\terminal64.exe`, data folder
`C:\Users\bagheri\AppData\Roaming\MetaQuotes\Terminal\1BFBA8D123B04AAD5E48746348E9B594`.
Not touched yet. Phase 11 must confirm the terminal build matches what
`auto-trade`'s control ids were measured on (Known Issue 5).

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
verified identical at the end of Phase 3.

```
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
2ea3c93d21f9598d814730bc75e16a27b4091419
feat: stop, take-profit and validation policies in the domain layer
```

**Push status: SUCCESS** — `683fbda..2ea3c93  main -> main` on
`git@github.com:ybagheri/signal-to-trade-bridge.git`. `origin/main` was read back
afterwards and matches the local head exactly.
```
6e264a25f198cdf68a1d50db074906c9ea002751
feat: al brooks signal adapter, deterministic signal identity, mapping tests
```

**Push status: SUCCESS** — `8e4431e..6e264a2  main -> main` on
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
  Python ≥3.11, ruff, mypy, pytest, the `mt5` marker). The domain layer: eleven
  frozen value objects, six enums, a 39-code `RejectionReason`, and twelve
  exception classes under two roots. Seven ports. `BridgeConfig` with 16
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

---

## How To Continue

**Before doing anything else:**

1. Read `HANDOFF.md` — this file.
2. `git status`
3. `git log --oneline -n 10`
4. Run the suite: `.\scripts\test.ps1`, or `python -m pytest -q` if the
   virtual environment is not set up. **432 tests should pass.** If they do not,
   the repository is not in the state this file describes, and the repository
   wins.
5. Read `docs/architecture.md` §4 (the gap analysis), §5 (the design) and **§9
   (the safety invariants — later phases must not relax them)**.
6. Read `docs/signal-flow.md` for what currently works and where the path stops.
7. Read `docs/risk-management.md` for the policies and the Phase 4 boundary.
8. Read `docs/integration.md` §3 and §4 — the consolidated gaps and the
   do-not-assume checklist.
9. Verify the actual repository state against this file. **If they conflict, the
   repository wins and this file must be corrected.**

**Then start Phase 4** from the Remaining Work list above: `domain/risk.py`,
`domain/sizing.py`, and the fakes that unblock their tests.

**Before writing the sizer, read
[`tests/unit/test_stop_resolution.py::test_there_is_no_fallback_that_produces_a_stop`](tests/unit/test_stop_resolution.py).**
It is the enforcement behind the project's central rule, and it will fail if
anything in `stops.py` gains a numeric literal that could serve as a price. The
same instinct applies to `sizing.py`: the sizer must not be able to invent an
account balance or a tick value any more than the stop resolver invents a stop.

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
