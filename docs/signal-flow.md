# Signal Flow

How a reading becomes an order, and where it can stop.

This document is the operational counterpart to
[`architecture.md`](architecture.md): that one explains why the design is shaped
this way, this one says what happens, in order, and what each step refuses.

---

## The pipeline

```
Market data
    ↓
[1] Al Brooks engine                 albrooks.Analyzer.analyze(...)     Phase 0
    ↓  AnalysisResult.decision  (a dict, 3 or 13 keys)
[2] Signal adapter                   → internal Signal                  Phase 2
    ↓
[3] Signal validation               validate_signal                    Phase 3
    ↓
[4] Stop resolution                 resolve_stop                       Phase 3
    ↓
[5] Take-profit resolution          resolve_take_profit                Phase 3, 5
    ↓
[6] Geometry                        validate_geometry                  Phase 3
    ↓
[7] Policy                          validate_policy                    Phase 3
    ↓
[8] Account + symbol data            RiskService.read_account/read_spec  Phase 4
    ↓                                                                  real: Phase 7
[9] What the symbol can express      validate_against_spec             Phase 3
    ↓
[10] Risk amount                     resolve_risk_budget               Phase 4
    ↓
[11] Position size                   resolve_position_size             Phase 4
    ↓
[12] The decision                    ProcessSignal.process             Phase 6  ← IMPLEMENTED
    ↓
[13] Idempotency                                               Phase 9  ← IMPLEMENTED
    ↓
[14] Dry run?  record, stop                          Phase 8  ← IMPLEMENTED
    ↓
[15] Execution                                               Phase 9  — REACHABLE
    ↓
MetaTrader 5 → broker
```

**Every stage from 2 to 12 is implemented, tested and — since Phase 6 — called in
order by a single function.** `ProcessSignal.process(signal) -> TradeDecision`
runs steps 3 to 12, stops at the first refusal, and returns either a `NO_TRADE`
carrying a reason code or a `DRY_RUN` carrying a fully sized `TradeIntent`.

It still cannot place an order, and the reason is now a deliberate one rather than
an unfinished phase. `AutoTradeExecutor` exists and is tested against the execution
project's real `ExecutionWorkflow`, but it is **not wired into step 15** yet:
`test_this_module_holds_no_executor` asserts the pipeline imports no `TradeExecutor`,
and it keeps passing on purpose. Execution arrives with the idempotency ledger
(Phase 9) and the kill switch around it, not before — an executor on the pipeline with
no ledger behind it is a second way to open a duplicate position, which is the one
failure this architecture exists to prevent.

So a passing trade still comes back as `DRY_RUN`: "every check passed, nothing was
sent, and nothing could have been".

**Since Phase 8, that report is in full.** `application/dry_run.py` turns the
validated intent into the `ExecutionRequest` that *would* be sent, and answers the
question a dry run exists to answer before real money is involved: what exactly
would this system do?

```
DRY_RUN
  request          the order: volume, entry, stop, target, comment, strategy
  arithmetic       the sizing behind it, and the balance it came from
  downstream       what auto-trade's own risk gates would say
  blockers         everything standing between here and a placed order
```

**The `downstream` row is the part that was invisible before.** This bridge
validates against its own rules and `auto-trade` validates against its own rules
again, and the two rulebooks disagree by default: its default allow-list is
`EURUSD,XAUUSD,YM` with a 1.0 volume cap, while this bridge's limits come from the
operator's configuration. **A signal on `GBPJPY` passes every check here and is
refused downstream** — and until this phase the only place that became visible was a
`REJECTED` result on a live account. The dry run now asks `auto-trade`'s own
`RiskEngine`, which is a pure function of the request, and reports the answer.

It is a **prediction, and the report says so**: the real gates also depend on the
account type, the kill switch, the ledger and the terminal window. When no engine is
wired the verdict is `evaluated=False`, never `accepted=True` — an unevaluated gate
that reads as a pass is the specific silence this phase exists to prevent.

**Since Phase 9, step 15 is reachable** — and only with its whole envelope.

```
ProcessSignal
  ├─ wire_execution(ExecutionEnvelope)
  │      └─ executor + idempotency ledger + kill switch   all three, or none of them
  └─ process(signal)
         ├─ stages 1..7   unchanged
         ├─ build the report, and ask the downstream gates
         ├─ ledger.contains(signal_id)?        → NO_TRADE  DUPLICATE_SIGNAL
         ├─ execution_enabled && !dry_run?
         │    ├─ no  → DRY_RUN, with every blocker named
         │    └─ kill switch engaged?  → NO_TRADE  KILL_SWITCH_ACTIVE
         └─ executor.submit(request)          → EXECUTE / DRY_RUN / NO_TRADE
```

The envelope has no public constructor, so `wire_execution` cannot be handed an
executor on its own. That replaced a test which read the pipeline's source and
failed on any `TradeExecutor` import — an **absence** that could forbid the mistake
but could not authorise the right thing. Three phases of forbidding is how this
project spent the time it needed for permitting.

