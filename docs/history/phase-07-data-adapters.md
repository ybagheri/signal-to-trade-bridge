> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## What Phase 7 Built

```
adapters/mt5/
  __init__.py    the package docstring: the pattern, and what is not here yet
  bindings.py    MT5Bindings, MT5AccountInfo, MT5SymbolInfo   three Protocols
                 MT5Unavailable, load_bindings()   the one import site
  account.py     MT5AccountProvider    AccountProvider
  symbols.py     MT5SymbolSpecProvider SymbolSpecProvider
  positions.py   MT5PositionReader     the indicator's snapshot, on disk
tests/unit/
  test_mt5_data_adapter.py     48 tests, no terminal and no bindings imported
  test_mt5_position_reader.py  48 tests, incl. 3 opt-in against the live terminal
```

**Phase 7 is complete, and it was two halves.** The MT5 data adapter reads real
account facts; the execution adapter wraps `auto-trade`'s real workflow. The second
half was written against the source rather than against a transcription, because the
maintainer supplied the checkout at `D:\Projects\auto-trade` and it turned out to be
installed editable — so it is exercised against the real thing on every suite run.

### The gap Phase 7 opened, and then closed

Phase 7 first shipped `open_positions` hard-coded to zero, and documented why:
`account_info()` has no position count, so a configured `BRIDGE_MAX_OPEN_POSITIONS`
gate saw zero, admitted the trade, and the limit did nothing. **A gate that admits
a trade is the wrong direction to be wrong in.**

The maintainer then supplied the terminal path, the data folder, the fact that an
`AutoTradePositionReader` indicator was attached to EURUSD, and the `auto-trade`
checkout. Between them those closed the gap from the far end: **the terminal
already publishes the answer**, as JSON on disk, and the indicator's source is in
the upstream repository.

`adapters/mt5/positions.py` reads it. The contract below was **observed, not
designed** — from a live terminal and from `AutoTradePositionReader.mq5`:

```
<data path>\MQL5\Files\auto_trade_positions_a.json
<data path>\MQL5\Files\auto_trade_positions_b.json
```

**Three things this phase first got wrong by reasoning, and the source corrected.**

1. **The double buffer is not two files.** The writer picks `a` or `b` by
   `sequence % 2` and then **deletes the other one**, so in normal operation
   exactly *one* file exists. The two-file state appears only in the window
   between truncating the target and deleting its predecessor — and that window is
   the hazard the design contains, because `FileOpen` for writing truncates. So
   the guard is **parse failure** and the recovery is the other file. The first
   draft of this module described the protocol as "read both, take the higher
   sequence", which is right as a *reader strategy* and wrong as an explanation.
2. **`complete` is never false.** The indicator writes the literal `true`. The
   check is defensive against a future writer or a hand-edited file — the same
   reasoning `auto-trade`'s own reader gives — and the first draft implied it was
   a state a reader would meet.
3. **The per-position shape was a guess; now it is not.** `positions` was empty on
   this account, so the entry fields were unknowable, and guessing them would have
   produced a count that looked right and described the wrong thing. The writer and
   a captured real entry both give them: `ticket`, `symbol`, `type` (`BUY`/`SELL`),
   `volume`, `price_open`, `sl`, `tp`, `profit`, `magic`, `opened_at`. Only the
   first four are modelled — the four `auto-trade`'s verifier matches on — with the
   rest kept in `raw` and deliberately absent from the model, so nothing can come
   to depend on a field nobody has thought about.

**The 30-second staleness limit was arrived at independently and matches.** The
indicator writes once per second, so thirty seconds is thirty consecutive missed
writes; `MT5FilePositionSnapshotProvider` uses the same figure the same way. They
must agree or the same terminal looks alive to one reader and dead to the other,
so a test asserts the two constants are equal.

**One cross-check was added because the count could otherwise be confidently
wrong.** The snapshot names the account it was written for, and the account
provider now compares it against `account_info().login`. Two accounts on one
machine -- a stale data folder from another login, or two terminals sharing one
data path -- is ordinary, and the failure it produces is the worst kind: another
account's *flat* book is a zero on the concurrency gate, arriving through the
same code path as a computed answer. **This is the one reader outcome that raises
rather than falling back**, because "not known" and "known to be someone else's"
are different facts and only the first deserves a fallback.

