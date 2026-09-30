# Signal-to-Trade Bridge

🇬🇧 **English** (this file) · 🇮🇷 **[فارسی](README_FA.md)**

A safe, maintainable integration layer between an Al Brooks price-action analysis
engine and an automated trading execution system, with explicit risk management,
a default risk of **0.5% of account balance per trade**, and a default
**1:1 risk/reward**.

> **Status: Phase 0 complete — architecture audit.** No trading code exists yet.
> See [HANDOFF.md](HANDOFF.md) for exactly where the project stands and
> [docs/architecture.md](docs/architecture.md) for the design this audit produced.

---

## What this project is

Three projects, three jobs, no overlap:

```
Market data
    ↓
al-brooks-price-action-engine     ← owns the analysis: what is the market doing?
    ↓  a BUY / SELL / WAIT / NO_TRADE reading, with entry, stop and target prices
signal-to-trade-bridge           ← owns the translation: risk, size, validate
    ↓  a finished order with a computed volume, or a structured refusal
auto-trade                       ← owns the execution: place the order in MT5
    ↓
MetaTrader 5 → broker
```

The bridge is **not** another trading strategy. It does not detect setups, does
not choose a direction, and does not have an opinion about the market. It takes a
reading from one project, turns it into a correctly sized and validated order,
and hands it to another project. When it cannot do that safely, it refuses and
says why.

### The one thing this project had to build itself

Neither upstream project knows the account balance, and neither knows a symbol's
contract specification. `albrooks` treats a symbol as an opaque string.
`auto-trade` drives the MetaTrader 5 order dialog by clicking on it, so it can
only read what a human could see on screen — it has no account balance and no
tick value.

**Position sizing therefore lives here, and it is the substance of this
project.** A 0.5%-of-balance risk rule cannot be implemented by wiring two
existing components together, because the numbers it needs do not exist in
either.

---

## Current state

| Phase | Scope | Status |
|---|---|---|
| 0 | Repository audit, architecture, integration contracts | ✅ complete |
| 1 | Project foundation, packaging, configuration, test harness | ✅ complete |
| 2 | Al Brooks signal adapter | ✅ complete |
| 3 | Stop, take-profit and validation policies | ✅ complete |
| 4 | Risk management and position sizing | ✅ complete |
| 5 | 1:1 risk/reward take-profit policy | ✅ complete |
| 6 | Trade validation pipeline | ⬜ next |
| 7 | auto-trade execution adapter and MT5 account/symbol adapter | ⬜ |
| 8 | Dry run / simulation mode | ⬜ |
| 9 | Idempotency and duplicate protection | ⬜ |
| 10 | End-to-end integration | ⬜ |
| 11 | MT5 / demo validation (isolated, opt-in) | ⬜ |
| 12 | Documentation and developer experience | ⬜ |
| 13 | Final architecture review | ⬜ |

**614 tests passing, 96% coverage.** Lint, format, type check and the
domain-isolation check all clean. The suite runs **without the upstream projects
installed** — which is what makes it a safety net rather than a souvenir.

**What works today:** a signal is read from the price-action engine, normalised,
given a deterministic identity, validated, given a stop and a take profit under an
explicit policy, and then **sized**: the risk budget from the account balance and
the configured percentage, and the volume from the symbol's tick value and the
broker's volume constraints — tick-value based, so it is correct for gold, indices
and CFDs without a special case. Every trade that gets through reports the
reward:risk it *actually* has, not the one that was configured.

**What does not:** nothing connects a sized trade to an order. The signal-to-size
pipeline is Phase 6, and the account and symbol facts come from fakes until the
MT5 data adapter of Phase 7 exists. A volume below the broker's minimum is refused
rather than floored up — always.

---

## Quick start

```powershell
git clone git@github.com:ybagheri/signal-to-trade-bridge.git
cd signal-to-trade-bridge

# Point at your local checkouts of the two upstream projects. Any path works.
$env:ALBROOKS_PATH   = 'E:\al-brooks-price-action-engine'
$env:AUTO_TRADE_PATH = 'E:\auto-trade'

.\scripts\setup.ps1
Copy-Item .env.example .env

.\scripts\test.ps1
```

Full instructions, including Linux and macOS, are in
[docs/setup.md](docs/setup.md).

---