**The duplicate check runs before the configuration,** because "this trade already
happened" is true whether or not we were about to place it. Checked second, a
duplicate on a live pipeline would reach the executor before the ledger was asked.

**An executor that raises produces a recorded `UNKNOWN`,** not a traceback: a decision
whose recording failed must still be recorded, and this is the outcome that most needs
one.

### The one thing that can still go wrong

`compute_signal_id` hashes `(symbol, timeframe, bar_index, bar_time, action, direction,
setup_id)` — **the bar is the unit of identity.** But the mapper falls back to
`bar_index=-1` and `bar_time=None` when the engine reports neither, and `Signal`'s own
default is `-1`, so the two agree. A signal with both fallbacks produces a key with
**no bar in it**, and two of them hash alike.

Different symbols and different setups still separate, and either field alone is
enough — it takes both being absent. So it is a serious defect on one symbol and one
timeframe, not a systemic one. **Phase 10's first job is to refuse such a signal**,
which is fail-closed and needs no substitute key.

Two properties exist only at this level, and are what the pipeline tests are for:

- **the order** — cheap and decisive before expensive and outward-reaching. A
  signal with no stop is refused before the account is ever read, so a malformed
  signal costs no round trip to a terminal.
- **the snapshot** — the account and the specification are read **once** each and
  used for both the gate that admits the trade and the arithmetic that sizes it.
  Two reads can straddle a close, and then the open-position count that admitted
  the trade would not be the count that sized it.

---

## Step 1 — the engine reads the market

The upstream engine is a **library, not a service**. There is no process, no port,
no callback. One call does everything:

```python
from albrooks import Analyzer

result = Analyzer().analyze(bars, symbol="EURUSD", timeframe="H1")
```

It is synchronous, and it does not raise for ordinary outcomes. A series it
cannot analyse comes back as a result explaining why.

Two shapes come back, and this is the single most important thing to know about
the boundary:

| Path | When | `decision` keys |
|---|---|---|
| Normal | analysis ran | **13** — `action`, `reason`, `subject`, `direction`, `plan`, `evidence`, `explanation`, `vetoes`, `considered`, `ranking_basis`, `sides`, `is_actionable`, `is_probability` |
| Degenerate | no analysis ran | **3** — `action`, `reason`, `note` |

The degenerate path fires for `NO_BARS`, `NEGATIVE_LAST_CLOSED` and
`ATR_UNAVAILABLE`. The engine is explicit that these are statements about the
*input*, not about the market — which is why the adapter keeps them distinguishable
from a quiet market. A broken data feed that read as "no setup found" would be a
day of not trading with nothing in the logs to say why.

---

## Step 2 — the adapter normalises it

`AlBrooksSignalSource.latest_signal(symbol, timeframe)` returns the bridge's own
`Signal`, or `None`.

### The mapping

| Upstream | Internal | Note |
|---|---|---|
| `decision["action"]` | `SignalAction` | `BUY`/`SELL`/`WAIT`/`NO_TRADE` |
| `decision["direction"]` | `Direction` | **not read**; the action determines it |
| `decision["plan"]["entry"]` | `Signal.entry` | `Decimal`, `None` on an abstention |
| `decision["plan"]["stop"]` + `stop_basis` | `Signal.stop_loss` + `stop_basis` | provenance preserved |
| `decision["plan"]["target"]` + `target_basis` | `Signal.take_profit` + `take_profit_basis` | provenance preserved |
| `decision["subject"]` | `Signal.setup_id` | names the detector |
| `decision["evidence"]["value"]` | `Signal.evidence_score` | **not a probability** |
| `AnalysisResult.last_closed_bar` | `Signal.bar_index` | the analysed bar, not the last row |
| `Bar.time` at that index | `Signal.bar_time` | float epoch seconds |

### Five rules that are not obvious

**A `0.0` price means "undefined", not "zero".** The engine's `TradePlan` uses
`0.0` for an absent level, and its own geometry check *skips* zero levels rather
than comparing them. The adapter preserves that, so a missing stop stays missing
instead of becoming a stop at zero — which would be wrong by the entire size of
the instrument.

**A `WAIT` and a `NO_TRADE` are different, and stay different.** One is a
condition that was evaluated and not met; the other is a refusal to answer. Both
are abstentions, both are non-tradable, and a log that flattened them could not
say which happened.

**The direction comes from the action, not from `decision["direction"]`.** When
the two disagree — an upstream bug — the action wins, because it is the coarser
and more conservative statement. A long taken from a `SELL` because of a stale
direction field is the worst available outcome.

**An abstention has no entry price.** The engine returns `plan: None` on every
abstention, so requiring an entry would have forced the adapter to substitute the
last close for one. `Signal.entry` is therefore optional, and required only for a
tradable action.

**The bar time comes from the analysed index, not the last row.** The engine
computes `bar_features` only for bars `0..last_closed`, so the final row of a full
series is a *later* bar than the one that was analysed.

---

## Signal identity

The key is a SHA-256 over exactly these fields, and nothing else:

```
symbol, timeframe, bar_index, bar_time, action, direction, setup_id
```

