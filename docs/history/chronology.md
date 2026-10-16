> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Chronological History

- **Phase 0** — audited both upstream repositories by reading their source, not
  their documentation alone. Established that position sizing, account data and
  symbol specification exist in neither, that `auto-trade` is a UI-automation
  bridge rather than an MT5 bindings client, and that `albrooks` emits a dict
  rather than a signal object. Wrote `docs/architecture.md`,
  `docs/integration.md`, `README.md`, `README_FA.md`, `.gitignore` and
  `.gitattributes`. Committed and pushed as `68c8b87`, then `19e4888`.

- **Phase 1** — built the foundation. `pyproject.toml` (setuptools, src layout,
  Python ≥3.11, ruff, mypy, pytest, the `mt5` marker). The domain layer: eleven
  frozen value objects, six enums, a 39-code `RejectionReason`, and twelve
  exception classes under two roots. Seven ports. `BridgeConfig` with 16
  `BRIDGE_*` settings that default to safe and fail loudly. Structured logging
  with a 13-event vocabulary and substring-based redaction. 126 tests, including
  a domain-isolation test that walks the AST. Setup scripts for three platforms
  with no committed drive letter, `docs/setup.md`, and `LICENSE`.

  Three real bugs were found by the tests and fixed, rather than the tests being
  adjusted to pass:
  - `config_from_env` used `bar_count=_int(...) or 300`, so a configured `0` was
    falsy and silently replaced by the default. The `or` idiom hid the very
    value validation was supposed to catch. Now `_require_int`.
  - `SymbolSpec.clamp_volume` documented that it would never raise a volume to
    the broker minimum, but implemented `min(max(v, 0), max)` — which returned a
    sub-minimum volume unchanged. That looks like a usable volume, and a caller
    checking only "is it in range" would pass it to the broker. Now clamps
    sub-minimum to zero, which is unambiguously invalid.
  - The `infrastructure/logging/__init__.py` re-exported event names as module
    constants, but the names are members of a single `Event` enum. The import
    failed at module load.

  One test was also wrong rather than the code, and was corrected: it built a
  modified frozen+slotted model with `{**intent.__dict__}`, which cannot work
  because such a model has no `__dict__`. Now uses `dataclasses.replace`, which
  also re-runs validation.

- **Phase 2** — built the first adapter. `adapters/albrooks/` with
  `identity.py` (deterministic `signal_id`), `mapper.py` (the anti-corruption
  layer), and `source.py` (`AlBrooksSignalSource`). `Signal.entry` became
  optional, because the engine returns `plan: None` on every abstention and
  requiring one would have forced the adapter to substitute the last close for an
  entry. The abstention path was split out so it needs no plan at all. Added
  `tests/stubs.py` built from the audit's verbatim quotes, 22 port-contract
  tests, 9 integration tests against the real `Analyzer`, and
  `docs/signal-flow.md`. 245 tests, 90% coverage.

  Four bugs found and fixed:
  - `Signal.entry` was mandatory in Phase 1, which made the commonest outcome in
    the system — "the engine found nothing to trade" — unmappable. It returned
    `None` with an empty reason.
  - An abstention was reported as `SIGNAL_DIRECTION_UNKNOWN` instead of being
    mapped, because the shared builder required an entry.
  - `source_direction` read the numeric `decision["direction"]` for abstentions,
    so a stray non-zero value on a `WAIT` would have produced a tradable signal.
    Now an abstention is `FLAT` unconditionally and the field is not read.
  - A missing `action` key was reported as an unknown action, sending an
    operator to look for a new enum member instead of at the upstream contract.

  One test was wrong rather than the code: it asserted that two float timestamps
  differing at the 16th digit produce different keys, but Python silently rounds
  `1727740800.0000001` to `1727740800.0`, so it would have passed whether or not
  the renderer worked. It now asserts the fixture values are distinct doubles
  *before* checking the keys.

  Settled the architecture audit's open question about `signal_id`: the prices
  are excluded and the bar is the unit of identity. Reasoning and the accepted
  trade-off are recorded above, and Phase 9 must revisit it against real engine
  output.

