> Moved verbatim from `HANDOFF.md` on 2026-10-16 (Phase 18: handoff shrink). Narrative history; the live handoff lives in `HANDOFF.md`.

## Phase 12 — CLI, public API, documentation

**What shipped.** A runnable command, an importable public surface, and a setup guide
that was written by running the thing rather than by describing it.

| Piece | Where |
|---|---|
| `main()` returning an exit code, never raising `SystemExit` | `src/signal_to_trade_bridge/cli/main.py` |
| `doctor`, `check`, `signal`, `config` | same |
| Public API, upstream internals deliberately not exported | `src/signal_to_trade_bridge/__init__.py` |
| Console entry point | `pyproject.toml`, `[project.scripts]` |
| 35 tests, mostly the exit-code contract | `tests/unit/test_cli.py` |
| Install-to-first-command guide | `docs/setup.md` §5, §8 |

### The exit codes are the contract

```
0  it did what it was asked (a DRY_RUN counts)
1  a refusal — the bridge worked and declined
2  a fault — unreachable terminal, unreadable file, refused config
3  UNKNOWN — must not be retried automatically
```

**`3` exists so a caller's retry logic has something to key on.** A caller told
"refused" would reasonably resend, and a resend of an unknown-outcome signal may
open a second position. `EXIT_UNKNOWN != EXIT_REFUSED` is asserted in the suite
because that inequality is the entire point.

### What running the CLI found — four real defects

Every one of these was invisible to the unit tests that already existed, and every
one was found by executing the command or by writing a test that used the real
model. This is the argument for the phase.

0. **The public API was unusable from outside the package.** `build_bridge` takes a
   `BridgeConfig`, and `BridgeConfig` was not exported — so the one documented way
   to call the library was "import a name the package does not export". Twelve
   phases had shipped before anyone noticed, because every test reached past the
   package root into a private module to get what it needed, and a test that
   reaches past the API cannot notice the API is missing something. There are now
   three tests that cannot: one asserting every name in `__all__` exists, one
   building a config from the public surface, and one running `python -c` with
   `import signal_to_trade_bridge as stb` and nothing else.

1. **`--side SELL` silently produced a BUY.** The example builder branched on
   `args.symbol` where it meant `args.side`, and `args.symbol` is never `"SELL"`.
   The `else` branch was therefore the only reachable path, so the short side of
   the example had never been produced. A test asserting the mirrored stop landed
   on it immediately.

2. **The emitted signal used the wrong key for its identity.** It wrote
   `signal_id`. `auto_trade.domain.models.TradeSignal.from_dict` requires `id` —
   the *constructor* takes `signal_id` and the *deserialiser* reads `id`, an
   asymmetry in upstream that is easy to miss. A file emitted by `signal` would
   have been rejected by the project it exists to feed. The payload now carries
   both, and `_signal_from` accepts either.

3. **A Windows redirect produced a file `check` refused.** PowerShell redirects
   through UTF-16, so `signal > sig.json` wrote something a strict UTF-8 reader
   rejected with `'utf-8' codec can't decode byte 0xff in position 0` — the
   project's own documented command, failing on the platform most users are on.
   `_read_text_any` now tries UTF-8-sig, UTF-16, UTF-8, cp1252, in that order. The
   last one is the reason the chain can still fail: `latin-1` would decode any byte
   sequence and turn corrupt input into nonsense instead of a refusal.

4. **`config` could not print its own configuration.** `BridgeConfig.risk` is a
   `RiskParameters`, and `json.dumps` raises on it. Two further issues sat behind
   that one: a `Decimal` must become a **string** (writing `0.00500` as `0.005`
   would make the printed configuration differ from the effective one in the
   digits that matter), and a `Path` must **not** be escaped before `json.dumps`
   sees it, or every separator is escaped twice and the output no longer round-trips.

### Two smaller things

- `prog="stb"` in argparse, while the installed command is
  `signal-to-trade-bridge`. The usage line named a command the user had not typed,
  and it is the line people paste into scripts. Now derived from `argv[0]`.
- The `config` help said "redacted". Nothing is redacted, **deliberately**: a
  heuristic that guesses which values look like secrets either over-redacts a risk
  percentage or under-redacts a token, and the second is the failure that matters.
  The help now says what the command actually does.

### What the example signal deliberately does *not* contain

No `volume`, no `price`. Those are the bridge's to compute — the volume from the
account balance and the symbol's contract, the entry from the signal's own
resolution. A template carrying a plausible `0.10` would be a number nobody
computed, accepted by `auto-trade` and refused by this bridge with no explanation
of which number was invented. The `check` output shows the real computed volume
(`1.66` against a $10,000 account at 0.5%).

### Two pre-existing tests were environment-dependent

Not a Phase 12 defect, but found by running the full suite **twice** — once in a
clean shell and once with the real terminal paths exported, which is how a Phase 11
operator actually runs it.

`clean_environment` snapshots and restores `os.environ` but **never clears it**, and
that is deliberate (so it cannot delete a developer's real settings mid-session).
The consequence is that a test asserting something about the *defaults* runs
against the ambient environment. `test_no_machine_specific_path_is_baked_in` —
"no machine path is hard-coded" — passed on a machine with nothing configured and
**failed on the machine that configures a terminal**, which is the worst possible
pairing. It now clears the two variables it is about.

My own new test had the same flaw and was caught by the same double run: it counted
escaped backslashes across the whole `config` document, so it also counted
`mt5_terminal_path` and `mt5_data_path`. It now compares against `json.dumps` of the
expected value, which is exact and cannot be moved by an unrelated setting.

**Both the test and the fixture were right in isolation and wrong in combination.**
Run the suite in a configured shell before trusting a green run in a clean one.

### Test infrastructure changed

`StubBindings` moved to `tests/conftest.py` as a shared double, with a
`bridge_factory` fixture. `tests/unit/test_composition.py` had its own copy; three
modules now need it, and a copy per module is three places for a wrong MT5 field
name to hide. `pythonpath` gained `"tests"` so a test module can import the double
by name.

`build_bridge()` **without** bindings refuses on a machine with no terminal — which
is correct production behaviour and useless as a test default, hence the factory.
A CLI test that reached for `build_bridge(BridgeConfig())` and got `MT5Unavailable`
was hitting the right behaviour in the wrong place.

### Still true after Phase 12

- **No command can place an order.** `build_live()` refuses on the build mismatch,
  and the CLI reaches no order path at all. A test asserts the CLI module contains
  no call to `build_live`.
- **`control ids REFUSED`** on `doctor` on this machine: measured on build 6184,
  terminal is 6230, a gap of 46 builds. Unchanged by this phase and unfixable from
  a script.
- **`albrooks` is still not installed**, so the engine-side integration tests
  remain unrunnable here.
- Phase 11's live suite is still opt-in behind `BRIDGE_ALLOW_MT5_TESTS=1`.

---

---