It is deterministic: the same reading always produces the same key, on any
machine, forever. That is what makes the downstream deduplication work across a
process restart, and it is why there is no UUID anywhere in this project.

**The prices are deliberately excluded.** The tension is real:

* re-delivery of the same reading must be recognised → include the bar
* a recomputation after new bars close must be a *new* trade → include the bar
* a recomputation over the *same* bar that nudges a stop by one tick is the same
  reading with a rounding difference → exclude the prices

The bar is the unit of identity, so the bar is in the key and the prices are not.
Without that, a stop that flapped by one tick would produce a fresh position every
cycle.

**The trade-off, stated plainly:** if the engine materially revises a stop on the
same closed bar, the bridge treats the revision as the same signal and suppresses
it. Phase 9 revisits this against real engine output. The key format is
`stb-<32 hex chars>`, and the field order is part of the contract — changing it
would make every previously-recorded signal look new and re-enable duplicates
against a live ledger.

---

## Stop provenance

The adapter carries `stop_basis` through without interpreting it. Recording it is
the whole point, because the two kinds of stop are not the same thing:

| `stop_basis` | Structural? | What it is |
|---|---|---|
| `PULLBACK_EXTREME` | yes | beyond the pullback's extreme |
| `BREAKOUT_REFERENCE` | yes | beyond the breakout reference |
| `PATTERN_EXTREME` | yes | beyond a double top/bottom extreme |
| `SWING` | yes | beyond an identified swing |
| `ATR_FALLBACK` | **no** | a volatility multiple |
| `NONE` | **no** | undefined |

The engine's own docstring calls an `ATR_FALLBACK` stop *"arithmetically sound and
structurally empty"*, and its `has_structural_stop` property is exactly
`stop_basis not in ("ATR_FALLBACK", "NONE")`.

Phase 4 decides what to do with that. The default will be to **refuse** a
volatility-fallback stop, with a configuration flag to permit it. That is the
brief's instruction not to invent a stop merely to make the system trade, and it
is now grounded in the real API rather than in an assumption.

---

## What the signal does not carry

Things a reader might expect and will not find, each for a stated reason:

| Not present | Why |
|---|---|
| Position size | Neither upstream project has it. Phase 4. |
| Account balance | Neither upstream project has it. Phase 4 + 7. |
| Symbol specification | `albrooks` treats a symbol as an opaque string; `auto-trade`'s `SymbolInfo` is dead code with three fields. Phase 4 + 7. |
| A confidence *probability* | The engine says in three places that its evidence score is not one. Carried verbatim, labelled `evidence_is_probability: false`. |
| An entry price on an abstention | The engine returns `plan: None`. Substituting the last close would be exactly the substitution this project refuses. |
| A callback or an event | The engine is a library. There is nothing to subscribe to. |

---

## Observability

Every step emits a structured event. What has been implemented:

| Event | When | Level |
|---|---|---|
| `SIGNAL_RECEIVED` | a signal was read and mapped | INFO |
| `SIGNAL_REJECTED` | a signal was read and refused, or no analysis ran | WARNING |
| `SIGNAL_SOURCE_UNAVAILABLE` | bars unreachable, or the engine failed | ERROR |

An abstention is logged as `SIGNAL_RECEIVED`, not as `SIGNAL_REJECTED`. The engine
made a considered decision, and logging it as a rejection would make a normal
outcome look like a fault in this bridge. A degenerate result *is* a rejection,
because no analysis ran at all.

Every logged signal carries its `signal_id`, and that id is the same one the
returned signal has. A decision log naming a different signal than the one traded
would be worse than no log at all.

The remaining events — `STOP_RESOLVED`, `RISK_CALCULATED`, `POSITION_SIZED`,
`TRADE_VALIDATED`, `TRADE_REJECTED`, `DRY_RUN_COMPLETED`, `EXECUTION_RESULT`,
`DUPLICATE_SUPPRESSED` — are defined and reserved, and the phase that introduces
each one emits it.

---

## Testing this layer

**245 tests, 245 passing, in about 1.3 seconds.** The whole suite runs **without
`albrooks` installed**, which is the property that makes it a safety net rather
than a souvenir.

| Layer | How it is tested |
|---|---|
| `identity.py` | Pure functions, no stubs needed. 16 tests. |
| `mapper.py` | Hand-written stubs shaped like the engine's real output. 72 tests. |
| `source.py` | A stub analyzer and a stub data provider. 23 tests. |
| Both, against the real engine | 9 integration tests, skipped when `albrooks` is absent. |

The stubs live in `tests/stubs.py` and are built from the audit's verbatim quotes
rather than by importing the engine. A stub can only ever prove it is
self-consistent, so the integration tests exist to catch the stubs drifting — and
they run on any machine that has the checkout.

```powershell
# Everywhere
python -m pytest

# Only the real-engine tests
python -m pytest tests/integration -v
```

**A skip is not a pass.** The upstream project makes the same point about its own
live-terminal tests, and it applies here: when the integration tests are green,
they are green.
