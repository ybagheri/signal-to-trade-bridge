> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

### Resolved

**Q1. `signal_id` composition — settled in Phase 2, to be revisited in Phase 9.**

SHA-256 over `(symbol, timeframe, bar_index, bar_time, action, direction,
setup_id)`. **The prices are excluded, and the bar is the unit of identity.**

The reasoning: re-delivery of the same reading must be recognised, a
recomputation after new bars close must be a new trade, and a recomputation over
the *same* bar that nudges a stop by one tick is the same reading with a rounding
difference. The bar satisfies the first two; excluding the prices satisfies the
third. Including them would let a flapping stop produce a fresh position every
cycle.

**The accepted trade-off:** if the engine materially revises a stop on the same
closed bar, the bridge treats the revision as the same signal and suppresses it.
That is a real limitation, stated rather than hidden. Phase 9 must check it
against real engine output, and if a material revision turns out to be common the
key will need a revision counter rather than a price hash.

**The key format is `stb-<32 hex>`, and the field order is part of the
contract.** Changing the order changes every key, which would make every
previously-recorded signal look new and re-enable duplicates against a live
ledger. A change needs a migration note here, not just a test update.

