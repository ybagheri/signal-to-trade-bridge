> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

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

