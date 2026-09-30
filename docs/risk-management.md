# Risk Management

How a reading becomes a sized trade, and what happens to it next.

**The pipeline is assembled.** Steps 1–8 are the policies over values in hand;
step 9 is the decision. Since Phase 6 a single function —
`application.process_signal.ProcessSignal.process` — runs them in order, stops at
the first refusal, and returns either a `NO_TRADE` carrying a reason code or a
`DRY_RUN` carrying a fully sized `TradeIntent`.

What remains unimplemented is everything *after* the decision: idempotency
(Phase 9), dry-run reporting (Phase 8), execution (Phase 7) and the real MT5
data adapter (Phase 7). In tests the account and symbol facts come from
`adapters/fake/`, which satisfies the same ports the terminal adapter will.

The bridge can now take a signal all the way to a sized, unsent decision. It
cannot yet send one, because no executor is wired into the pipeline and the
default configuration cannot execute.

---

## The pipeline

```
Signal (from the adapter)
    ↓
[1] validate_signal        is this a signal we will act on at all?
    ↓
[2] resolve_stop           is there a defensible stop, on the correct side?
    ↓
[3] resolve_take_profit    where does the trade aim, and did we choose that?
    ↓
[4] validate_geometry      do entry, stop and target agree with each other?
    ↓
[5] validate_policy        may *this bridge* act, under its own rules?
    ↓
[6] validate_against_spec  can the symbol express these prices at all?
══════════ Phase 4: everything below needs the account and the symbol ══════════
[7] resolve_risk_budget    balance × configured percentage
    ↓
[8] resolve_position_size  ticks × tick value, rounded to the broker's step
═════════════════════════════ Phase 6: the decision ════════════════════════════
[9] TradeDecision          NO_TRADE with a reason, or DRY_RUN with an intent
═════════════════════════════ Phase 9 → 8 → 7 ═════════════════════════════════
    idempotency → dry run → execution
```

Every step returns a `Resolution`: either a value or a reason code. **All eight
are implemented** — steps 1–6 are pure functions of their arguments, steps 7 and 8
need facts that exist only in a terminal, and `application/process_signal.py` is
what obtains them and puts the steps in order.

### The order is the design

Cheap and decisive before expensive and arithmetic. Signal validity costs nothing
and rejects the commonest outcome in the system. The stop comes next because
without one there is nothing to size against. Geometry is checked before policy
because a malformed trade is not a policy question. The account and the
specification come last, because they are the only steps that reach outside the
process — so a signal that fails step 1 costs no round trip to a terminal. There
are tests for that, not just for the refusals themselves.

**It stops at the first refusal.** Running a later check on a signal that has
already failed produces arithmetic on meaningless inputs. `diagnostics["stage"]`
names the stage that refused, because "no trade" without "which check" is an
answer nobody can act on.

**The geometry stage always passes** when reached, and that is worth knowing: every
condition it checks has already been checked by an earlier stage on the same
objects. It stays because the two functions have different callers and a check
that trusts its input to have been verified elsewhere fails the first time
somebody calls it directly.

### The account is one snapshot per decision

The account is read **once** and the same read feeds both the concurrency gate at
step 5 and the budget at step 7. Reading twice would be cheap and still wrong:
two reads can straddle a close, and the open-position count that admitted the
trade would not be the count that sized it.

**A concurrency limit that cannot be checked is refused, not skipped.** If
`max_open_positions` is set and the terminal does not answer, the trade is refused
— a limit that quietly stops applying when the account is unreachable is a risk
control that switches off exactly when it is most needed.

### Every failure is a decision

A malformed signal, an abstention, a stop on the wrong side, a terminal that is
not running — all of them come back as a `TradeDecision` with a reason code, never
as an exception. The commonest outcome in the system is "the engine found nothing
to trade", and a loop that died on it would stop processing the signals it could
still act on.

### It cannot place an order