## Documentation

| Document | What it answers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | The design, and why each decision was made |
| [docs/integration.md](docs/integration.md) | The exact upstream APIs this bridge calls, quoted from source |
| [docs/signal-flow.md](docs/signal-flow.md) | What happens to a signal, step by step, and where it can stop |
| [docs/risk-management.md](docs/risk-management.md) | The stop, take-profit and validation policies, and the sizing boundary |
| [docs/setup.md](docs/setup.md) | How to install and run it on a laptop |
| [HANDOFF.md](HANDOFF.md) | Where the project stands, and how to continue it |

---

## The rules this project holds itself to

These are invariants, not aspirations. A change that breaks one is a change that
needs a very good reason and a test.

1. **No valid stop loss ⇒ no trade.** There is no code path that invents a stop.
2. **A volume below the broker minimum is a refusal, never a floor-up.** Rounding
   up to the minimum would place a position risking far more than the budget
   allows. This is the most dangerous line in any position sizer.
3. **Volume rounding to the broker's step is always down.** Rounding up could
   exceed the risk budget; rounding down leaves it slightly under.
4. **Every refusal carries a stable reason code**, not a sentence to be parsed.
5. **An `UNKNOWN` execution result is never automatically retried**, because a
   retry may open a second position.
6. **A dry run never reaches the executor**, so downstream configuration cannot
   cause an order during a simulation.
7. **The default configuration cannot execute.** Enabling live execution is a
   separate, explicit, documented act.
8. **A stop on the wrong side of the entry is refused locally**, because the
   execution layer does not check it and would let the broker refuse instead.
9. **The engine's evidence score is never described as a probability.** The
   upstream engine is explicit that it is not one, and a score mistaken for a
   win-rate is the most consequential misreading available in this design.

---

## Safety model

The bridge is fail-closed. Every stage of the decision pipeline can only refuse,
and each refusal is recorded with a reason:

```
Signal
  → validate signal                    → NO_TRADE
  → resolve stop loss                  → NO_TRADE   (refuse rather than invent one)
  → resolve take profit                → NO_TRADE
  → account balance + symbol spec      → NO_TRADE   (cannot size without facts)
  → risk amount + position size        → NO_TRADE
  → broker constraints                 → NO_TRADE
  → idempotency check                  → NO_TRADE   (already acted on)
  → dry run?  record the decision, stop
  → submit to the execution layer
```

When in doubt, the answer is no trade.

Three safety properties are inherited from the downstream project rather than
reimplemented, because they are already implemented well: a durable cross-process
kill switch, a demo-only policy, and a JSONL audit log of every execution attempt.
The bridge delegates to them instead of building a second version.

---

## Upstream projects

| Project | Role | Remote |
|---|---|---|
| `al-brooks-price-action-engine` | Analysis. Produces the reading. | `git@github.com:ybagheri/al-brooks-price-action-engine.git` |
| `auto-trade` | Execution. Places the order. | `git@github.com:ybagheri/auto-trade.git` |
| `signal-to-trade-bridge` | This project. | `git@github.com:ybagheri/signal-to-trade-bridge.git` |

Both are private, unpublished Python packages with a `src/` layout and no
runtime dependencies of their own.

---

## Requirements

* **Python 3.11 or newer.** `albrooks` needs 3.10; `auto-trade` needs 3.11.
* **Windows**, for anything touching MetaTrader 5. The domain layer and the whole
  unit test suite are platform-independent and run anywhere.
* Local checkouts of both upstream repositories. See
  [docs/architecture.md §7](docs/architecture.md) for the dependency strategy and
  why git submodules and pinned git dependencies were rejected.

---

## Development protocol — for humans and for AI agents

> **Any AI agent continuing development of this repository MUST read
> [`HANDOFF.md`](HANDOFF.md) before making changes.**
>
> It must then verify the actual repository state with `git status` and
> `git log --oneline -n 10`, and inspect the source.
>
> **`HANDOFF.md` is a continuity aid, not an authority over the codebase.** If it
> disagrees with the repository, **the repository wins and `HANDOFF.md` must be
> corrected** to match. A stale handoff file is a bug, not a reference.

The same rule applies to a developer returning after time away: trust the code,
then fix the handoff document.

---

## License

Proprietary. See the repository owner for terms.
