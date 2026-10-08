# Roadmap

> The single place that says **what is done, what is next, and what is deliberately
> out of scope.** [`HANDOFF.md`](HANDOFF.md) is the long history; this file is kept
> short and current. If the two disagree about the *future*, this one wins; if either
> disagrees with the repository, the repository wins.
>
> Last updated: Phase 16 (2026-10-05).

---

## Done

| Phase | Scope |
|---|---|
| 0–6 | Audit, foundation, Al Brooks adapter, stop / take-profit / validation policies, risk and sizing, 1:1 reward:risk, the decision pipeline |
| 7 / 7b | MT5 account and symbol adapter; `auto-trade` execution adapter (wraps the real workflow, never bypasses it) |
| 8–10 | Dry run, idempotency and the execution envelope, end-to-end composition root |
| 11 | Live side assembled; control identifiers re-measured on build 6230 (13 of 13 OK) |
| 12–13 | CLI, public API, documentation, architecture review |
| 14 | Machine armed on a demo account; first real orders (exposed the One Click defect) |
| 16 | Test portability (33 failures on a clean machine → 0), personal data removed from the repository, One Click guard wired to the terminal and its mode test corrected, documentation synchronised |
| — | Configurable pre-submit pause (`BRIDGE_PRE_SUBMIT_DELAY_*`, off by default) taken after all bridge refusals and before `workflow.execute`; order-comment policy (`BRIDGE_ORDER_COMMENT_ENABLED=false` submits an empty comment). Previews record the pause without waiting. |

## In progress

### Phase 15 — One Click Trading guard: wired, **not yet verified on a real terminal**

The guard refuses any order carrying a stop or target unless
`terminal_info().trade_allowed is True`. It is unit-tested with doubles only.

Remaining, in order (all need a Windows machine, a running **demo** terminal and a
person watching):

1. Read `terminal_info().trade_allowed` with a read-only probe, with Algo Trading **off**
   and then **on**, and record both readings. Confirm the flag actually tracks the
   state that produced the stop-less fills; if it does not, the guard's premise is wrong
   and must be revisited before anything else.
2. Join `execution_mode` to the `check_control_ids` seam so the terminal is read once,
   from one place.
3. With Algo Trading still off, send a correctly-formed demo order through
   `build_live`. **Expected result: a refusal, and no click.** That refusal is the proof.
4. Only then, and only as the operator's own deliberate act, enable Algo Trading and
   confirm a demo order carries its volume, stop and target. Nothing in this project
   may toggle that setting.

## Next

### Phase 17 — close out Phase 15 (above) and record the evidence

Exit criteria: probe readings recorded under `docs/measurements/`, a test that reads
that record the way `test_control_id_evidence.py` reads the control-id one, and the
README's "real-terminal check pending" line removed.

## Doable without a terminal

None of these touches, or needs, a live terminal.

* **Run `tests/integration` against the real `albrooks` engine** (nine tests that
  have not run in several phases). Needs `ALBROOKS_PATH` and `scripts/setup.*`.
* **Run `requires_auto_trade` tests** (84 skips on a clean machine include them) on a
  machine with the private checkout, and keep the skip count recorded in `HANDOFF.md`.
* **Rewrite git history if the repository is or will become public.** Earlier commits
  contain a demo login, an account-holder name and a Windows profile name that were
  removed from the working tree in Phase 16 but remain in history.
* **Shrink `HANDOFF.md`.** It is ~190 KB, mostly per-phase narrative. Move closed
  phases into `docs/history/` and keep the hand-off to current status, known issues and
  the protocol. Do this deliberately and in its own commit; it is the file future
  agents trust first.
* **CI.** The suite now passes on a clean Linux machine, so `ruff`, `mypy` and `pytest`
  can run in GitHub Actions without either private repository. Not yet set up.
* **Cover the remaining gaps** behind the 93% figure (CLI error paths are the largest).

## Later (needs a decision, not just work)

* **Whether the One Click guard belongs in this project or upstream.** It sits
  immediately before `workflow.execute`, which is right for this codebase, but
  `auto-trade` owns the click and knows which panel it uses. A refusal here is honest and
  cheap; a rule about a terminal capability arguably belongs beside the code that uses it.
* **A runtime guard on `build_live`.** `build_bridge` is the safe path and `build_live`
  is not, and that distinction is carried only by which function a caller reaches for.
  If execution is ever turned on in earnest, review whether that is still the shape you
  want.
* **Upstream `AuditLogger` file-handle leak** (blocks log rotation in long-running
  commands). Upstream's code; record or fix there.

## Deliberately out of scope

* Detecting setups, choosing direction, or having any opinion about the market. The
  bridge translates and refuses; it does not trade ideas.
* Any command that places an order from the CLI.
* Toggling terminal settings (Algo Trading, One Click Trading) on the operator's behalf.
* A second idempotency store. The execution project's ledger is the only one.
* Trading a non-demo account.

## Invariants no roadmap item may relax

See the nine rules in [`README.md`](README.md#the-rules-this-project-holds-itself-to)
and `docs/architecture.md` §9. In particular: no valid stop ⇒ no trade; a volume below
the broker minimum is a refusal, never a floor-up; `UNKNOWN` is never auto-retried;
the default configuration cannot execute.
