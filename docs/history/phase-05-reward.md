> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

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

