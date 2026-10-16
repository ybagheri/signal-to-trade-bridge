> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

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

