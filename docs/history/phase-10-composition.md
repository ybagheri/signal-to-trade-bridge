> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

### What Phase 10 Built

```
composition.py            Bridge, CompositionRefusal, build_bridge()
adapters/albrooks/
  source.py                _guard_bar_identity(), _newest_bar()
domain/enums.py            SIGNAL_BAR_UNKNOWN
tests/unit/
  test_composition.py           31 tests
  test_signal_bar_identity.py   16 tests
```

**Nine phases built the pieces and every one of them was tested against a
stand-in. This is the first place they are wired to each other** -- and the three
things only an assembly can find are: a wiring that is wrong while every individual
component passes, the default configuration's inability to execute *behaviourally*
rather than as a flag someone reads, and whether the refusals compose.

### The `signal_id` collapse is closed, and it is a repair rather than a refusal

Phase 9 left the decision to here. The answer is better than refusing: the bar is
**knowable at exactly one place** -- the adapter that read the bars and asked the
engine to analyse them -- and at that point the fallback bars are still in hand.

So `_guard_bar_identity` does not refuse. It recovers:

```
engine reported a bar (index >= 0 OR time is not None)  ->  left exactly as it was
neither, and the series has them                        ->  the series answers
neither, and the series has neither                     ->  SIGNAL_BAR_UNKNOWN
```

**"Left exactly as it was" includes the mixed case.** A signal with
`bar_index=-1` and a real `bar_time` is *not* repaired, because either field alone
separates two readings and repairing the index would mix the engine's authority with
ours in one key. When the engine said something, it wins; the recovery only fills a
total blank.

The index is the **position from the end**, not from the start, because the engine
indexes that way and the two must agree -- a recovery that disagreed would put the
reading on a bar the engine never saw, which is worse than refusing.

### The composition root, and why the live path is refused rather than degraded

`build_bridge` assembles the MT5 providers, the position reader, the ledger, the
preflight and the pipeline, and **refuses the live path outright**:

> execution is enabled and dry-run is off, so this bridge would place real orders --
> but the live execution side is assembled by Phase 11 and not yet by this module.
> Refusing rather than degrading to a dry run, because a silent degradation is how an
> operator's setting gets ignored.

That sentence is the phase. An operator who enables execution today is told so,
rather than handed a pipeline that quietly reports and looks like it obeyed.

**The ledger is opened before anything that could write**, and refused rather than
replaced. ``JsonExecutionLedger`` raises on a file it cannot read, and an unreadable
ledger must stop the process while there is still nothing to undo. It is opened even
on a dry run, deliberately: a dry run is exactly when somebody wants to know the
ledger is readable, and finding out at the first real order is the worst time.

### What the composition root deliberately does not assemble

* **The terminal adapter and the ``ExecutionWorkflow`` with it.** Both need the
  private execution package, and making a *dry run* depend on it would mean a missing
  dependency silently disables reporting rather than trading. The two are independent
  and conflating them is the mistake.
* **The signal source.** The bridge reads signals; choosing them would be a strategy
  in disguise.
* **A CLI.** ``cli/`` stays empty until Phase 12.

### Two things this phase's tests found, both about assumptions

**The stub was shaped like the wrong thing.** The first version of this file's
bindings double carried the *bridge's* ``SymbolSpec`` fields, and every test failed
with ``SYMBOL_SPEC_UNAVAILABLE``. The adapter maps ``trade_contract_size`` and the
rest one for one, so a double carrying ``contract_size`` tests a conversion that does
not happen. **That failure is the argument for this file existing**: every other test
drives a real adapter with a correct double, and only an assembly exercises the seam
where a mis-shaped double looks like a broken adapter.

**The preflight cannot show a disagreement through the composition root.** The root
builds the preflight's limits from the *bridge's* ``allowed_symbols``, so the two
projects agree by construction -- deliberately, since a root that configured the
downstream limits independently would be a second policy nobody maintains. The test
that claimed to prove a `GBPJPY` refusal was therefore asserting something untrue,
and was corrected to state the limitation rather than kept for its green tick. **The
genuine disagreement tests live in ``test_dry_run.py``, where the caller supplies the
limits.**

### The concurrency limit, seen through the composition

Phase 7's residual case is now visible from the outside. Without a data path the
count is zero, so a limit of **one** admits a trade on a number that was never read --
and the report still says the count was not read. With a limit of **zero** the trade
is refused at the concurrency gate. And with a *dead terminal* the refusal moves
earlier, to the account gate, as soon as the limit is configured. Three cases, three
different refusals, and the safe configuration -- leave the limit unset -- is stated
in `docs/risk-management.md` rather than discovered.

