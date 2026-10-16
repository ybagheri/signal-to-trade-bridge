> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

### What Phase 7b Built

```
adapters/auto_trade/
  __init__.py    the package docstring: what is wrapped, and what is refused
  bindings.py    AutoTradeBindings, AutoTradeUnavailable, load_bindings()
                 the one import site; eight upstream names, declared
  executor.py    AutoTradeExecutor    TradeExecutor
                 SENTINEL_PROFILE     the stand-in for a field upstream never reads
adapters/fake/
  executor.py    FakeTradeExecutor    the other TradeExecutor
tests/unit/
  test_auto_trade_executor.py  50 tests
```

**The executor is built, and it is tested against the real thing rather than a
transcription.** The maintainer supplied `D:\Projects\auto-trade`, and it turned
out to be installed editable -- so `TestTheRealPackageIfItIsInstalled` runs the
real `ExecutionWorkflow`, the real `RiskEngine`, a real `JsonExecutionLedger` and a
real `DryRunTerminalAdapter` on every suite run. No terminal is opened and no
control can be reached, which is asserted rather than assumed, because that
assumption *is* the safety model.

**It is a wrapper, not a second execution layer.** `ExecutionWorkflow` already
owns the state machine, the ledger ordering, the kill-switch check, the
`demo_only` policy, and the classification of a failed click as `UNKNOWN`. Those
are the mechanisms standing between a decided trade and a duplicate position.
Re-implementing any of them would be a second opinion about whether a trade
happened -- which is the exact shape of work Phases 4 and 5 found real bugs in.
So the mapping is thin and two-way:

```
ExecutionRequest  ->  TradeSignal        one direction enum, four numbers
ExecutionResult   ->  ExecutionResult    one status, one ticket
```

### Four decisions in the mapping, and why each one is pinned by a test

**The evidence score is not sent as `confidence`.** `TradeSignal.confidence` is
typed and validated as a probability in `0..1`. The bridge's `evidence_score` is
explicitly *not* one -- Phase 2 established that. It travels in `metadata` under
`bridge_evidence_score` instead. The strongest form of the test sends a score of
`4.2`: if the adapter ever starts passing it through, the double's own `0..1`
check rejects it and the test fails naming the regression. Rule 9 exists to
prevent exactly this misreading, and the adapter is where it would have been
committed silently.

**`CLOSED` maps to `UNKNOWN`, never to `REJECTED`.** Upstream has six statuses
and the bridge knows five. A closed position means the order *was* placed and later
closed, so re-sending is the duplicate rule 5 forbids -- whereas `REJECTED` says
"definitely not placed", which is a licence to retry. `UNKNOWN` is the only honest
answer. The bridge never closes, so this should never arrive; it is handled rather
than ignored because a status from nowhere is the signal that something upstream
changed.

**`execute` is not exception-free, and that shaped the whole `submit`.**
Upstream's `except Exception` around the click deliberately *re-raises*
`ExecutionUnknownError`, so an adapter assuming a return value would crash on the
one outcome that most needs a record -- and the crash would take the record with
it. `submit` catches broadly and maps everything to `UNKNOWN`: not retried,
escalated, recorded. The test drives a real `UnknownExecutionError` and asserts
`is_retryable is False`.

**`executed_price` is always `None`.** Upstream's `VerificationEvidence` carries
`baseline`, `observed` and `position_id` -- references to the snapshots it
compared -- and no fill price. A click is not a fill, which is why
`execute_order` returns `REQUESTED` and never `ACCEPTED`. So there was never a
price to report, and putting the requested price there would be the most
confident-looking fabrication in the adapter.

### Three things the source corrected that reasoning alone would have got wrong

- **`profile` is required and never read.** The field appears once in the whole
  315-line class, in the assignment, and upstream's own tests pass
  `type("Profile", (), {})()` -- an object with no attributes at all. The bridge
  passes a named `SENTINEL_PROFILE` for the same reason: inventing
  plausible-looking terminal attributes would be asserting facts about the machine
  that nothing has established.
- **There is no factory.** No `build_workflow`, no `create_workflow`; the only
  assembly upstream is two private CLI functions. So the bridge's composition root
  must build the workflow, which is *why* `AutoTradeExecutor` takes an
  already-assembled one and refuses to construct its own -- a test walks the AST
  for an `ExecutionWorkflow(...)` call and fails on it.
- **A dry run has two independent guards,** not one: the workflow refuses to call
  `execute_order` at all (`workflow.py:102-110`), *and* `prepare_order` closes the
  dialog when the gate says dry-run (`window_manager.py:644-645`). The CLI passes
  `ExecutionGate(enabled=False)` regardless of configuration. The bridge keeps no
  way to reach either guard: a test fails if the adapter module references
  `MT5DesktopAdapter`, `ExecutionGate` or `CloseGate` **by name** -- checked over
  the AST, because both appear in these docstrings explaining why they must not be
  used, and a text search would fail on the documentation of the rule it enforces.

### What Phase 7b deliberately did not do

**It is not wired into `ProcessSignal`, and that test still passes on purpose.**
Execution arrives with the ledger and the kill switch around it, not before.
Wiring an executor in while the idempotency store is absent is precisely the
failure the architecture was drawn to prevent.

**One bug this phase's own tests found, which is the argument for having them.**
`load_bindings()` was initially called *outside* the `try` that converts a
translation failure into an `UNKNOWN` result. So on a machine without the
checkout, a missing package raised out of `submit` -- and an exception out of
`submit` loses the trade record, on exactly the situation where an operator most
needs to see which signal did not go out. The fix moved the load inside the
`try`; the test that catches it simulates the `ImportError` rather than relying on
the package being absent, because a test that only passes because a dependency is
missing stops testing the moment somebody installs it.

### One design choice that was rejected

`AutoTradeBindings` originally verified the upstream subset in `__post_init__`.
That is wrong: the check imports the real package, so a dataclass that imports on
construction cannot be built from doubles -- which made the mapping untestable on
exactly the machine where it most needs testing. `verify()` is now explicit and
`load_bindings()` calls it, so the guarantee holds on the path that matters and the
suite stays runnable without the checkout.

