# Controlled demo execution test — preparation (no order sent yet)

A single controlled real execution on the MT5 **DEMO** account, prepared but
**not executed**. Nothing in this document places an order. The execution
itself is one explicit command, run once, by a person, in a later step.

## Why no new command was needed

The existing `trade` command already is the single controlled execution path:
one signal file in, at most one order out. `tests/unit/test_demo_execution_safety.py`
pins that structurally — exactly one `process()` call site in `_trade`, no loop
construct that could iterate over entries or attempts, `UNKNOWN` surfacing as
exit 3 rather than a retryable refusal. No strategy, risk, SL/TP, or gate code
was touched for this preparation, and none needs to be.

## The execution command (run once, later, by a person)

```powershell
cd E:\signal-to-trade-bridge
signal-to-trade-bridge trade .\example-buy.json --confirm-demo
```

Rules for that run: type it once, do not loop it, do not re-run it on any
non-zero exit without first reading the outcome section below. A second run of
the same signal file is expected to refuse as a duplicate — that refusal is
the idempotency ledger doing its job, not a failure.

`--confirm-demo` is the explicit human acknowledgement. It is per-invocation:
arming the machine never implies it.

## Safety gates required (all must hold; each refuses independently)

| # | Gate | Where it is enforced |
|---|---|---|
| 1 | `--confirm-demo` on the command line | CLI confirmation gate |
| 2 | `BRIDGE_EXECUTION_ENABLED=true` | CLI config gate + `build_live` |
| 3 | `BRIDGE_DRY_RUN=false` | CLI config gate + `build_live` |
| 4 | Control IDs match this terminal's build | `check_control_ids` in `build_live` |
| 5 | Terminal running, already logged in (bridge never launches it) | `build_live` bindings |
| 6 | Account is `DEMO` | execution layer `demo_only` policy |
| 7 | Ledger readable | composition root, before anything that writes |
| 8 | Kill switch clear | envelope + pre-click check |
| 9 | Risk engine, sizing, SL/TP validation, broker minimum | decision pipeline |

## Pre-flight (all read-only; sending nothing is structural, see tests)

Run in this order. Every step must pass before the next; any refusal stops
the sequence.

```powershell
# 1. Machine, terminal paths, control IDs against the detected build.
signal-to-trade-bridge doctor
# Expect: terminal ok, data directory shown, control ids ok with the build
# in brackets matching the running terminal. Any REFUSED here is unfixable
# by configuration -- re-measure, do not proceed.

# 2. Arming state, set by hand in .env (never by a command).
signal-to-trade-bridge config
# Expect: "execution_enabled": true, "dry_run": false,
# "mt5_terminal_path" pointing at the Alpari demo terminal64.exe,
# "mt5_data_path" the hash-named data folder of THAT terminal.

# 3. Signal validity and sizing through the dry-run root (no executor).
signal-to-trade-bridge check .\example-buy.json
# Expect: exit 0/1 with a decision (not exit 2), volume > 0 computed from
# the account and contract, stop and target on the correct sides.

# 4. Exact-request preview through the real live wiring (recorder, no click).
signal-to-trade-bridge trade .\example-buy.json --what-if
# Expect: the request that would go to the terminal -- symbol, direction,
# volume, entry, stop, target, comment. Confirm the volume is within the
# broker minimum/maximum and the SL/TP are valid prices before arming.
```

Before-execution verification checklist (tick each off by hand):

* [ ] MT5 terminal running, logged into the Alpari **demo** account (title bar).
* [ ] `doctor` control ids `ok` on the detected build.
* [ ] `config` paths match the running terminal's install and data folders.
* [ ] `check` decision valid; volume, SL, TP sane against the broker spec.
* [ ] `--what-if` request matches what you intend to send.
* [ ] No `KILL_SWITCH` file in the configured log directory.
* [ ] Account flat or the new position accounted for (no stacking intent).

`.env` changes, if any, are made by hand in a text editor. No command in
this project edits `.env`, enables execution, or closes positions.

## Outcome handling (read before running)

| Exit | Meaning | Action |
|---|---|---|
| `0` | Sent and verified | Record the report below from the terminal + ledger |
| `1` | Refused (incl. duplicate on re-run) | Read the reason; a duplicate refusal after an exit-0 run is correct |
| `2` | Fault (terminal, ledger, config, control IDs) | Fix the cause; nothing was sent |
| `3` | `UNKNOWN` — may or may not have reached the terminal | **Do NOT re-run.** Verify manually, then decide by hand |

`UNKNOWN` is the one that matters: a retry may open a second position. The
ledger (`BRIDGE_LOG_DIR`, JSONL audit log) records every attempt; settle an
unknown by looking at the terminal and the ledger, never by resending.

## Post-order verification (expected position/order in MT5)

Confirm in the terminal UI (Trade tab / Toolbox → Trade) and the ledger, then
record:

* order ticket:
* symbol:
* BUY/SELL:
* volume:
* entry price:
* SL:
* TP:
* comment:
* resulting position state (open, ticket, current P/L):

The bridge's own decision output carries `status` and `message`; the
`ExecutionResult.ticket` names the position when the layer could identify one.
The terminal is the authority on what exists; the ledger is the authority on
what was attempted.

## Explicitly out of scope

* Closing the position (manual act afterwards, by the operator's own decision).
* Any second order, loop, retry, grid, or martingale — the path cannot express
  them (see tests) and the operator must not emulate them by hand either.
* Changing strategy, risk, SL/TP, gates, or `.env` from any command.