A trade that passes every check comes back as `DRY_RUN`, not `EXECUTE`. There is
no executor wired into the pipeline, so `EXECUTE` would be a lie — and calling it
a dry run says exactly what happened: every check passed, nothing was sent, and
nothing could have been. A test asserts the module imports no `TradeExecutor`,
`IdempotencyStore` or `KillSwitch`, because an execution path that appears before
Phase 7 would be one without the kill switch, the ledger and the audit log around
it.

---

## Step 2 — the stop

**There is no code path that invents a stop.** That is the rule, and it is
enforced rather than merely documented: a test walks `stops.py`'s own AST and
asserts that no numeric literal in the module could serve as a price, and that
every `StopLoss` is constructed from the signal's own price.

The checks, in order — and the order is the design:

| # | Check | Refusal code |
|---|---|---|
| 1 | Is there a stop at all? | `NO_VALID_STOP` |
| 2 | Is the price a tradable level? | `NO_VALID_STOP` |
| 3 | Is it at the entry? | `INVALID_STOP_DISTANCE` |
| 4 | Is it on the correct side? | `STOP_ON_WRONG_SIDE` |
| 5 | Is the distance usable? | `INVALID_STOP_DISTANCE` |
| 6 | Is it structural? | `STRUCTURAL_STOP_REQUIRED` |

### Why 3 comes before 4

A stop exactly at entry is a *distance* problem, not a *side* problem. Reported as
"wrong side" it would point an operator at a sign error that is not there — the
stop is not on the wrong side, it is on no side at all. The same ordering applies
to the take profit in step 3.

### Why 2 is separate from 1

The upstream engine reports an absent level as `0.0`, and the adapter preserves
that as absent. A signal carrying `0.0` and a signal carrying nothing are
reporting **the same condition**, so they get **the same code** — otherwise an
alert on "no stop" would fire twice for one fault.

### The structural question

This is where the upstream engine's provenance matters. Its `stop_basis` says
where the price came from:

| Basis | Structural? | What it is |
|---|---|---|
| `PULLBACK_EXTREME` | yes | beyond the pullback's extreme |
| `BREAKOUT_REFERENCE` | yes | beyond the breakout reference |
| `PATTERN_EXTREME` | yes | beyond a double top/bottom extreme |
| `SWING` | yes | beyond an identified swing |
| `ATR_FALLBACK` | **no** | a volatility multiple |
| `NONE` | **no** | undefined |
| anything else | **no** | unrecognised |

The engine's own docstring calls an `ATR_FALLBACK` stop *"arithmetically sound
and structurally empty"*. That is the distinction being enforced. A volatility
multiple is a reasonable number; it is not a level the market produced.

**Refused by default.** `BRIDGE_ALLOW_VOLATILITY_FALLBACK_STOP=true` permits it,
and when permitted the resolution records `StopSource.SIGNAL_VOLATILITY_FALLBACK`
so a log six months later says the stop was not structural.

**An unrecognised basis is refused even when that flag is set.** The flag names
one specific alternative to structural geometry; it is not a general licence. The
refusal message says so, because an operator who hits an unknown basis must not
be left thinking they tripped the documented `ATR_FALLBACK` case — the two have
different remedies.

---

## Step 3 — the take profit

The configured default is 1:1, so reward distance equals risk distance:

```
BUY   entry 100  stop 99   →  target 101
SELL  entry 100  stop 101  →  target 99
```

But the engine's own target must not be silently overridden, and both are true at
once. The resolution is a **policy**, chosen per deployment:

| `BRIDGE_TAKE_PROFIT_SOURCE` | Behaviour |
|---|---|
| `RR_FALLBACK` *(default)* | Use the signal's target when it is structurally defensible, above the configured minimum ratio, and on the correct side. Otherwise apply the ratio, and record that it was a fallback. |
| `SIGNAL` | Use the signal's target. Refuse the trade if it is unusable on any of those grounds. |
| `RR_DERIVED` | Ignore signal targets entirely. Always the ratio. |
| `NONE` | No take profit. Only valid if exits are managed elsewhere. |

