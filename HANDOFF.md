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

**Phases 0, 1, 2, 3 and 4 complete. Phase 5 not started.**

Phase 0 audited the upstream repositories. Phase 1 built the foundation. Phase 2
built the first adapter. Phase 3 gave the domain its **policies**: stop resolution,
take-profit policy, and validation. Phase 4 added **risk management and position
sizing** — the phase the whole project exists for.

**554 tests passing, 95% coverage.** The five files this phase added have **100%
statement coverage**. Lint, format, type check and the domain-isolation check all
clean. The suite still runs **without `albrooks` installed**.

```
Last completed phase: 4
Current phase:        5 (not started)
Next phase:           5 — verify the 1:1 risk/reward wiring (largely built in Phase 3)
```

---

## Completed Phases

- [x] **Phase 0** — Repository discovery and architecture audit
- [x] **Phase 1** — Project foundation
- [x] **Phase 2** — Al Brooks signal adapter
- [x] **Phase 3** — Stop, take-profit and validation policies
- [x] **Phase 4** — Risk management and position sizing
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
  domain/          models.py     twelve value objects
                   enums.py      six enums, 39 rejection codes
                   errors.py     two roots, twelve exceptions
                   resolution.py Resolution[T]           Phase 3
                   stops.py      resolve_stop()          Phase 3
                   take_profit.py resolve_take_profit()  Phase 3
                   validation.py four validation layers  Phase 3
                   risk.py       resolve_risk_budget()   Phase 4
                                check_currency_compatibility()
                   sizing.py     resolve_position_size() Phase 4
                                check_broker_constraints()
                   no external deps at all -- enforced by test_domain_isolation
  ports/           __init__.py                        seven Protocols
  application/     risk_service.py                    Phase 4; ProcessSignal is Phase 6
  adapters/        albrooks/                          Phase 2, complete
                   fake/                              Phase 4, complete
                   auto-trade/  mt5/                   Phase 7
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
                   test_risk_service.py
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

`BridgeConfig` + `config_from_env`, reading 16 `BRIDGE_*` variables. Defaults are
the safe ones: `execution_enabled=False`, `dry_run=True`, `risk_percent=0.5`,
`reward_risk_ratio=1.0`.

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

**Documentation**: architecture, integration contracts, setup, signal flow, risk
management, bilingual README.

**Not yet implemented, and not to be assumed.** Everything from reading a signal
through sizing it works. Two things are missing, and they are different in kind:

- **Nothing connects a signal to a size.** `RiskService` takes a symbol, a stop
  and a risk configuration; it does not know what a `Signal` is. That
  composition — `ProcessSignal` — is Phase 6. Until then the pipeline can size a
  trade it has not been asked to size.
- **The account and symbol facts come from fakes.** In production they have to
  come from MetaTrader 5, which is Phase 7. Until then the numbers are real
  arithmetic over invented facts, which is exactly the situation the AST tests
  guard against.

Margin checking is also absent, and is recorded as a known gap rather than an
oversight: a position can pass every check in this project and still be refused by
the broker for margin.

The honest summary: the bridge can **understand** a signal, **validate** it,
**size** it, and say exactly what it would need in order to place it. It cannot
yet place one.

---

## Tests

**555 collected, 554 passed, 1 skipped in about 1.5 seconds.** The suite runs
**without `albrooks` installed** and without MetaTrader 5.

| File | Tests | Covers |
|---|---|---|
| `unit/test_domain_models.py` | 75 | Every value object, its validation, and its invariants. |
| `unit/test_stop_resolution.py` | 56 | The no-invented-stop rule, the basis policy, and every refusal path. |
| `unit/test_take_profit_policy.py` | 50 | The four policies, the ratio arithmetic, and provenance. |
| `unit/test_albrooks_mapper.py` | 49 | The mapping, against stubs shaped like the real engine's output. |
| `unit/test_validation.py` | 48 | The four validation layers, fail-closed behaviour. |
| `unit/test_position_sizing.py` | 46 | The sizer, the three floor-up guards, and the conservative tick value. |
| `unit/test_risk_service.py` | 35 | The wiring, the two reserved events, and the fakes as port implementations. |
| `unit/test_configuration.py` | 33 | Defaults, overrides, fail-loudly behaviour, dotenv parsing, repr redaction. |
| `unit/test_risk_budget.py` | 31 | The no-invented-balance rule, the refusals, and currency coherence. |
| `unit/test_logging.py` | 28 | Redaction cannot be bypassed, JSON shape, handler hygiene, event vocabulary. |
| `unit/test_albrooks_source.py` | 24 | Fetching, delegating, error paths, and the events emitted. |
| `unit/test_resolution.py` | 22 | The two-outcome type, including success-with-no-value. |
| `unit/test_ports.py` | 22 | Port narrowness, structural substitutability, annotation completeness. |
| `unit/test_signal_identity.py` | 16 | Determinism, the bar-as-unit-of-identity, and the encoding. |
| `unit/test_domain_isolation.py` | 13 | The architectural invariant, parametrised over every domain module. |
| `unit/test_defensive_guards.py` | 7 | Guards reachable only by bypassing model validation. |
| `integration/test_albrooks_real.py` | 9 | The real `Analyzer`, so the stubs cannot drift unnoticed. **Not collected here** — `albrooks` is not installed on this machine, so the module skips at import. |
| **Total** | **555 collected, 554 passed, 2 skipped** | |

