> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

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

