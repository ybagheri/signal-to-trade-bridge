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
[2] Signal adapter                   → internal Signal      ← IMPLEMENTED, Phase 2
    ↓
[3] Signal validation                                           IMPLEMENTED, Phase 3
    ↓
[4] Stop resolution                                            IMPLEMENTED, Phase 3
    ↓
[5] Take-profit resolution                                       IMPLEMENTED, Phase 3, 5
    ↓
[6] Account + symbol data                             IMPLEMENTED via fakes, Phase 4
    ↓                                                              real: Phase 7
[7] Risk amount + position size                                     IMPLEMENTED, Phase 4
    ↓
[8] Broker constraints                                          IMPLEMENTED, Phase 4
    ↓
[9] Idempotency                                                 Phase 9
    ↓
[10] Dry run?  record, stop                                      Phase 8
    ↓
[11] Execution                                          Phase 7
    ↓
MetaTrader 5 → broker
```

**Every stage from 2 to 8 is implemented and tested. Nothing connects them.**
There is still no `ProcessSignal`, so a signal arriving today goes nowhere — the
functions exist, are individually correct, and have never been called in sequence.

That gap is Phase 6, and it is the first thing in this document that is *not* a
Phase 0 finding: everything above it was discovered by auditing the two upstream
repositories, and this was discovered by noticing that no line of `src/` mentions
`resolve_stop` and `resolve_take_profit` together.

Note also that step 6 is the only one whose *production* source is missing. In
tests the account and symbol facts come from `adapters/fake/`, which is real code
satisfying the real ports — not a stub that returns a tuple.

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