> **The per-file counts in this table were wrong before this phase and are now
> measured rather than remembered.** The previous table claimed 72 tests in the
> mapper and 7 in the isolation test; the real figures are 49 and 13. The totals
> it reported were close to right for the wrong reasons. Nothing about the code
> was affected — only the record of it, which is the thing this file is for.

Coverage: **95%** of statements. The five files Phase 4 added — `risk.py`,
`sizing.py`, `application/risk_service.py`, `adapters/fake/account.py`,
`adapters/fake/symbols.py` — are at **100%**, as are the four Phase 3 files.
`ports/` shows 0% line coverage, which is expected for `Protocol` declarations and
says nothing; `unit/test_ports.py` checks their contract instead.

The full gate, all clean:

```
ruff check .            All checks passed!
ruff format --check .   63 files already formatted
mypy                    Success: no issues found in 32 source files
pytest                  554 passed, 2 skipped
```

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

### Three tests worth knowing about

`test_there_is_no_fallback_that_produces_a_stop` walks `stops.py`'s own AST and
asserts that no numeric literal in it could serve as a price and that every
`StopLoss` is built from the signal's own price.

`test_there_is_no_fallback_that_produces_a_balance` and
`test_there_is_no_fallback_that_produces_a_tick_value` are the same idea for
`risk.py` and `sizing.py`. The balance one additionally asserts that `risk.py`
never constructs an `AccountBalance` and that the budget's amount comes from
`RiskParameters.risk_amount` rather than a second implementation of the
percentage calculation.

All three are the enforcement behind one rule stated three times: **nothing in
this project invents a stop, a balance or a tick value.** The architectural test
covers imports; these cover the *absence of behaviour*, which is the thing a
reviewer has to be told about and the thing no import rule would catch. Read them
before adding anything to `stops.py`, `risk.py` or `sizing.py`.

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

### Phases 5–13

As laid out in `README.md`.

#### Phase 5 — 1:1 risk/reward  ← next, and the smallest remaining phase

**Largely already implemented.** The take-profit policy in Phase 3 applies the
configured ratio, and `TradeIntent.reward_to_risk` reports the *achieved* ratio
computed from the distances actually used rather than the configured one. Phase 5
should therefore verify the wiring and add the missing coverage, **not build a
second mechanism**. The useful work is:

* end-to-end coverage of the three stages that touch the ratio together — stop
  resolution, take-profit resolution, and now sizing — which needs `ProcessSignal`
  and is therefore really Phase 6 work;
* confirming `BRIDGE_REWARD_RISK_RATIO` flows through configuration into
  `RiskBudget.reward_amount`, which Phase 4 now produces but nothing yet asserts;
* the case where the configured ratio cannot be met because the signal's own
  target is closer — already handled by `RR_FALLBACK` and reported as
  `achieved_ratio`, and worth a test that says so.

If Phase 5 turns out to have nothing left to do, **say so in this file rather than
manufacturing work**, and move to Phase 6.

#### Phase 6 — trade validation pipeline

`ProcessSignal`, the single use case. This is where the phases compose:

```
signal → validate_signal → resolve_stop → resolve_take_profit → validate_geometry
       → validate_policy → validate_against_spec → RiskService.position_size
       → TradeIntent → TradeDecision
```

It is also where `STOP_RESOLVED`, `TAKE_PROFIT_RESOLVED` and `TRADE_VALIDATED`
finally get emitted — Phase 4 established that the domain has no logger, so the
two Phase 3 events are still un-emitted and this is the fix.

#### Phase 7 — adapters

The real `AccountProvider` and `SymbolSpecProvider` over the `MetaTrader5`
bindings, written against the contract `adapters/fake/` already pins, plus the
`auto-trade` execution adapter wrapping `ExecutionWorkflow`.

---

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
terminal `C:\Program Files\Alpari MT5_4\terminal64.exe`, data folder
`C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal\1D9617E1A6A4352DBDC25D08FEC12BD2`.
Not touched, and **must not be touched outside Phase 11**. The build number is
unverified; `auto-trade`'s control ids were measured on Alpari build 6184, and
this is an `_4` installation, so Known Issue 5 is a live risk.

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

