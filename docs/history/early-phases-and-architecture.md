> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

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

