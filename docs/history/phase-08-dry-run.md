> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

### What Phase 8 Built

```
application/
  dry_run.py     DryRunReport, DownstreamVerdict
                 build_execution_request(), report_for()
adapters/auto_trade/
  preflight.py   DownstreamLimits, ask_downstream_risk()
tests/unit/
  test_dry_run.py  39 tests
```

**Phase 6 made `DRY_RUN` an honest placeholder. This phase makes it an answer.**
"Every check passed, nothing was sent, and nothing could have been" is true and it is
not much use: a dry run exists to answer one question before real money is involved,
and the question is *what exactly would this system do?*

### The missing link, and it was genuinely missing

`ExecutionRequest` — the type the executor accepts — was **never constructed anywhere
in this repository** before this phase. The whole mapping from a sized intent to an
order existed only as a hand-written fixture in the executor's tests. So the boundary
the execution project is handed had no producer outside a test, and nothing in
`src/` had ever exercised the numbers a real trade would carry.

:func:`build_execution_request` is that producer, and it **copies every number rather
than recomputing any**. A dry run that re-derived the volume or the stop would be a
second sizing pass, and two passes that disagree produce a report describing an order
the bridge would never send — which is worse than no report, because it looks
authoritative.

### The disagreement this phase was actually about

The bridge validates against its own rules and `auto-trade` validates against its
own rules again. **The two rulebooks disagree by default:**

| Check | Owner | Default |
|---|---|---|
| risk per trade | this bridge | 0.5% of balance |
| minimum volume | this bridge | refuse below broker minimum |
| allowed symbols | `auto-trade` | `EURUSD,XAUUSD,YM` |
| maximum volume | `auto-trade` | `1.0` |
| orders per minute | `auto-trade` | `5` |

So **a signal on `GBPJPY` passes every check in this bridge and is refused
downstream.** Same for a 2.0-lot order, which the 0.5% rule will cheerfully compute on
a large balance and `auto-trade`'s absolute cap will refuse however well sized it is.

Before this phase the only place any of that became visible was a `REJECTED` result
on a live account: in production, on real money, with the reason arriving as a
downstream result rather than as a decision. The dry run now asks `auto-trade`'s own
`RiskEngine` — a pure function of the request and the configured limits — and reports
the answer as a blocker with the reason verbatim.

### Three properties of that answer, each enforced by a test

- **It is a prediction, and the type says so.** `DownstreamVerdict` carries
  `evaluated`, because the real gates also depend on the account type, the kill
  switch, the ledger and the terminal window. **An unevaluated gate reporting as
  accepted is a construction error, not a normalisation** — `accepted=True` with
  `evaluated=False` raises, because an unevaluated gate reporting as accepted is
  indistinguishable downstream from one that ran and passed.
- **No engine wired means `evaluated=False`, never `accepted=True`.** A missing
  package, a renamed class and a bug all look identical from here, and all three mean
  the check did not happen. That is the most expensive possible silence: a green tick
  on the one gate that would have refused the trade.
- **The report names every blocker, not the first.** An operator fixing them one at a
  time, each fix revealing the next, is the slowest way to answer "can this trade ever
  run here?".

### What this module still may not do

**It holds no `TradeExecutor`.** Not an import, not a type hint, not a call — and
that is awkward, because an executor exists one package away and is perfectly usable.
`test_this_module_holds_no_executor` asserts it, and so do three tests in
`test_dry_run.py`: the dry run imports none, and `preflight.py` references neither
the executor nor `MT5DesktopAdapter`, `ExecutionGate` or `ExecutionWorkflow`.

The checks are over the AST rather than the raw source, because all four names appear
in `preflight.py`'s own docstrings explaining precisely why it may not use them. A
text search would fail on the documentation of the rule it enforces — and a structural
test that fires on correct code gets deleted rather than fixed.

`preflight.py` also constructs the `TradeSignal` itself rather than reaching through
the executor for one. The duplication is deliberate (two tables, one test asserting
they agree) because the alternative is inheriting the executor's ability to send.

### One known gap, deliberately not worked around

`ExecutionRequest.take_profit` is **required**; `TradeIntent.take_profit` is optional
and so is `auto-trade`'s `TradeSignal.take_profit`. `TakeProfitSource.NONE` is a
supported, documented, tested configuration, so **this combination is reachable in
production and currently cannot be expressed.**

Two options were considered. Making the boundary field optional is correct and is
**Phase 9's work**; refusing the trade is the fail-closed direction but means an
operator who deliberately disabled targets gets every trade refused at the last step
with a message about a model. Rather than invent a second code path, the refusal
names the gap and says which phase fixes it. The report still exists and still carries
the arithmetic.

### A bug this phase's own tests caught, and one they prevented

`_ORDER_ACTIONS` in `preflight.py` was written as an **empty dict** on the first pass
— the direction mapping the engine checks `symbol` and `volume` against would have
been missing entirely. The type annotation was a `dict` so nothing complained, and the
first test to run it failed. It is now populated, and
`test_the_two_direction_tables_agree` keeps the two copies from drifting.

The second is not a bug but a rule: the file's most important property is an
*absence*, and the first draft of the structural test was a text search that would
have failed on the correct code.

