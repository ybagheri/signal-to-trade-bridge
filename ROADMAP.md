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

1. ~~Read `terminal_info().trade_allowed` with a read-only probe, with Algo Trading **off**
   and then **on**, and record both readings. Confirm the flag actually tracks the
   state that produced the stop-less fills; if it does not, the guard's premise is wrong
   and must be revisited before anything else.~~ **Answered 2026-10-15: the premise
   is wrong.** A live demo order (ticket 387457709) went out at the panel defaults
   with `trade_allowed is True`. The cause was never the trading mode: upstream wrote
   dialog fields with `set_edit_text`, which MT5 never commits (proven with read-only
   probes 2026-10-15: display updates instantly, MT5's own committed-state labels
   `10881`/`11138`/`11139` do not move). Fixed upstream — keystroke writes plus a
   commit-witness wait, with the witness ids joined to the `terminal-check` probe.
   The guard stays as a cheap pre-click refusal, not the primary protection.
2. Join `execution_mode` to the `check_control_ids` seam so the terminal is read once,
   from one place.
3. With Algo Trading still off, send a correctly-formed demo order through
   `build_live`. **Expected result: a refusal, and no click.** That refusal is the proof.
4. Only then, and only as the operator's own deliberate act, enable Algo Trading and
   confirm a demo order carries its volume, stop and target. Nothing in this project
   may toggle that setting.

## Next

### Phase 17 — ~~close out Phase 15 (above) and record the evidence~~ done 2026-10-15

Probe readings recorded under `docs/measurements/` (`control_ids.json` now covers
the three commit witnesses; `field_commit.json` records the SetValue-never-commits
finding and the live fill of ticket 389589852), `test_field_commit_evidence.py`
reads the new record the way `test_control_id_evidence.py` reads the control-id
one, and both READMEs no longer say "real-terminal check pending".

## Doable without a terminal

None of these touches, or needs, a live terminal.

* ~~**Run `tests/integration` against the real `albrooks` engine**~~ **done
  2026-10-15: all 9 pass** against the installed engine
  (`D:\Projects\al-brooks-price-action-engine`), no `ALBROOKS_PATH` needed.
* **Run `requires_auto_trade` tests** (84 skips on a clean machine include them) on a
  machine with the private checkout, and keep the skip count recorded in `HANDOFF.md`.
  2026-10-15: on this machine (both private checkouts installed) the suite is
  **1326 passed, 6 skipped** -- the 6 skips are platform/installation facts with
  stated reasons (file locking on Windows, an installed `MetaTrader5`, an unset
  data path), not missing coverage.
* ~~**Rewrite git history.**~~ **done 2026-10-16 with `git filter-repo`.**
  `BazikadeStore` → `Trader`, the account-holder name → `Demo Holder`, demo
  logins → `5000000x` placeholders; author identity untouched. Verified by
  scanning every blob in the object database (zero hits), full suite green
  afterwards (1385 passed), then force-pushed. A local mirror backup was kept
  at the time in case the rewrite needed reverting.
* **Shrink `HANDOFF.md`.** It is ~190 KB, mostly per-phase narrative. Move closed
  phases into `docs/history/` and keep the hand-off to current status, known issues and
  the protocol. Do this deliberately and in its own commit; it is the file future
  agents trust first.
* ~~**CI.**~~ **done 2026-10-15: `.github/workflows/ci.yml`** runs the full gate
  (`ruff check`, `ruff format --check`, `mypy`, `pytest`, domain isolation) on
  Python 3.11 + 3.12, Ubuntu, with neither private repository installed.
  Verified by replaying the gate in a throwaway venv here: 1263 passed,
  86 skipped (every skip carries its reason).
* ~~**Cover the remaining gaps** behind the 93% figure (CLI error paths are the largest)~~
  **done 2026-10-15 for the CLI and the domain: `cli/main.py` and `domain/models.py`
  are at 100%** (`tests/unit/test_cli_trade_errors.py` pins the trade refusals, the
  unreadable/unusable signal paths, the unwired-pipeline refusal, and both report
  blocks; `tests/unit/test_domain_validation_gaps.py` pins every constructor guard,
  and a duplicated validation block found on the way was deleted, not tested).
  **done 2026-10-16 for the rest: `test_residual_branches.py` pins the albrooks,
  executor, config, logging and live refusals; a dead FLAT-direction branch was
  deleted. Suite total 100%.**
* ~~**Shrink `HANDOFF.md`.**~~ **done 2026-10-16: per-phase narrative moved
  verbatim to `docs/history/` (22 files, every one of the 3382 lines
  accounted for); `HANDOFF.md` is the live state only.**

## Later (decided 2026-10-16; the code follows the decision)

* ~~**Whether the One Click guard belongs in this project or upstream.**~~
  **Decided: it stays here.** The falsification settled the premise question,
  not the placement one. Upstream now commits fields properly and the verifier
  checks protection, but neither of those refuses an order on a terminal that
  is *genuinely* in one-click mode -- and only the bridge states the rule in
  its own terms ("never send levels the terminal cannot carry"). It is a cheap
  pre-click refusal with unit tests; moving it upstream would trade a tested
  local rule for a second opinion in a codebase that has no such concept.
* ~~**A runtime guard on `build_live`.**~~ **Decided and implemented: keep the
  shape, audit the arming.** The env-var gates, the demo check, the ledger and
  the kill switch *are* runtime guards, evaluated at assembly; the per-invocation
  `--confirm-demo` covers the CLI. What was missing was not a sixth lock but a
  record: `build_live` now writes one `assembled` audit event naming the
  execution configuration, so "when was this machine armed" is answerable from
  the trail rather than from memory. Pinned by
  `test_the_assembly_itself_is_audited`.
* ~~**Upstream `AuditLogger` file-handle leak.**~~ **Fixed upstream:
  `AuditLogger.close()`** releases the file handlers it installed (idempotent,
  leaves non-file handlers alone), with tests proving the handle is gone by
  renaming the file -- the operation that raises `PermissionError` while it is
  held. Long-running commands call it on shutdown; one-shot commands never
  needed it. (The bridge holds only the bound `record` method, so there was
  nothing to close on this side.)

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
