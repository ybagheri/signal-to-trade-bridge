> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Remaining Work

### Phases 5–13

As laid out in `README.md`.

#### Phase 5 — 1:1 risk/reward  ← **done, and it was not a no-op**

The handoff for this phase said the work was "largely already implemented — verify
the wiring and add the missing coverage". The wiring turned out to be *present but
not connected*, and the default policy did not do what its own documentation said.
See *The finding that made this phase worth doing* above.

**Do not read this entry as confirmation that a phase can be skipped because its
output looks finished.** Three of the four bugs fixed in this phase were in code
that Phases 1 and 3 wrote, had 200+ passing tests, and was described here as
complete.

#### Phase 6 — trade validation pipeline  ← **done**

`ProcessSignal.process(signal) -> TradeDecision`, running every stage in order and
stopping at the first refusal. See *What Phase 6 Built* above.

#### Phase 7 — adapters  ← **done, both halves**

**Done:** the MT5 data adapter and the `auto-trade` execution adapter. See *What
Phase 7 Built* and *What Phase 7b Built* above. The data half is tested without a
terminal at all, which is the whole reason the bindings-injection pattern was chosen
in Phase 0. The execution half is tested against the **real** `ExecutionWorkflow`,
`RiskEngine` and ledger, with a `DryRunTerminalAdapter`, so no terminal is opened and
no control can be reached.

`AutoTradeExecutor` wraps `auto-trade`'s `ExecutionWorkflow`. **Wrapped, never
bypassed**, per a Phase 0 decision that still stands: calling the terminal adapter
directly would skip the risk engine, the kill switch, the ledger, the state machine
and the audit log.

**Why the earlier refusal was right even though the blocker is gone.** The
executor was held back because `auto_trade` was a private repository not installed
here, so the adapter would have had to be written against a transcription of its API
with nothing to check it against. That would have produced code which looks
finished, has passing tests asserting my own assumptions back at me, and has never
been compared to the thing it calls — precisely the shape of work Phases 4 and 5
each found real bugs in, while believed complete. The checkout arrived; the adapter
was written against the source; and the transcription is no longer load-bearing.

**The contracts are recorded where the work will look for them:** `docs/integration.md`
§2.14 is the `ExecutionWorkflow` reference, written from the source in Phase 7b
rather than transcribed in Phase 0. §2.2–2.5 still has the `TradeSignal` and
`ExecutionResult` shapes, and §2.14.3 has the six-status vocabulary including the
`UNKNOWN` state that must never be auto-retried and the `CLOSED` one that maps onto
it.

**It arrives with the ledger, not before.** `test_this_module_holds_no_executor` in
`tests/unit/test_process_signal.py` asserts the pipeline imports no `TradeExecutor`,
`IdempotencyStore` or `KillSwitch`, and **it should keep passing until the ledger
does** — not merely until an executor exists.

#### Phase 8 — dry run reporting  ← **done**

See *What Phase 8 Built* above. `application/dry_run.py` turns a validated intent
into the request that *would* be sent and asks `auto-trade`'s own risk engine what
it would say.

#### Phase 9 — idempotency  ← **done**

See *What Phase 9 Built* above. The envelope, the ledger over the real JSON ledger,
the two-phase port, and the `ExecutionRequest.take_profit` fix Phase 8 left open.

#### Phase 10 — end-to-end integration  ← **done**

See *What Phase 10 Built* above. The composition root, and the `signal_id` repair
Phase 9 left for here.

#### Phase 11 — MT5 / demo validation  ← **done**

Closed by measurement, not by editing a number: `auto-trade terminal-check` reported
13 of 13 controls OK on build 6230 and `MEASURED_ON_BUILD` / `EXPECTED_BUILD` moved to
6230 afterwards (see Phases 13–14). The heading and the three-step list that used to
sit here described the state *before* that and were removed in Phase 16.

Phases 12–14 are done. **Outstanding work now lives in `ROADMAP.md`**, which is kept
current; this section is history and is not updated.

---

## Open Questions