The first and the last encode genuinely different intentions:

* `RR_FALLBACK` — "the engine's target is usually better than mine, but not
  always, and I want 1:1 as the floor".
* `RR_DERIVED` — "I do not trust a target from a system that documents its own
  targets as unvalidated, and I want exactly 1:1".

A trader who has read the engine's own caveats may well want the second. It is not
the same preference as the default, which is why it is a separate setting.

### The "floor" was not real until Phase 5

The paragraph above describes `RR_FALLBACK` as wanting *"1:1 as the floor"*, and
until Phase 5 nothing compared the signal's implied ratio against anything. The
configured ratio decided only the **fallback** distance; a structurally sound
target at 0.2:1 was accepted silently under the default policy. The resolver's
own docstring said so more precisely — *"use the target when structurally
defensible"* — and nothing compared ratios. Two descriptions, one implementation.

Phase 5 added `BRIDGE_MINIMUM_REWARD_RISK_RATIO`:

| Setting | Effect |
|---|---|
| unset *(default)* | Any structurally sound target is taken as the engine measured it. **Unchanged behaviour.** |
| set, e.g. `1.0` | A target implying less than 1:1 is treated as unusable, so the configured ratio is applied and the substitution recorded as a fallback. |

**Off by default on purpose.** Switching it on starts replacing engine-measured
targets with the configured distance — a trading decision, not a bridge upgrade,
and one nobody should have made for them by editing a config file's meaning. The
maintainer chose the opt-in form explicitly over both alternatives: making the
configured ratio an implicit floor (silently changes the default policy), and
refusing sub-floor targets instead of falling back (turns `RR_FALLBACK` into a
filter).

The floor applies where the signal's target is a **candidate** — `RR_FALLBACK`
and `SIGNAL`. Under `RR_DERIVED` there is nothing to filter, because ignoring
signal targets is the entire point of that policy; a test pins this so the
interaction is deliberate.

### A fallback is never silent

Under `RR_FALLBACK`, when the signal's target is unusable, the resolution records
`fallback_because` with the specific reason — including the ratio that was found
and the floor it failed. Every resolved target carries a `TakeProfitSource`, so a
log can never make a 1:1 target look like the engine's own measured move.
**That distinction is the difference between an auditable decision and a
plausible-looking number.**

The signal's target is treated as unusable when it is absent, on the wrong side
for the direction, carries a non-structural basis, or implies a ratio below the
configured floor. They are checked in that order, so a target that is wrong on
two grounds reports the one with the upstream remedy: a volatility-multiple basis
is the engine's own defect, a poor ratio is this bridge's opinion about it.

### The achieved ratio is reported, not the configured one

`RR_FALLBACK` with a usable signal target produces the *signal's* ratio, not the
configured one. The resolution reports `achieved_ratio` — what the trade actually
has. A decision log reporting the configured ratio would be reporting an
intention as a fact.

**Every successful resolution reports it**, including the ratio the fallback
produced, the ratio `RR_DERIVED` derived, and the ratio the strict `SIGNAL`
policy accepted. Under `NONE` the key is present and **`None`**, because there is
no target and therefore no ratio — which is a successful, deliberate outcome, not
a refusal. Phase 5 fixed that: the key used to be *absent* on that path, so the
first consumer to index it would have raised `KeyError` on a resolution that had
already succeeded.

**One ratio, one representation.** `Decimal` carries its own exponent, so the
same ratio could be written as `"1"` or `"1.0"` depending on which arithmetic
produced it — and a log query for one missed the other. `domain.canonical_ratio`
normalises it, with a guard so a 100:1 configuration does not become `1E+2`. It
changes only the representation; the numeric value is untouched, so nothing doing
arithmetic on it is affected.

### The engine's own ratio is not used

