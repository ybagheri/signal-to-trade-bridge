> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

### What Phase 9 Built

```
application/
  execution_envelope.py  ExecutionEnvelope, build_execution_envelope()
adapters/auto_trade/
  ledger.py        AutoTradeLedger, open_ledger()   the two-phase port, translated
domain/
  models.py        ExecutionRequest.take_profit is now optional
ports/
  __init__.py      IdempotencyStore is two-phase, not one-shot
tests/unit/
  test_execution_envelope.py  33 tests, incl. 8 against the real JSON ledger
  test_execution_wiring.py     27 tests
  test_signal_identity.py      +5, the collapse this phase found
```

**Phase 9 did one structural thing and it is the thing that mattered: it turned an
absence into a type.**

For three phases the rule was "the pipeline must not import an executor", enforced by
a test reading a source file. That test did its job — and it had to be retired,
because **an absence cannot also authorise.** It could forbid the mistake and say
nothing about whether the right thing was in place, so Phase 9 could not say "yes,
and here is why that is safe."

### The envelope

`ExecutionEnvelope` has **no public constructor.** Its `__init__` exists only to
refuse, the three fields are set through `object.__setattr__` by
`build_execution_envelope(executor, idempotency, kill_switch)`, and every field is
required. `ProcessSignal.wire_execution` takes the envelope and nothing else, so
there is no way to hand the pipeline an executor without the ledger beside it.

Four checks in the factory, each a case where the object *looks* complete and is
not. The one worth naming: **the same object passed as two collaborators is
refused.** A stub satisfying both the executor and the ledger would pass a naive
"all three present" check while recording nothing and stopping nothing — which is
worse than an obviously incomplete envelope, because it looks complete.

*(One detail recorded because it cost time: `slots=True` cannot be combined with
`object.__new__` plus `object.__setattr__` — it raises. So this is a plain class with
read-only properties and a refusing `__setattr__`, not the tidier frozen dataclass.)*

### The ledger, and the two properties that make it worth wrapping

Upstream's `JsonExecutionLedger` already guarantees what this project needs, so the
adapter translates three calls and adds no behaviour. Read from
`application/ledger.py`, because none of it is obvious from the interface:

1. **the write is atomic** — a `.tmp` sibling then `replace()`, so a crash mid-write
   leaves the previous file rather than a truncated one;
2. **the attempt is recorded before the click** — upstream calls `record_attempt`
   *before* `adapter.execute_order`, so a process that dies between them leaves a
   pending record an operator can settle rather than no record at all;
3. **a second attempt cannot take over the first one's record** — `record_result`
   returns silently on a mismatched `execution_id`.

And the fourth, which is the one the adapter exists to preserve:

4. **an unreadable ledger is a refusal, not an empty one.** Upstream's `_read`
   raises for a corrupt or wrongly-shaped file, and `open_ledger` does not catch it.
   **A ledger that silently read as empty would answer "no, this signal has not been
   acted on" for every signal ever recorded** — a green light on a duplicate. Every
   failure in `AutoTradeLedger` raises `AutoTradeUnavailable`, and all of them carry
   one message, because an unreadable ledger cannot answer *any* question and three
   messages differing in a noun would read as three different faults.

### The port had to change shape, and Phase 2's key had a hole

**`IdempotencyStore.record(key, mapping)` became `record_attempt` + `record_outcome`.**
The single-call shape cannot express the two-phase write above, and the collapse is
invisible until the first crash — at which point the ledger says the signal was never
attempted and the trade is free to repeat. `execution_id` ties the halves together and
is required on both.

**The `signal_id` key can collapse two trades into one**, and the handoff asked Phase 9
to find out. It could not check against real engine output — `albrooks` is not
installed here — so it checked what the key is built from instead:

```
_bar_index(result)  ->  result.last_closed_bar, or -1
_bar_time(result)   ->  None unless a bar feature matches that index
```

Both fallbacks are **permitted**: ordinary attribute lookups, nothing raised, nothing
logged. And `Signal.bar_index` **defaults to `-1` on the model itself**, so the
mapper's fallback and the model's default agree. A signal carrying `bar_index=-1` and
`bar_time=None` produces a key with **no bar in it at all** — and the bar is the whole
unit of identity. Two such readings hash to the same value, so the second is refused
as a duplicate even though it is a different trade.

The blast radius is bounded, and the tests say by how much: different symbols do not
collide, different setups do not collide, and either `bar_index` **or** `bar_time`
alone is enough to separate. It takes both being absent. That makes it a serious
defect on one symbol and one timeframe, not a systemic one — and it is the failure the
ledger exists to prevent, arriving *through* the ledger.

**Not fixed here**, because the fix is a decision about what identity means when the
engine reports no bar, and Phase 10 is where a composition root exists to make it.
The options are refusing the signal (fail-closed, and correct), or folding the
reading's own timestamp in — which would make every re-read of the same unbarred
reading a new trade, which is the opposite defect.

### The retryable / ledger disagreement, written down rather than resolved

`ExecutionResult.is_retryable` is `True` for a clean rejection. Upstream's ledger
disagrees: `contains()` is a plain key lookup, so an entry written for a `REJECTED`
still matches and the same signal id is refused with `"duplicate signal id"`.

**Both are true and they are about different moments**, so neither was changed.
`is_retryable` describes what a caller may do with a result it already holds;
`contains` describes what a *new* attempt would meet. But an operator who retries on
`is_retryable` will be refused, and pretending otherwise would be worse than the
refusal — so it is in the port's docstring, in `docs/risk-management.md`, and here.