- **Phase 3** — gave the domain its policies. `domain/resolution.py` (one
  `Resolution[T]` for every two-outcome step), `domain/stops.py` (the stop
  policy, with the no-invented-stop rule enforced by an AST test),
  `domain/take_profit.py` (four policies, a fallback never silent), and
  `domain/validation.py` (four validation layers). Moved the structural-basis
  vocabularies out of the adapter and into the domain, because deciding what
  counts as a defensible stop is a trading policy. Added
  `docs/risk-management.md`. 432 tests, 94% coverage, the four new files at 100%.

  Three bugs found and fixed:
  - A missing stop escaped as a `ValueError` instead of a reason code.
    `resolve_stop` passed a zero price straight to `StopLoss`, whose own
    validation raises — so the commonest outcome in the system arrived as an
    exception, and a trading loop that did not catch it would crash on a quiet
    market.
  - `Resolution` could not express "succeeded with no value". Found by the
    `TakeProfitSource.NONE` tests, the only policy that resolves to nothing.
  - A wrong-side stop discarded its computed distance on the way out, because the
    distance was calculated after the side check — and the distance is the first
    thing anyone looks at when asking why a stop was rejected.

  Two tests were wrong rather than the code, both for the same reason: an
  assertion written against a value Python silently rounds. One expected five
  distinct refusal codes and got four, because a missing stop and a zero stop are
  the same fault by design. The other compared a `Decimal` distance as a string,
  but `str(Decimal)` switches to exponent notation below `1e-6` — `1E-11`, not
  `0.00000000001`.

- **Phase 4** — built the sizing the whole project exists for. `domain/risk.py`
  (the risk budget, with the no-invented-balance rule enforced by an AST test
  exactly as the stop rule is, plus the currency-coherence check that has to run
  before any division can mean anything), `domain/sizing.py` (the tick-value
  sizer, the broker constraints, and three independent guards on the floor-up
  rule), `RiskBudget` as the twelfth value object, `application/risk_service.py`
  to obtain the account and symbol facts through the ports the domain may not
  import, and `adapters/fake/` with two provider fakes and three named symbol
  specifications. `docs/risk-management.md` rewritten around steps 7 and 8. 555
  tests, 95% coverage, the five new files at 100%.

  One real bug found and fixed, and it was in Phase 1's code:
  `SymbolSpec.conservative_tick_value` was `min(tick_value_profit,
  tick_value_loss)`. Because volume is inversely proportional to the tick value,
  the *smaller* value produces the *larger* position, so `min` was the least
  conservative choice available and under-sized every position on an instrument
  whose two values differ — by 2× in the worked example. Now `max`. The two
  Phase 1 tests that pinned the old behaviour were corrected rather than deleted,
  with the arithmetic in the comment, because they encoded exactly the reasoning a
  future contributor would otherwise re-invert.

  Two tests were wrong rather than the code: several assertions compared a
  `Decimal` as a string, and `Decimal` arithmetic both preserves an exponent
  (`10000 × 0.5 / 100` is `50.0`, not `50`) and runs division to 28 significant
  digits, so `0.50 / 300` is a long tail of 6s. They now compare numerically.

  **Two unreachable guards were deleted rather than kept.** The sizer had a
  `risk_per_unit <= 0` branch that could not fire, because a positive distance, a
  positive tick size and a positive tick value cannot produce a zero product. An
  unreachable guard reads as though it handles a case it does not; the reasoning
  became a comment explaining why the division cannot fail. Two guards that
  *are* one edit away — an over-budget size, and a volume failing its own
  broker-constraint check — were kept, and each has a test that drives its branch
  by making the bad edit for real.

  `STOP_RESOLVED` and `TAKE_PROFIT_RESOLVED` are still **not** emitted, and this
  was a deliberate consequence rather than an oversight: Phase 4 established that
  the domain has no logger and must not acquire one, so the two Phase 3 events
  wait for Phase 6's `ProcessSignal`, which is the first place that can emit them.

