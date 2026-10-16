> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Pre-submit pause + order-comment policy (2026-10-08, after Phase 16)

Two execution/UI settings, both off by default; default behavior unchanged.

- `BRIDGE_PRE_SUBMIT_DELAY_ENABLED=false`, `MIN_MS=1000`, `MAX_MS=5000`: a fresh
  `randint(MIN, MAX)` pause per submission, taken in `AutoTradeExecutor.submit`
  after every bridge-side refusal (kill switch, translation, One Click mode
  check) and immediately before `workflow.execute` -- the first call that
  touches the terminal, so no UI state is held during the pause. Rolled
  duration logged as `PRE_SUBMIT_DELAY_APPLIED`. Sleeper and RNG injectable;
  the suite never sleeps. This is UI pacing, not anti-detection: no mouse
  movement, no fake input, no timing anywhere else.
- `BRIDGE_ORDER_COMMENT_ENABLED=false`: empty comment when off, the existing
  `default_comment` value (`stb SYMBOL LONG/SHORT setup-id`, <=64 chars) when
  on. Gated in `build_execution_request`, so all paths (live, dry-run,
  what-if) agree. Upstream already skips an empty comment (`window_manager.py`
  `if request.signal.comment`), so no upstream change was needed.
- `--what-if`/dry-run never sleep: the recorder (`FakeTradeExecutor`) rolls
  and records the pause it would have taken (`pre_submit_delays`), and the
  what-if output prints it. No order is submitted by tests or previews.
- Validation fails loudly: negative min, max < min, non-integers, and anything
  above one hour (`MAX_PRE_SUBMIT_DELAY_MS`) are refused at construction;
  `MIN_MS=0` is legitimate and survives env loading (no `or`-default).
- Note (2026-10-08, after the bridge change): `auto-trade` gained its own
  complementary intra-dialog pause (`AUTO_TRADE_PRE_SUBMIT_DELAY_*`,
  `3cbb6c2`), taken between `confirm_dialog_matches` and `click_final_control`.
  The two pauses are independent (separate env prefixes, separate rolls); with
  both enabled a real order waits twice. `--what-if` only exercises the bridge
  layer, so the intra-dialog pause never applies in previews. Documented in
  `docs/setup.md` ("Two pauses, two layers"). `E:\auto-trade` itself was never
  touched from this repo; verified read-only (`git status` clean there except
  its own commits).
- New files: `application/pre_submit.py`, `tests/unit/test_pre_submit.py`.
  Touched: `domain/models.py` (`PreSubmitDelay`), `configuration/config.py`,
  `adapters/auto_trade/executor.py`, `adapters/fake/executor.py`,
  `application/dry_run.py`, `application/process_signal.py`, `composition.py`,
  `cli/main.py`, `infrastructure/logging/events.py`, `.env.example`,
  `docs/setup.md`, `ROADMAP.md`. No strategy, risk, stop/TP, or safety-gate
  change. `E:\auto-trade` untouched: intra-dialog pacing would require an
  upstream change and was deliberately not made.