The upstream engine computes a `reward_to_risk` and *ranks candidates on it*, and
the adapter discards it. That is deliberate, and recorded in
`docs/architecture.md §5.5` and `docs/integration.md §1.6`: the engine documents
its own targets as unvalidated, and the bridge computes the same ratio from
distances it has already validated. The cost is one line of diagnostic detail.

---

## Step 1 — the evidence filter

`BRIDGE_MINIMUM_EVIDENCE_SCORE` filters on the upstream engine's evidence score.

**A missing score is refused, not allowed through.** If an absent value counted as
a pass, the filter would be trivially bypassed by any signal source that simply
omitted the field — which is precisely the wrong failure direction for a risk
control.

**The name matters.** The brief suggested `minimum_signal_confidence`. That name
was rejected: the upstream engine states in three separate places that its
evidence score is *not* a probability of continuation, and no threshold on it has
been validated against outcomes. A configuration key called `confidence` invites
exactly the misreading the number cannot support. The refusal message repeats the
caveat at the point where someone is about to set a threshold.

---

## Step 4 — geometry, checked twice

`validate_geometry` re-checks the stop side even though `resolve_stop` already
did. That is deliberate duplication:

- the two functions are reached by different callers
- the geometry can come from configuration as well as from a signal
- a geometry check that trusts its input to have been checked elsewhere is a
  geometry check that fails the first time somebody calls it directly

**A redundant check costs a comparison. A missing one costs a rejected broker
order.**

---

## Step 6 — what the symbol can express

A fourth check the execution layer does not make. A stop inside the tick size, or
a target rounded to fewer decimals than the symbol quotes, is a different price
from the one that was computed — and the broker will usually round it silently,
which means the executed risk is not the risk that was calculated.

Off-tick levels are **recorded rather than refused**. A broker will usually accept
and round them, and refusing outright would reject trades the broker would take.
The point is that the log says so.

The spread check is **not** here: it needs a live quote rather than a
specification, so it belongs with the market-data provider in Phase 7.

---

## Step 7 — the risk amount

```
risk_amount = balance × risk_percent / 100
```

Balance, never equity. Equity moves with open positions, so sizing on it would
make the risk of a new trade depend on trades already running.

The result is a `RiskBudget`, not a bare number. A budget on its own is an
assertion; "0.5% of a $10,000 balance, targeting $50" can be recomputed by hand,
and that recomputation is the only thing standing between a mistyped percentage
and a system trading at a risk level nobody chose.

### There is no code path that invents an account balance

The same rule `stops.py` applies to a stop, one level up. If the balance is
unavailable, the answer is a refusal — not a remembered figure, not a configured
default, not the last value that was seen. A position sized against an invented
balance is arithmetically correct and financially meaningless, and it is
arithmetically correct in a way that is very hard to notice afterwards.

Enforced by an AST test that walks `risk.py` and asserts three things: no numeric
literal in the module could serve as a balance, the module never constructs an
`AccountBalance` (it *reads* the one it is given), and the budget's amount comes
from `RiskParameters.risk_amount` rather than from a second implementation of the
percentage calculation that could later disagree with the first.

### Currency coherence is checked before any division

MT5 quotes `tick_value_profit` in the **account** currency for one lot. A
position size divides the budget by `ticks × tick_value`, and that division is
only meaningful when both sides are the same money. Dividing dollars by euros
produces a number that looks exactly like a volume and is not one.

So `check_currency_compatibility` runs before the sizing, and refuses when:

| Case | Reason code | Why |
|---|---|---|
| `currency_profit` is not stated | `SYMBOL_SPEC_UNAVAILABLE` | A check that passes on missing data is not a check. The MT5 adapter populates this field, so an empty one means the adapter did not run. |
| `currency_profit` ≠ account currency | `INVALID_RISK_PARAMETERS` | The division would be across two currencies. |

**The margin currency is recorded but not refused.** EURUSD margin is quoted in
EUR on a USD account. That is normal, and refusing it would refuse every forex
pair on a dollar account.