- **Phase 5** — finished the reward:risk policy, and found it was not finished. A
  full audit of the chain from `BRIDGE_REWARD_RISK_RATIO` through configuration,
  `RiskParameters`, `resolve_take_profit`, `RiskBudget` and `TradeIntent` found
  that under the **default** policy the configured 1:1 was not a floor: nothing
  compared the signal's implied ratio against anything, so a structurally sound
  target at 0.2:1 was accepted silently. Added `BRIDGE_MINIMUM_REWARD_RISK_RATIO`
  as an **opt-in** floor (unset by default, so behaviour is unchanged), plus
  `achieved_ratio`, `canonical_ratio` and `signal_target_ratio`. 614 tests, 96%
  coverage, every file touched at 100%.

  The floor was escalated to the maintainer rather than decided here, because it
  changes which trades the bridge takes. Three options went on the table and
  **opt-in** was chosen; see Q6, which also records why the other two were
  rejected and why the question is left open rather than quietly settled.

  Four bugs found and fixed, three of them in code Phases 1 and 3 had written and
  called complete:
  - `config.py` used `risk_percent=_decimal(...) or Decimal("0.5")` and the same
    for the reward ratio. `Decimal("0")` is falsy, so `BRIDGE_RISK_PERCENT=0`
    started the bridge at **0.5%** and `BRIDGE_REWARD_RISK_RATIO=0` at **1:1**.
    Phase 1 had already identified this idiom as a bug and fixed it for
    `BAR_COUNT`; it was left in the two fields where it costs money.
  - `achieved_ratio` did not exist on the `TakeProfitSource.NONE` path, so indexing
    it raised `KeyError` on a resolution that had already succeeded.
  - `TradeIntent` required a `take_profit`, so a supported, documented, tested
    policy made the class unconstructible — and `to_dict()` divided by a take
    profit it assumed was there.
  - The ratio was computed in five places. Now one `achieved_ratio()`, with an AST
    test asserting `resolve_take_profit` performs no division of its own.

  Also removed: `_MIN_RATIO`, a constant whose docstring described a minimum-ratio
  clamp that did not exist and which nothing referenced. A dead guard is worse
  than no guard, because it reads as though it handles a case it does not.

- **Phase 6** — assembled the pipeline. `application/process_signal.py`:
  `ProcessSignal.process(signal) -> TradeDecision`, running every stage in order
  and stopping at the first refusal. `RiskService` gained a resolve half
  (`budget_for`, `size_for`) so the account and the specification are one snapshot
  per decision rather than two. `STOP_RESOLVED`, `TAKE_PROFIT_RESOLVED` and
  `TRADE_VALIDATED` are finally emitted — the first two had been reserved since
  Phase 1 and un-emitted for two phases, because Phase 4 established that the
  domain must not hold a logger. 665 tests, 96% coverage.

  No new bug in the domain, and that is worth noting: this phase found no defect
  in code the earlier phases wrote. It found the *absence of a connection*, which
  is the one class of problem only a phase that composes can find, and which five
  phases of individually-correct functions could not.

  One finding recorded rather than engineered around: **the geometry stage cannot
  refuse a signal that reaches it**, because every condition it checks was already
  checked by an earlier stage on the same objects. It is kept — the Phase 0
  rationale for the duplication still holds — and the honest position is written
  down and pinned by a test. `process_signal.py` is therefore at 99%, the one
  uncovered line being that refusal.

