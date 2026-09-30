# Risk Management

How a reading becomes a sized trade, and where it stops.

Phase 3 covers the policies that work on values already in hand: the stop, the
take profit, and the geometry checks. **Position sizing is Phase 4** and needs
the account balance and the symbol specification, which come from the terminal.

This document describes the implemented policies and names the Phase 4 boundary
explicitly, because the two are easy to confuse and the confusion would lead
someone to write a sizer that invents its own account data.

---

## The pipeline so far

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
[5] validate_policy        may *this bridge* act on it, under its own rules?
    ↓
[6] validate_against_spec  can the symbol express these prices at all?
    ↓
──────── Phase 4 begins here: everything below needs the account and the symbol ────────
    ↓
[7] risk amount            balance × configured percentage
    ↓
[8] position size          ticks × tick value, rounded to the broker's step
```

Every step returns a `Resolution`: either a value or a reason code. Steps 1–6
are implemented. Step 7 is not.

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
| `RR_FALLBACK` *(default)* | Use the signal's target when structurally defensible and on the correct side. Otherwise apply the ratio, and record that it was a fallback. |
| `SIGNAL` | Use the signal's target. Refuse the trade if it is unusable. |
| `RR_DERIVED` | Ignore signal targets entirely. Always the ratio. |
| `NONE` | No take profit. Only valid if exits are managed elsewhere. |

The first and the last encode genuinely different intentions:

* `RR_FALLBACK` — "the engine's target is usually better than mine, but not
  always, and I want 1:1 as the floor".
* `RR_DERIVED` — "I do not trust a target from a system that documents its own
  targets as unvalidated, and I want exactly 1:1".

A trader who has read the engine's own caveats may well want the second. It is not
the same preference as the default, which is why it is a separate setting.

### A fallback is never silent

Under `RR_FALLBACK`, when the signal's target is unusable, the resolution records
`fallback_because` with the specific reason. Every resolved target carries a
`TakeProfitSource`, so a log can never make a 1:1 target look like the engine's
own measured move. **That distinction is the difference between an auditable
decision and a plausible-looking number.**

The signal's target is treated as unusable when it is absent, on the wrong side
for the direction, or carries a non-structural basis.

### The achieved ratio is reported, not the configured one

`RR_FALLBACK` with a usable signal target produces the *signal's* ratio, not the
configured one. The resolution reports `achieved_ratio` — what the trade actually
has. A decision log reporting the configured ratio would be reporting an
intention as a fact.

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
caller into a `try` block to handle the normal case.

**Success is an explicit flag, not `value is not None`.** A take-profit step can
legitimately succeed with the answer "there is no take profit" — that is
`BRIDGE_TAKE_PROFIT_SOURCE=NONE` working as configured, and inferring success
from the value made it indistinguishable from a refusal. A log full of refusals
for a deliberate configuration trains people to ignore the reason codes, which
defeats their purpose.

`Resolution.details` is read-only. A `Resolution` is a decision record, and a
record that could be edited after the fact would not be one.

---

## What Phase 4 owns, and why it is separate

Not implemented yet:

| Step | Needs | Why it is not here |
|---|---|---|
| Risk amount | `AccountBalance` | Neither upstream project knows the balance. `albrooks` has no account layer; `auto-trade` reads nothing numeric from the terminal. |
| Position size | `SymbolSpec` | `albrooks` treats a symbol as an opaque string. `auto-trade`'s `SymbolInfo` is dead code with three fields. |
| Broker constraints | `SymbolSpec` | Volume step, min, max. |
| Margin check | both | Needs live account state. |

Writing a sizer now would mean inventing account data, and inventing account facts
is the one thing this project refuses to do. The boundary is not conservatism — it
is that there is nothing to compute from until the MT5 adapter of Phase 7 exists.

---

## Invariants established in this phase

1. **No code path invents a stop.** Enforced by an AST test, not by convention.
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
