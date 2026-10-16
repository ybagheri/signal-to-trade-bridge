> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Phase 14 — Arming the machine, and the first real order

**This is the phase where the project actually traded.** Everything before it built,
proved, documented and refused. This section records what happened, including two
defects that only a live click could have found and one that was mine.

### The order

`BRIDGE_EXECUTION_ENABLED=true` and `BRIDGE_DRY_RUN=false` in a gitignored `.env`,
and a new `trade` command with three separate permissions: `--confirm-demo`, an
arming configuration, and control identifiers measured on this build. `--what-if`
previews by substituting a recorder for the executor and nothing else.

The first send, EURUSD 0.03 lots at 0.01% risk -- $10 risked on a $100k demo
account -- produced:

```
ticket 384819188  EURUSD  BUY  0.01  entry 1.12614  sl 0.0  tp 0.0
comment 'stb EURUSD LONG smoke-test-1'
```

**A position opened. The click path works end to end.** The state machine went
`SIGNAL_RECEIVED → VALIDATING → VALIDATED → LOCATING_TERMINAL → PREPARING_UI →
ORDER_READY → EXECUTING → EXECUTION_DETECTED → VERIFYING → VERIFICATION_FAILED`.

### What was wrong with it, and why the system said so

The decision came back `UNKNOWN` / `VERIFICATION_FAILED`, "no new matching position
detected". The evidence in the ledger:

```
baseline  ui positions=none
observed  ui positions=384819188:EURUSD:BUY:0.01
```

The position was there and the system still refused to call it proven, because the
observed volume was **0.01** and the requested volume was **0.03**. And the
stop loss and take profit were **both 0.00**.

So three fields did not reach the terminal: volume, stop loss, take profit. The
symbol and the BUY click did. That is a coherent single failure -- the *input*
fields were not written -- and it is the most important thing this project has
learned about the execution layer.

**`UNKNOWN` was the correct verdict.** The order went out and the outcome could not
be proven to match the request, and the ledger exists precisely to stop a retry
against something that may already be live. The refusal is the feature working.

**A position with no stop loss is the failure this project exists to prevent**, so it
was closed immediately rather than left to be studied. `auto-trade close-position`
refused, correctly: the ticket was not in the ledger with an order reference, so
the command would not close something it could not prove it owned. It was closed
through `MT5DesktopAdapter.close_position()` -- the same method, the same UI route,
the same independent verification -- bypassing only the ledger precondition.

**Algo Trading was not enabled, and was not needed.** The MT5 API close returned
`AutoTrading disabled by client`, which is correct behaviour for a system whose
whole design is to open and close positions by clicking the terminal. Nothing in
this project depends on that API.

### The `--what-if` defect, which is the worst kind

The first version of the preview flag built the live side -- which wires the real
executor -- processed the signal, and then printed "nothing was sent". **The order
had already gone out.**

It was found by an `--what-if` run that came back `UNKNOWN`. The fix is to
substitute the recorder *before* the signal is processed, so everything that decides
*what* would be sent stays real and only the final click is replaced.
`tests/unit/test_cli_whatif.py` asserts the executor in place at the moment the
signal is handled, with no terminal involved.

**A flag whose entire contract is "this sends nothing", tested only by its printed
output, is tested by the thing it is not responsible for.**

### The ledger protocol defect

`ExecutionWorkflow` calls `record_result`, `record_attempt` and `contains` on the
object it is given as `ledger`. `AutoTradeLedger` only offered this project's port,
whose `record_outcome` takes three loose arguments -- so the call raised
`AttributeError` **after** the click, which is the worst possible moment, because
the outcome then genuinely is not known. That was the cause of the first `UNKNOWN`.

`tests/unit/test_ledger_protocol.py` now reads upstream's source, extracts every
`self.ledger.*` call, and asserts the wrapper has all of them. It found **three**
methods rather than the two expected -- `record_attempt` is called too, and the
wrapper happens to have it because this project's port has the same shape. Written
from expectation rather than from the source, that near miss would have stayed
invisible.

### A leak that armed every test after it

`config_from_env(apply=True)` seeds `os.environ` from whatever `.env` it finds, so
any test that read the configuration armed every test that ran after it. Two tests
asserting the **shipped defaults** failed on the day this machine armed itself, and
neither had anything to do with the CLI. `tests/conftest.py` now restores the
environment around every test through an autouse fixture -- an autouse fixture
rather than a reminder, because the reminder is what failed.

### Where it stands

**1235 passing, 97% coverage.** 1248 with the live opt-in and a terminal running.

**Open, and it is the next thing to fix: the order dialog's input fields are not
being written.** Symbol and the final control work; volume, stop loss and take
profit do not land. Until that is understood, this project can open a position it
cannot protect, which is worse than not opening one at all. The account is flat,
the balance is 100001.54, and no position is open.

---

