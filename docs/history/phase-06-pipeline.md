> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## What Phase 6 Built

```
application/
  process_signal.py  ProcessSignal.process(signal) -> TradeDecision
                     STAGES, PASSED, budget_to_account
  risk_service.py     budget_for() / size_for()   the resolve halves, added here
tests/unit/
  test_process_signal.py   50 tests: the order, the snapshot, the refusals
```

**This is the phase five others were deferring.** Every stage existed and was
tested; nothing in `src/` had ever called two of them in sequence. A signal
arriving today went nowhere, not because a piece was missing but because no
function connected the pieces.

### What it does

```
signal → validate_signal → resolve_stop → resolve_take_profit → validate_geometry
       → validate_policy → validate_against_spec → budget → position size
       → TradeIntent → TradeDecision
```

The order is the design, and it is specified rather than incidental: cheap and
decisive before expensive and arithmetic. Signal validity costs nothing and
rejects the commonest outcome in the system; the stop comes next because without
one there is nothing to size against; geometry precedes policy because a malformed
trade is not a policy question; and the account and specification come **last**,
because they are the only stages that reach outside the process — so a signal
with no stop costs no round trip to a terminal. There are tests for that
specifically, not only for the refusals.

### Design decisions Phase 6 made

- **It stops at the first refusal.** Running a later check on a signal that has
  already failed produces arithmetic on meaningless inputs — a position size
  computed from a stop that does not exist is arithmetically fine and completely
  wrong, which is the failure mode this project keeps running into. `diagnostics`
  names the stage that refused, because "no trade" without "which check" is an
  answer nobody can act on.
- **Every outcome is a `TradeDecision`, never an exception.** An abstention, a
  malformed signal and an unreachable terminal are ordinary outcomes, and the
  commonest one is "the engine found nothing to trade". A loop that died on any
  of them would stop processing the signals it could still act on.
- **A passing trade is `DRY_RUN`, never `EXECUTE`,** and that is not a
  placeholder. There is no executor wired in, so `EXECUTE` would be a lie; and
  with the defaults the bridge cannot execute anyway. A trade that passed every
  check and sent nothing must never be able to read as a fill, or a log becomes
  evidence of an order that does not exist. **A test asserts the module imports no
  `TradeExecutor`, `IdempotencyStore` or `KillSwitch`** — an execution path that
  appeared before Phase 7 would be one without the kill switch, the idempotency
  ledger and the audit log around it, which is the failure the architecture exists
  to prevent. The constraint is structural, not left to review.
- **The account and the specification are read once each.** The same account read
  feeds the concurrency gate at stage 5 and the budget at stage 7, because two
  reads can straddle a close — and then the open-position count that admitted the
  trade would not be the count that sized it. This is why `RiskService` grew a
  resolve half (`budget_for`, `size_for`) alongside its read-and-resolve
  convenience methods: the pipeline reads, the service resolves. The old
  single-call methods are kept and are now three-line wrappers.
- **A concurrency limit that cannot be checked is refused, not skipped.** If
  `max_open_positions` is set and the terminal does not answer, the trade is
  refused. A limit that quietly stops applying when the account is unreachable is
  a risk control that switches off exactly when it is most needed — and with no
  limit configured the unreadable account is *not* fatal at that stage, because
  there is no gate to evaluate; it refuses later at sizing, which is a different
  moment and a different remedy.
- **It uses geometry's validated pair from stage 4 onwards,** not the two values it
  passed in. They are the same objects today; the difference is the point. The
  stage exists to say "these two agree", and continuing to use the un-validated
  copies would make it a function whose answer is ignored, which is how a check
  stops being one.
- **The clock is injected,** so a test can pin "now" and get an identical decision
  twice. Phase 9's idempotency ledger depends on that determinism and cannot
  provide it.
- **`STOP_RESOLVED` and `TAKE_PROFIT_RESOLVED` are finally emitted** — two events
  reserved in Phase 1 and still un-emitted after Phases 3 and 4, because Phase 4
  established that the domain has no logger and must not acquire one. This is the
  first layer that may hold one. `TAKE_PROFIT_RESOLVED` fires under
  `TakeProfitSource.NONE` too: "a trade with no target because the operator
  disabled targets" is a decision somebody will want to find in a log.

### A finding worth recording

**The geometry stage cannot refuse a signal that reaches it.** Every condition
`validate_geometry` checks has already been checked by an earlier stage on the
same objects — `resolve_stop` verified the stop price, its distance and its side,
and `resolve_take_profit` verified the target's side, both building their results
through models that refuse a non-positive distance at construction.

It is kept, because the Phase 0 rationale still holds: the two functions have
different callers, the geometry can come from configuration as well as from a
signal, and a check that trusts its input to have been verified elsewhere fails
the first time somebody calls it directly. But the honest position is recorded in
`process_signal.py` and pinned by a test, rather than left for a reader to work
out from the call sequence — a stage in the pipeline that can never fire is
either dead code or a bug, and the code cannot tell you which.

The consequence for coverage: `process_signal.py` is at **99%**, the one uncovered
line being that refusal. The companion refusal — an incomplete specification, at
the `spec` stage — *is* reachable and *is* tested, because a specification arrives
from outside the process and may have been deserialised by someone else's code,
which is a real difference from the stop and the take profit.

---