- **Phase 7** — both adapters, and they are the two halves of the same idea.

  **The data half**, `adapters/mt5/`: `bindings.py` (three Protocols,
  `MT5Unavailable`, and the project's single `import MetaTrader5`), `account.py`,
  `symbols.py` and `positions.py`. 92 tests against an injected bindings module: no
  terminal, no bindings import, and the whole suite runs on a machine that has
  neither.

  **The execution half**, `adapters/auto_trade/`: `bindings.py` (eight declared
  upstream names and the project's single `import auto_trade`), `executor.py` and
  `FakeTradeExecutor` in `adapters/fake/`. 50 tests, of which six run the **real**
  `ExecutionWorkflow`, `RiskEngine`, `JsonExecutionLedger` and
  `DryRunTerminalAdapter` -- because the maintainer's checkout is installed editable,
  so the adapter is verified against the thing it calls on every suite run rather
  than against a transcription of it.

  850 tests, 98% coverage, every new file at 100%.

  **One bug the tests caught in this phase's own code,** which is the argument for
  having written them: `load_bindings()` sat outside the `try` that turns a
  translation failure into an `UNKNOWN` result, so on a machine without the checkout
  a missing package raised out of `submit()` -- and an exception out of `submit()`
  loses the trade record, on exactly the situation where an operator most needs to
  see which signal did not go out.

  **The `open_positions` gap is closed, with a residual case.** It was always zero,
  because `account_info()` does not carry a position count — and a configured
  `BRIDGE_MAX_OPEN_POSITIONS` therefore admitted a trade it should have refused. The
  fix reads the terminal's own published snapshot
  (`MQL5\Files\auto_trade_positions_[ab].json`), written by the
  `AutoTradePositionReader` indicator and confirmed against that indicator's source.
  What remains: **an unreadable snapshot still yields zero**, because refusing would
  make an unreadable indicator indistinguishable from an unreachable terminal. So an
  unset `BRIDGE_MAX_OPEN_POSITIONS` is still the only correct configuration, and a
  set one is a gate that has a known fail-open path. That case is stated in the
  config docs rather than hidden.

---

- **Phase 8** — dry run reporting. `application/dry_run.py`, plus
  `adapters/auto_trade/preflight.py`. 39 tests.

  **The content of the phase is one disagreement made visible.** This bridge
  validates against its own rules and `auto-trade` validates against its own rules
  again, and the two disagree by default: its allow-list is `EURUSD,XAUUSD,YM` with a
  1.0 volume cap, while this project's come from the operator's configuration. **A
  signal on `GBPJPY` passes every check here and is refused downstream** — and the
  only place that used to become visible was a `REJECTED` result on a live account.
  The dry run now asks `auto-trade`'s own `RiskEngine`, which is a pure function of
  the request, and reports the answer as a blocker with the reason verbatim.

  **A prediction is marked as one.** `DownstreamVerdict` carries `evaluated`, because
  the real gates also depend on the account type, the kill switch, the ledger and the
  terminal window. With no engine wired it reports `evaluated=False` and **never**
  `accepted=True`; `accepted=True` with `evaluated=False` raises, because those two
  are indistinguishable downstream and only one of them is true.

  **`ExecutionRequest` had no producer.** The type the executor accepts was never
  constructed anywhere in `src/` before this phase — the whole mapping existed only as
  a hand-written test fixture. `build_execution_request` is that producer, and it
  copies every number rather than recomputing any: a dry run that re-derived the volume
  would be a second sizing pass, and two passes that disagree describe an order the
  bridge would never send.

  **A gap left open on purpose.** `ExecutionRequest.take_profit` is required while
  `TradeIntent`'s is optional, so `TakeProfitSource.NONE` — supported, documented,
  tested — cannot be expressed. Phase 9 fixes the type alongside the ledger.

  Still no `TradeExecutor` anywhere in the pipeline. The tests enforcing that check
  the AST rather than the source text, because all four forbidden names appear in
  `preflight.py`'s docstrings explaining why it may not use them.

  850 tests, 98% coverage, both new files at 100%.