**Current development machine — corrected in Phase 4.** The previous version of
this section described a different machine and different paths. It is replaced
rather than appended, because a stale environment section is worse than none: it
sends the next person to a directory that does not exist.

- Terminal: `C:\Program Files\Alpari MT5_4\terminal64.exe`
- Data folder:
  `C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal\1D9617E1A6A4352DBDC25D08FEC12BD2`
- Workspace: `D:\Projects\signal-to-trade-bridge`
- Python: 3.12.9, the standard install at
  `C:\Users\BazikadeStore\AppData\Local\Programs\Python\Python312\`
- Upstream checkouts: **not present on this machine.** `albrooks` is not
  installed, which is why `tests/integration/test_albrooks_real.py` skips at
  import rather than running its nine tests.

**None of these paths may appear in application logic.** They are recorded here
because a human needs them, and in `.env` files because configuration is
per-machine by definition. A path in the domain layer is a bug.

`D:\Projects\` is the *current* development path, not a requirement. Everything
except MT5 itself must work on a laptop where nothing lives on `D:`.

> **The terminal build is unverified.** `auto-trade`'s control ids were measured
> on **Alpari build 6184**, and this machine has Alpari MT5 `_4` at an unknown
> build. Known Issue 5 applies, and Phase 11 must confirm it before anything is
> clicked. Do not open the terminal outside Phase 11.

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
| Local path | `D:\Projects\signal-to-trade-bridge` |
| Upstream A | `git@github.com:ybagheri/al-brooks-price-action-engine.git` |
| Upstream B | `git@github.com:ybagheri/auto-trade.git` |
| Python | `>=3.11` (the higher of the two upstreams' floors) |
| Platform | Windows for MT5; the domain and its tests are platform-independent |
| Dependencies | `albrooks` and `auto-trade` as editable local paths; `MetaTrader5` and `pywinauto` in a Windows extra |

---

## Git Status

Branch `main`, tracking `origin/main`. Working tree state at the time Phase 4
was written: see the commit list below.

```
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
f250a4fb1a735cf9dd9187237796215374390287
feat: risk management and position sizing, and fix the tick value divisor
```

**Push status: SUCCESS** — `631ec6a..f250a4f  main -> main` on
`git@github.com:ybagheri/signal-to-trade-bridge.git`. `origin/main` was read back
afterwards and matches the local head exactly.

> **This section is always one commit behind by construction.** It records the
> commit that contained the previous phase's work; recording the hash of the
> commit that records the hash is not possible. The authoritative head is
> `git log --oneline -n 1`, and the `## Git Status` list above is the one to
> trust for history.

### What could not be verified on this machine

Recorded rather than glossed over, because the alternative is a handoff that
claims more than anyone checked:

* **`pytest tests/integration` did not run.** The nine tests against the real
  `Analyzer` collect only when `albrooks` is installed, and it is not installed
  here — the module skips at import. The previous phase verified them green and
  Phase 4 touches no adapter code, so they are *expected* to be unchanged. They
  have **not** been run. Run `pytest tests/integration -v` on a machine with the
  upstream checkout before relying on that.
* **The MT5 terminal build is unknown.** This machine has Alpari MT5 `_4`, and
  `auto-trade`'s control ids were measured on build 6184. Nothing was clicked and
  no terminal was opened, so nothing is broken — but Known Issue 5 is unresolved
  and Phase 11 owns it.
* **No upstream checkouts exist here.** `E:\al-brooks-price-action-engine` and
  `E:\auto-trade` from the old environment section are gone; this machine has
  neither. `scripts/setup.ps1` has not been run.

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

---

## How To Continue

**Before doing anything else:**

1. Read `HANDOFF.md` — this file.
2. `git status`
3. `git log --oneline -n 10`
4. Run the suite: `.\scripts\test.ps1`, or `python -m pytest -q` if the
   virtual environment is not set up. **554 tests should pass, 2 skipped.** If
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

**Then start Phase 5** from the Remaining Work list above — and read that section
first, because Phase 5 is **largely already implemented** and may have nothing
left to do. If that is what you find, say so here rather than manufacturing work,
and move to Phase 6.

**Before writing anything that touches money, read all three AST tests:**

* [`tests/unit/test_stop_resolution.py::test_there_is_no_fallback_that_produces_a_stop`](tests/unit/test_stop_resolution.py)
* [`tests/unit/test_risk_budget.py::test_there_is_no_fallback_that_produces_a_balance`](tests/unit/test_risk_budget.py)
* [`tests/unit/test_position_sizing.py::test_there_is_no_fallback_that_produces_a_tick_value`](tests/unit/test_position_sizing.py)

They are the enforcement behind one rule stated three times — *nothing in this
project invents a stop, a balance or a tick value* — and they will fail if the
corresponding module gains a numeric literal that could serve as one. A contributor
adding anything to `stops.py`, `risk.py` or `sizing.py` should read these first.

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
