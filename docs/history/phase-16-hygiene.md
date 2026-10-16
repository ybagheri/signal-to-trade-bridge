> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Phase 16 — Hygiene, portability, and finishing the Phase 15 guard

This session started from the Phase 15 hand-off ("stopped mid-investigation") and a
repository whose test suite, on a clean machine, was **27 failed and 6 errors**. It
ended at **1184 passed, 84 skipped, 0 failed**. Nothing here placed an order or opened
a terminal.

### What was wrong, and what was done

1. **The suite was not portable (the 33 failures).** Three causes, all of them tests
   asserting things that only hold on the author's machine:
   * **Tests needing the private `auto_trade` package ran unconditionally.** They are
     now marked `@pytest.mark.requires_auto_trade` (registered in `pyproject.toml`) and
     skipped, with a reason, by `pytest_collection_modifyitems` in `tests/conftest.py`
     when the package is absent. `tests/unit/test_live_assembly.py` is marked as a whole
     module: it patches `auto_trade.*` dotted paths and builds the real
     `TerminalProfile`, so its docstring's old claim that it needs no private package was
     wrong and has been corrected.
   * **`build_bridge` could not be built without `auto_trade`**, because it refuses when
     the ledger is unavailable. `build_bridge` gained an optional `ledger=` argument (an
     injected ledger is trusted, exactly as an injected binding set already was; omitting
     it behaves as before). `tests/stubs.InMemoryLedger` satisfies the port, and the
     `bridge_factory` fixture uses it, so the CLI-report tests no longer depend on the
     private package. *This proves the bridge is wired correctly, not that upstream's
     ledger is durable — `TestAgainstTheRealLedger` still covers that where the package
     exists.*
   * **Tests pointed at the author's real terminal.** They now use a synthetic terminal:
     `tests/terminal_fixtures.py` (shared helpers) and the `fake_terminal` fixture. The
     two assertions that are only meaningful against a real install
     (`6230` read from the real executable and snapshot) are gated by
     `@needs_real_terminal`, driven by `BRIDGE_TEST_*` variables (see `.env.example`).
2. **Personal data was committed.** A demo account login, the account holder's display
   name, a Windows profile name, install paths and a terminal-instance hash appeared in
   source, tests, docs and this file. All were replaced with neutral placeholders; the
   live tests, and `scripts/control_ids.py`, now read their machine-specific values from
   the environment and refuse or skip with a message when unset. **Git history still
   contains the originals.** If this repository is, or will become, public, rewrite
   history (`git filter-repo`) and consider the demo login and profile name disclosed.
3. **The Phase 15 guard was inert, and is now wired.** `AutoTradeExecutor` takes a
   separate `mt5_bindings` parameter (typed by the new `TradeModeSource` protocol in
   `adapters/mt5/execution_mode.py`); `live.py` passes the execution project's bindings
   and the terminal's bindings **by name** to `_finish`. `_looks_like_mt5_bindings` was
   deleted. Five new tests (`TestTheOneClickGuard`) assert an order with a stop is
   refused *before* the workflow is reached, that an unreadable terminal refuses, and
   that the two binding sets are distinct parameters.
4. **The guard's mode test was reading the wrong field.** `is_one_click_trading` treated
   `account_info().trade_mode == 0` as "trading disabled". In MetaTrader 5 that field is
   `ENUM_ACCOUNT_TRADE_MODE` (0 DEMO, 1 CONTEST, 2 REAL): it is the account *type*. So
   every demo account read as one-click, and a real account with Algo Trading off read
   as fine. The decision now rests **only** on `terminal_info().trade_allowed`; anything
   other than an exact `True`, or an unreadable terminal, reads as one-click. Tests were
   rewritten to assert that `trade_mode` cannot change the answer either way.
   *(The Phase 15 text above, and its measured `trade_mode == 0`, record what the
   terminal reported; the interpretation of that number is what was wrong.)*
5. **Documentation was stale.** `README.md` and `README_FA.md` said "Phase 7 half done",
   quoted old test counts, described the One Click defect as "input fields not being
   written", and rendered every ✅ as a garbled glyph. All corrected; `ROADMAP.md` was
   added; `docs/setup.md` gained "what is skipped and why", the trading-mode gate and the
   re-measurement variables.

### What is still not verified (do not read this phase as closing Phase 15)

* **The guard has never run against a real terminal.** Its tests use doubles. In
  particular, whether `terminal_info().trade_allowed` is `False` in exactly the state
  that produced the stop-less fills — and `True` once Algo Trading is enabled — is an
  inference from one machine's behaviour, not a documented MT5 guarantee. Check it with
  a read-only probe before relying on it. Note also that MT5's One Click Trading is a
  separate setting (Tools → Options → Trade); the Phase 15 diagnosis ties the symptom to
  the Algo Trading button because that is what the screenshot showed. If the two ever
  diverge on a build, the guard must be revisited.
* **Phase 15 steps 4–6 remain**: join `execution_mode` to the `check_control_ids` seam,
  test against the real terminal, and only then re-send with Algo Trading still off,
  expecting a refusal.
* `tests/integration` (real `albrooks`) and `tests/live` have not run here.
* The upstream `AuditLogger` file-handle leak (Phase 14) is still recorded, not fixed.

### Verification (this session)

```
ruff check .            All checks passed
ruff format --check .   clean
mypy                    Success: no issues found in 48 source files
pytest                  1184 passed, 84 skipped, 93% coverage
```

---