---

## Step 8 — the position size

```
ticks         = |entry − stop| / tick_size
risk_per_unit = ticks × conservative_tick_value
raw_volume    = risk_amount / risk_per_unit
volume        = round_down_to_step(raw_volume)  then clamp to [min, max]
```

### Why tick-value based rather than a pip formula

Because it needs no special cases. A 5-digit EURUSD and a 2-decimal XAUUSD both
reach **$300 per lot** over their natural stop, by completely different
arithmetic:

```
EURUSD   0.00300 / 0.00001 × $1 = 300 ticks × $1 = $300 a lot
XAUUSD   3.00    / 0.01    × $1 = 300 ticks × $1 = $300 a lot
```

A sizer that assumed a 5-digit pair and a 100000 contract size would be wrong on
gold by two orders of magnitude, and wrong in the direction of **oversizing**.
Both cases are in the test suite deliberately, because the pair of them is what
distinguishes a tick-value sizer from a forex sizer that happens to work.

Note what is *absent* from the formula: `contract_size`. It is already folded
into the tick value, which is quoted per lot. A sizer that multiplied by it as
well would double-count on every instrument, and by 100000 on a forex pair. There
is a test for exactly that.

### The conservative tick value is the larger of the two

This is the one place Phase 4 changed a Phase 1 decision, and the correction is
worth reading twice because the original was not a typo — it was the right
conclusion reached by inverting the arithmetic.

`volume = risk_amount / (ticks × tick_value)`, so **volume is inversely
proportional to the tick value**. The *smaller* tick value therefore produces the
*larger* position. Taking the minimum of `tick_value_profit` and
`tick_value_loss` is the **least conservative choice available**.

`tick_value_profit` and `tick_value_loss` differ on a hedging account and on some
CFDs. On a symbol reporting $1 a tick in profit and $2 in loss, a $50 budget over
a 300-tick stop came out at **0.16 lots** — and if that stop were hit, the loss
would be `300 × $2 × 0.16 = $96`, twice the budget. The arithmetic downstream was
correct throughout; the divisor was wrong.

`conservative_tick_value` is now `max(...)`, which bounds the loss from above in
both directions: a long whose stop is hit on a falling price and a short whose
stop is hit on a rising one are both covered. It can under-size a position whose
real tick value happens to be the smaller of the two, and that is the correct
direction to err — an under-spent budget is a disappointment, an over-spent one is
a loss.

The exact per-direction value is knowable (`tick_value_loss` for a long,
`tick_value_profit` for a short) and would size both sides optimally. It is not
used: it would make the size depend on the direction, which means two more code
paths, two more combinations to test, and a way for a long to be sized with a
short's number. One bound that is safe for both is worth more here than the
tightness it gives up.

A side effect of `max`: a symbol reporting `0` for one of the two values no longer
produces a zero conservative value, so the ordinary case needs no special
handling. Only a symbol with **both** sides at zero is refused, and that is
correct — it is an unfinished symbol, not a cheap one.

### A volume below the broker minimum is a refusal, never a floor-up

```
budget $0.20, stop 300 ticks  →  raw 0.00066  →  rounds down to 0.00  →  minimum is 0.01
```

Refused, as `VOLUME_BELOW_BROKER_MINIMUM`. Flooring up to `volume_min` would
place a position risking `$3` against a `$0.20` budget — fifteen times the money
anybody chose to risk, decided by the broker rather than by the trader. It is the
single most dangerous line in any position sizer, and it is guarded three times
over:

1. `SymbolSpec.clamp_volume` clamps a sub-minimum volume **down to zero**, so the
   caller cannot pass one through by accident.
2. `PositionSize.__post_init__` **raises** if `clamped_to_minimum` is ever set, so
   no code path can construct a size that records a floor-up.
3. `resolve_position_size` refuses rather than clamping.

