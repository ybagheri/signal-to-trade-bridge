> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Phase 15 — Stopped mid-investigation; here is exactly where

**Read this first. The headline is not a fix, it is a diagnosis plus an unfinished
guard.**

**Account state: flat. Balance 100000.91, no positions, no pending orders, one
terminal (pid 11228, account 50000001). No order is open.**

### The root cause, established with evidence

Every order this bridge placed came back the same way: **0.01 lots where 0.03 was
asked for, and no stop loss and no take profit**, while the order dialog read back
exactly what had been requested and MT5 had applied all of it.

That rules out a write race and a focus problem, and a screenshot of the terminal
settled it: the toolbar shows **Algo Trading in red**, which on this Alpari build
means the terminal is in **One Click Trading** mode. In that mode `Buy by Market`
does not send the order ticket's fields at all — it sends the **Toolbox Trade
panel's** values, and that panel has no stop loss. That is precisely the shape of
every fill.

So `confirm_dialog_matches` was never wrong. It reads the dialog, the dialog was
right, and **no reading of the dialog can reveal which panel the click will use.**

Measured, for the record: `account_info().trade_mode == 0` and
`terminal_info().trade_allowed is False` on this account.

### What is committed, and what each piece is actually worth

**`auto-trade` (D:\Projects\auto-trade) — the settle wait, uncommitted.**

`prepare_order` now polls every written field until MT5 reports it, through
`_await_fields_applied`. MT5 genuinely does apply order fields asynchronously —
measured here at **250 ms / 500 ms / 800 ms** for volume, stop loss and take profit —
and the old code read each field once, which proves only that the write reached the
control. `tests/unit/test_order_field_settling.py` (14 tests) drives the settling
behaviour rather than the write.

**It is a real defect and a correct fix, but it was NOT the cause of the bad fills.**
It was written while still believing the cause was a race. Keep it; do not credit it
with anything.

Also changed in that repo, uncommitted: `_same_number` compares order-field readings
numerically, because MT5 reformats what it displays and a string comparison would
refuse correct orders.

**`signal-to-trade-bridge` — the mode check, uncommitted and NOT WORKING.**

`src/signal_to_trade_bridge/adapters/mt5/execution_mode.py` reads the mode and
`refuse_one_click_order()` produces the refusal message.
`tests/unit/test_execution_mode.py` (11 tests) covers both, including "an unreadable
terminal is not assumed safe".

**It is inert on the live path, and this is why:**

```
>>> _looks_like_mt5_bindings(load_bindings())
False
```

`AutoTradeExecutor.__init__`'s `bindings` parameter is the **`auto-trade`** bindings
namespace (`module`, `ExecutionWorkflow`, `TradeSignal`, …), not the MT5 bindings. The
duck-type check added to keep the executor's own tests honest therefore returns
`False` in production too, so the refusal never runs. This was found in the step
immediately before stopping, and it is the first thing to fix.

`live.py` was also edited to pass `live_bindings` (the MT5 ones) into `_finish`, and
`_finish` passes them as `bindings=` — which is the wrong parameter, and is part of
the same mistake rather than a fix for it.

### The work that remains

1. **Give the executor the MT5 bindings separately.** Add a distinct parameter —
   `mt5_bindings` — rather than reusing `bindings`, because the two are different
   packages and conflating them is what caused this. `live.py` already has
   `live_bindings` in hand; pass it there.
2. **Delete `_looks_like_mt5_bindings`.** It exists only to stop the check breaking
   the executor's own tests, and once the bindings are the right object it is dead
   code that would silently disable the guard again.
3. **Decide whether the guard belongs in the executor at all.** It currently sits
   immediately before `workflow.execute`, which is the right place in this codebase,
   but upstream owns the click and upstream is what knows which panel the click uses.
   A refusal here is honest and cheap; the argument for upstream is that a rule about
   a terminal capability belongs beside the code that uses the terminal capability.
4. **Test it against the real terminal.** `check_control_ids` already reads the build
   from the same bindings, so `execution_mode` should join that seam rather than
   opening its own.
5. **Only then re-send.** With Algo Trading still off, a correct order cannot carry a
   stop, and the guard will refuse. That refusal is the expected result and is the
   proof the guard works.
6. **Enabling Algo Trading is the operator's decision**, taken deliberately and by
   hand — it changes what the terminal is permitted to do. No script in this project
   should toggle it on the way past.

### State of the suites

| Suite | Result |
|---|---|
| `signal-to-trade-bridge`, clean env | **1247 passed, 17 skipped** |
| `signal-to-trade-bridge`, MT5 + opt-in | **1247 passed, 4 skipped** |
| `auto-trade` | 615 passed, 1 failed, 1 skipped |

**The one `auto-trade` failure is pre-existing and not from this phase:**
`test_a_reviewed_env_file_may_enable_execution` asserts `this test needs the project's
own .env`, and that file does not exist in that checkout. Verified by stashing this
phase's changes — it fails identically without them.

`ruff check` and `mypy` are clean on every file this phase touched. `ruff format
--check` reports 35 files in `auto-trade` as unformatted, **all pre-existing** —
verified by stashing; neither of the two files changed here is among them.

### What this phase cost, plainly

Four real positions were opened on the demo account and every one filled with no stop
loss, because the mode was wrong and nothing checked it. All were closed; the account
is flat and the cost is round-trip spread on 0.01 lots, visible as the balance moving
from 99999.96 to 100000.91.

The failure that mattered was never failing to write the values into the dialog. They
were in the dialog every single time.

---