**What remains open:** an unreadable snapshot still returns zero rather than
refusing, because `AccountBalance` cannot be built without a count and refusing
would make an unreadable *indicator* indistinguishable from an unreachable
*terminal* — two faults with different remedies. **Until that changes, an unset
`BRIDGE_MAX_OPEN_POSITIONS` remains the only correct configuration**, since it is
the only setting for which the zero cannot admit a trade.

### A cross-project security contract, found and pinned

`auto-trade`'s `TradeSignal` validates its id against
`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` because the id *"is used as a file name and
must not be able to name a path"*. `../../x` fails it.

That makes it a **security control on our side**: the execution project writes a
file named after the id the bridge supplies, so an id containing a path separator
would let a signal decide where it writes. Phase 2 chose `stb-<32 hex>`, which
satisfies the rule, and **nothing tested that it still did**. It is now asserted
in `test_signal_identity.py`, the pattern copied rather than imported because
`auto_trade` is private and may not be installed.

### The pattern, and why it makes this phase possible here

The bindings module is injected, so every branch is testable without MetaTrader 5
and no test file imports `MetaTrader5` — and the whole file runs on a machine that
does not have the bindings installed.

### Design decisions Phase 7 made

- **`import MetaTrader5` lives in one function,** `load_bindings`, called lazily.
  A test asserts it appears **exactly once** in the package and **not at module
  level** — a stray module-level import would pass every behavioural test and break
  the ones in files that do not inject the module.
- **The subset used is a `Protocol`,** not `Any`. The bindings ship no type
  information, so an untyped import would leave the boundary that converts
  untyped data into typed domain objects itself untyped.
- **The terminal path is a parameter,** never a constant, so no machine's path is
  committed. `MT5PositionReader` refuses to be constructed without a data path for
  the same reason — a default here would be exactly the `AUTO_TRADE_DATA_PATH`
  mistake the Phase 0 audit recorded, and that key's default still points at a
  *different machine's* terminal hash.
- **Nothing launches the terminal.** A test walks the AST and fails on any
  `launch` attribute. `initialize` connects; `launch` starts a process, and a
  process that launches a trading terminal is a process that can launch it by
  accident — and one that launches it without a login is worse.
- **A missing fact raises. It never falls back.** An unreachable terminal, an
  account that answers `None`, an unknown symbol, an unreadable snapshot — all
  raise `MT5Unavailable`.
- **`MT5Unavailable` is a `RuntimeError`, not a domain error.** By the time it is
  raised the bridge is outside the domain: its error vocabulary describes *trading
  refusals*, and "the terminal is not running" is a fact about the machine, not a
  verdict on a trade.
- **Floats become exact decimals via `Decimal(str(value))`,** never
  `Decimal(value)`. The second form copies the binary double's exact value, so a
  `tick_size` of `0.00001` arrives as `0.0000100000000000000002092251` and every
  tick count from it is slightly wrong in a way that looks entirely right.
- **JSON floats are parsed straight to `Decimal`** via `parse_float`. Going through
  `float` would round-trip `volume` through a binary double, and the execution
  verifier matches on exact `Decimal` volume equality — so drift there fails the
  verification of a trade that succeeded.
- **A missing field raises and names itself** rather than becoming zero. A zero
  tick size would be caught downstream as a *sizing* refusal, which points an
  operator at the arithmetic instead of at the terminal that failed to answer.
- **A malformed position entry refuses the whole snapshot** rather than being
  skipped. Skipping under-counts, and an under-count admits a trade the concurrency
  gate should have refused.
- **An empty `currency_profit` is passed through, not raised.** The deliberate
  asymmetry: an unstated profit currency is a fact the *domain* already refuses on,
  so the adapter reports it and stops. Refusing here too would be right for the
  wrong reason, and would move a policy decision into a converter.
- **No cache.** A specification does not change within a session, but a cache is a
  cache that can go stale across a broker changing a contract mid-session, and a
  re-read costs one local IPC call.
- **The two snapshot file names are written out, not globbed.** A glob would
  silently start reading a third file if the upstream project ever adds one, and a
  reader that picks up an unexpected file is a reader nobody reasoned about.