The refusal names three real remedies — raise `BRIDGE_RISK_PERCENT`, accept that
the instrument is too coarse for this account, or wait for a tighter stop — and
keeps the partial arithmetic (`raw_volume`, `rounded_volume`, `ticks`,
`risk_amount`) so it can be debugged rather than merely observed.

### Rounding is down, always

Rounding up can exceed the budget; rounding down leaves it fractionally under.
The sizer's last act is to check that, and to **discard the size** if it fails:

```python
if not position.is_within_budget:
    return refused(SIZING_FAILED, ...)
```

Unreachable through the normal path — rounding down and clamping down both move
the volume away from the budget — and kept for the day somebody changes the
rounding direction, which is a one-character edit that would break the central
invariant silently. There is a test that drives the branch by making the round-up
happen for real.

### Clamping down to `volume_max` is allowed and is recorded

Unlike a sub-minimum volume, clamping to the maximum is **safe**: it reduces the
risk. The only requirement is that the log says so, because a position at the
maximum is not a position at the intended size. `PositionSize.clamped_to_maximum`
and `planned_loss` both exist for that — `planned_loss` reports the money actually
at risk, which after rounding and clamping is almost never the intended amount.

### `PositionSize` keeps every input

A volume alone is not auditable. "0.16 lots" cannot be checked against anything,
while the balance, percentage, stop distance, ticks, tick size and tick value that
produced it can be recomputed by hand.

---

## Who obtains the account and symbol facts

`domain` cannot. `test_domain_isolation` walks the package's AST and fails on any
edge to `ports`, so `resolve_risk_budget` takes an `AccountBalance` as an argument
rather than asking an `AccountProvider` for one.

`application/risk_service.py` closes that gap, and that is the only reason it
exists:

```python
size = RiskService(account_provider, symbol_spec_provider).position_size(symbol, stop, risk)
```

It converts a provider that raises into a refusal, checks the currencies, and
emits `RISK_CALCULATED` and `POSITION_SIZED` — **including on refusals**. A size
refused because the budget is too small for this instrument is one of the most
useful lines in the whole log: it says the configuration and the market do not fit
together, and it says it with the numbers. An event emitted only on success would
leave an operator with a silence indistinguishable from a signal that never
arrived.

A terminal that is not running is an **expected condition**, so no exception
escapes: the loop records a refusal and processes the next signal.

### The fakes

`adapters/fake/` holds `FakeAccountProvider` and `FakeSymbolSpecProvider`. They
are in the package rather than in `tests/` because **a fake implements the same
port as the real thing** — so a test written against them exercises the path the
terminal will drive, and they are the specification Phase 7's MT5 adapter will be
written against.

Both obey the two rules that make the real adapter's hardest cases testable:

- **An unreachable source raises.** It does not return a sentinel, a default or a
  remembered last-known-good value. "The account has no money" and "we could not
  ask" are different answers, and collapsing them is how a system ends up sizing
  trades against a stale balance.
- **An unknown symbol raises.** It does not return a default specification. A
  default would be an invented contract, which is precisely what the Phase 0 audit
  established that neither upstream project has.

The specification builders (`eurusd_spec`, `gold_spec`, `unusual_spec`) exist by
name so the gold case is a deliberate test rather than an accident. `unusual_spec`
is deliberately asymmetric — $0.50 a tick in profit against $2.00 in loss — so the
conservative-value correction is exercised through the whole service.

---

## The `Resolution` type

Every step returns one:

```python
resolution = resolve_stop(signal, risk)
if not resolution.ok:
    return no_trade(resolution.reason, resolution.explanation)
stop = resolution.unwrap()
```

**Failures are values, not exceptions.** A missing stop is the commonest outcome
in the system, not an exceptional condition, and an exception would force every
caller into a `try` block to handle the common case.

**Success is an explicit flag, not `value is not None`.** A take-profit step can
legitimately succeed with the answer "there is no take profit" — that is
`BRIDGE_TAKE_PROFIT_SOURCE=NONE` working as configured, and inferring success
from the value made it indistinguishable from a refusal. A log full of refusals
for a deliberate configuration trains people to ignore the reason codes, which
defeats their purpose.

`Resolution.details` is read-only. A `Resolution` is a decision record, and a
record that could be edited after the fact would not be one.

---

## What is still missing, and where

| Step | Needs | Status |
|---|---|---|
| Risk amount | `AccountBalance` | **Done.** `domain/risk.py` |
| Position size | `SymbolSpec` | **Done.** `domain/sizing.py` |
| Broker constraints | `SymbolSpec` | **Done.** `check_broker_constraints` |
| Wiring signal → stop → size → decision | — | **Done.** `ProcessSignal` |
| A real account/symbol provider in tests | — | **Done.** `adapters/fake/` |
| The account and spec in production | a terminal | Phase 7. `adapters/mt5/` |
| Idempotency / duplicate protection | the ledger | Phase 9 |
| Dry-run reporting | an executor-shaped record | Phase 8 |
| Execution | `auto-trade`'s `ExecutionWorkflow` | Phase 7 |
| Margin check | live account state | Not scheduled. Recorded as a known gap: a position can pass every check here and still be refused for margin at the broker. |

---

## Invariants

1. **No code path invents a stop, a balance or a tick value.** Three AST tests, one
   per module that could plausibly grow such a path. A fourth asserts the
   reward:risk ratio is computed in exactly one place.
2. **A missing stop and a zero stop are one fault with one code**, because the
   engine uses `0.0` to mean "no stop".
3. **A level at the entry is a distance problem, not a side problem**, for both
   the stop and the target.
4. **An unrecognised basis is non-structural**, even when volatility fallbacks are
   permitted.
5. **A take-profit fallback is never silent** — it records which policy produced
   the number and why the signal's own target was not used.
6. **A missing evidence score fails the filter** rather than passing it.
7. **The achieved reward:risk is reported**, not the configured one.
8. **A refusal always carries a reason code** and keeps the arithmetic it did on
   the way.
9. **`Resolution.details` is read-only**, so a decision record cannot be rewritten
   after it was made.
10. **A volume below `volume_min` is a refusal, never a floor-up** — guarded three
    times over, one of which raises at construction.
11. **Rounding to `volume_step` is down**, and a size that would exceed its budget
    is discarded rather than returned.
12. **The conservative tick value is the larger of the two**, because volume is
    inversely proportional to it and the under-sized position is the safe failure.
13. **A size cannot be produced without both an account balance and a symbol
    specification**, and neither is ever defaulted.
14. **The configured reward:risk ratio is not a floor on a signal's target**, and
    a floor that *is* configured is opt-in, never silent, and applies only where
    the signal's target is a candidate.
15. **Every successful take-profit resolution reports `achieved_ratio`**, and it
    is `None` only when there is no target at all. The key is never absent.
16. **A configured zero is refused, never defaulted.** `Decimal("0")` is falsy, so
    the `or default` idiom would have brought the bridge up at 0.5% and 1:1 after
    the operator asked for neither.
17. **The pipeline stops at the first refusal**, and the stages after it never
    run — so a signal with no stop costs no round trip to a terminal.
18. **Every outcome is a `TradeDecision`, never an exception.** An abstention, a
    malformed signal and an unreachable terminal are all ordinary outcomes.
19. **A concurrency limit that cannot be evaluated is refused, not skipped.** A
    limit that stops applying when the terminal is unreachable switches off
    exactly when it is most needed.
20. **The account and the specification are one snapshot per decision**, so the
    gate that admitted a trade and the arithmetic that sized it saw the same
    numbers.
21. **A passing trade is `DRY_RUN`, never `EXECUTE`,** and the pipeline module
    imports no `TradeExecutor`. Execution belongs to Phase 7, where the kill
    switch, the idempotency ledger and the audit log come with it.
