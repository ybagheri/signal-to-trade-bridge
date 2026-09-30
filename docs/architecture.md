# Architecture — Signal-to-Trade Bridge

> **Status:** Phase 0 deliverable. This document records what the two upstream
> repositories *actually* provide, verified by reading their source, and what the
> bridge therefore owns. Every claim about an upstream API is quoted from real
> code. Where something does not exist, this document says **DOES NOT EXIST**
> rather than describing a plausible design.

---

## 1. The one-paragraph version

`al-brooks-price-action-engine` produces a **price-action reading** — a
`BUY`/`SELL`/`WAIT`/`NO_TRADE` action plus a `TradePlan` carrying absolute
entry, stop and target prices. `auto-trade` **executes** a single market order
with a stop loss and take profit, through a Windows desktop-automation bridge to
MetaTrader 5. Neither project knows the account balance, neither knows a symbol's
contract specification, and neither computes a position size. The bridge is the
missing middle: it normalises the reading, sizes the position against real
account and symbol data, validates the whole thing, and hands a finished order to
`auto-trade`.

The 0.5%-risk rule cannot be implemented without facts neither upstream project
holds. **That is the bridge's central responsibility, not an optional extra.**

---

## 2. Upstream A — `al-brooks-price-action-engine`

Local path `E:\al-brooks-price-action-engine`, remote
`git@github.com:ybagheri/al-brooks-price-action-engine.git`.

### 2.1 Packaging

| Property | Value |
|---|---|
| Distribution name | `albrooks` |
| Import name | `albrooks` |
| Version | `0.1.0` (`src/albrooks/version.py`) |
| Layout | `src/` (setuptools, PEP 621) |
| Python | `>=3.10` |
| Runtime dependencies | **none** (`dependencies = []`) |
| Extras | `dev` (pytest, pytest-asyncio, ruff, mypy, tomli), `pandas` (declared, never imported) |
| Entry point | none — it is a library |

`dependencies = []` is enforced by a test, and a CI script
(`scripts/check_no_mt5_dependency.py`) AST-scans `src/albrooks` and **fails** if
anything outside `albrooks/adapters/mt5/` imports `MetaTrader5`, `numpy`,
`pandas`, `scipy`, `sklearn` or `torch`. The engine core is deliberately
dependency-free and platform-independent. The bridge must not break that: it
inherits a zero-dependency core by construction if it only imports the core.

### 2.2 The public API is six names

`src/albrooks/__init__.py`, in full:

```python
from albrooks.core.bars import Bar, BarSeries
from albrooks.engine.analyzer import Analyzer
from albrooks.engine.configuration import AnalyzerConfig
from albrooks.engine.state import AnalysisResult
from albrooks.version import __version__

__all__ = [
    "__version__",
    "Bar",
    "BarSeries",
    "Analyzer",
    "AnalyzerConfig",
    "AnalysisResult",
]
```

The entry point is:

```python
class Analyzer:
    def __init__(
        self,
        config: AnalyzerConfig | None = None,
        registry: SetupRegistry | None = None,
    ) -> None: ...

    def analyze(
        self,
        bars: Sequence[Bar | dict[str, Any]] | BarSeries,
        symbol: str = "GENERIC",
        timeframe: str = "UNKNOWN",
        last_closed: int | None = None,
    ) -> AnalysisResult: ...
```

The full body of `analyze` is quoted in `docs/integration.md`. The parts that
matter to the bridge: it clamps `last_closed` to the series, returns
`self._empty(series, reason)` for degenerate input rather than raising, and
builds the result with `decision=decision.to_dict()`.

### 2.3 There is no `Signal` class

**`Signal` DOES NOT EXIST** — no class, dataclass, enum or type by that name
anywhere in `src/` or `tests/`. There is no callback, observer, event bus or
notification subsystem. There is also **no `FLAT`**; the vocabulary is:

```python
class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"
```

Note `WAIT` and `NO_TRADE` are **different** answers, and the distinction is
load-bearing: `NO_TRADE` means the engine declines to answer; `WAIT` means there
was something and a stated condition is not met. The bridge must map **both**
onto its own no-trade outcome, and should preserve which one it was for
traceability.

A signal is a **`dict` at `AnalysisResult.decision`**, and that dict is exactly
`Decision.to_dict()`:

```python
@dataclass(frozen=True, slots=True)
class Decision:
    action: str
    reason: str
    subject: str = ""
    direction: int = 0
    plan: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    explanation: tuple[str, ...] = ()
    vetoes: tuple[dict[str, Any], ...] = ()
    considered: dict[str, Any] = field(default_factory=dict)
    ranking_basis: tuple[str, ...] = RANKING_BASIS
    sides: dict[str, float] = field(default_factory=dict)
```

`Decision.is_actionable` is `action in ("BUY", "SELL")` — deliberately about the
action, not the geometry.

Reason codes are stable strings: `DECISION_DISABLED`, `NO_ANALYSIS`,
`NO_CANDIDATES`, `ALL_CANDIDATES_VETOED`, `EVIDENCE_CONFLICT`, `RANKED_CANDIDATE`,
`AGAINST_HTF`.

### 2.4 The degenerate path has a different shape — a real trap

`Analyzer._empty()` builds a decision with **only three keys**:

```python
decision={
    "action": "NO_TRADE",
    "reason": reason,
    "note": "No analysis was run; see market_state.reason.",
},
```

where `reason` is `NO_BARS`, `NEGATIVE_LAST_CLOSED` or `ATR_UNAVAILABLE`. So the
13-key shape is **not** guaranteed. The adapter must read `plan`, `explanation`,
`vetoes` and `considered` with `.get()` and tolerate their absence. `NO_BARS` and
`ATR_UNAVAILABLE` are statements about the *input*, not the market, and the
engine says so explicitly.

### 2.5 The stop loss exists, and it carries its provenance

`TradePlan` (`src/albrooks/trade/plan.py`) is a frozen slotted dataclass. Its
geometry fields:

```python
subject: str
direction: int = 0          # +1 long, -1 short, 0 none
entry: float = 0.0
stop: float = 0.0
target: float = 0.0
entry_basis: str = ENTRY_BASIS_NONE
stop_basis: str = STOP_BASIS_NONE
target_basis: str = TARGET_BASIS_NONE
entry_reference: float = 0.0
stop_reference: float = 0.0
target_reference: float = 0.0
bar_index: int = -1
signal_bar: int = -1
invalidation: str = ""
management: tuple[str, ...] = ()
issues: tuple[str, ...] = ()
warnings: tuple[str, ...] = ()
evidence_score: float | None = None
```

`stop` is an **absolute price**, not a distance. Its `stop_basis` says where it
came from:

| `stop_basis` value | Meaning | Structural? |
|---|---|---|
| `PULLBACK_EXTREME` | beyond the pullback's extreme | yes |
| `BREAKOUT_REFERENCE` | beyond the breakout reference | yes |
| `PATTERN_EXTREME` | beyond a double top/bottom extreme | yes |
| `SWING` | beyond an identified swing | yes |
| `ATR_FALLBACK` | an ATR multiple | **no** |
| `NONE` | undefined | **no** |

The engine exposes exactly the predicate the bridge needs:

```python
@property
def has_structural_stop(self) -> bool:
    """Whether the stop is derived from something the market actually did.

    A caller filtering for plans worth reading should test this rather than
    `is_valid`: a valid plan on an `ATR_FALLBACK` stop is arithmetically sound
    and structurally empty.
    """
    return self.stop_basis not in (STOP_BASIS_ATR, STOP_BASIS_NONE)
```

`target_basis` is the mirror image: `MEASURED_MOVE`, `FADE_ORIGIN`, `SWING`,
`ATR_FALLBACK`, `NONE`.

The engine also validates the geometry itself, and exposes it as
`issues`/`is_valid`. `BLOCKING_ISSUES` is the eight codes `NO_DIRECTION`,
`NO_ATR`, `ENTRY_UNDEFINED`, `STOP_UNDEFINED`, `STOP_NOT_PROTECTIVE`,
`TARGET_UNDEFINED`, `TARGET_NOT_AHEAD`, `RISK_NOT_POSITIVE`. `TradePlan.risk` is
`abs(entry - stop)` and `reward_to_risk` is `reward / risk` (`0.0` when risk is
`0`, never `inf`).

**Consequence for the bridge:** the engine has already answered "is there a
defensible stop, and is it on the right side of entry". The bridge must re-check
this rather than trust it, because `auto-trade` has no such check at all and a
plan that crosses the bridge boundary must be re-verified at the boundary.

### 2.6 Confidence is not a probability

`EvidenceScore` yields a value in `0..1` with a band (`NONE`/`WEAK`/`MODERATE`/
`STRONG`) and `EvidenceScore.to_dict()` carries an explicit
`"is_probability": False`. `Decision.to_dict()` repeats it. The engine is
insistent on this in prose, in `NOTE_PLAN_IS_NOT_A_RECOMMENDATION`, and in
`RANKING_BASIS`.

**Consequence:** a `minimum_signal_confidence` setting in the bridge is a
*threshold on an evidence score*, not on a win probability, and the bridge's
documentation and configuration names must say so. Silently treating it as a
probability would be the most consequential available misreading.

### 2.7 Multi-timeframe

`albrooks.engine.pipeline` provides `analyze_multi_timeframe(lower, higher, *,
ratio=0, config=None, symbol="GENERIC", ...)` returning `MultiTimeframeResult`
with `.lower`, `.higher`, `.bias` (`HTFBias`), `.decision`, `.symbol`,
`.lower_timeframe`, `.higher_timeframe`.

`apply_htf_veto` **withholds** a lower-timeframe decision that runs against a
strong higher-timeframe read — it never inverts it — and does so by rewriting the
action to `WAIT` with reason `AGAINST_HTF` and adding
`VETO_AGAINST_HIGHER_TIMEFRAME`. This is a good default for the bridge: the
engine already suppresses the case where it is least confident, and the bridge
should not try to second-guess it.

### 2.8 The MT5 adapter exists and is reusable

`albrooks.adapters.mt5` is the **only** sub-package allowed to name
`MetaTrader5`. Its `__init__.py` exports 43 names. The relevant class:

```python
class MT5Feed:
    def __init__(self, mt5_module: Any | None = None) -> None: ...

    def connect(
        self,
        symbol: str | None = None,
        *,
        path: str | None = None,
        login: int = 0,
        timeout_ms: int = 60_000,
    ) -> None: ...

    def shutdown(self) -> None: ...
    def select_symbol(self, symbol: str) -> None: ...
    def bars(self, ...) -> ...: ...
    def closed_bars(self, ...) -> FrozenSeries: ...
    def frozen_bars(self, *args: Any, **kwargs: Any) -> FrozenSeries: ...
    @staticmethod
    def period(timeframe: int) -> float: ...
    def server_time(self, symbol: str | None = None) -> float: ...
```

Two properties make this directly reusable by the bridge:

1. `mt5_module` is injectable — an object with the same shape as the real
   bindings. The engine's own suite exercises every branch without a terminal,
   and **`MetaTrader5` appears nowhere in its tests**. The bridge can do exactly
   the same.
2. It never launches a terminal and never hard-codes a path: `connect(path=...)`
   passes the path straight to `mt5.initialize(path=path, ...)`.

Timeframes have a real vocabulary in `adapters/mt5/timeframes.py` —
`TIMEFRAMES: dict[str, int]` mapping `"M1"…"MN1"` to the MT5 `ENUM_TIMEFRAMES`
values, plus `canonical_name`, `period_seconds` (raises `TimeframeUnsupported`
for `MN1`) and `timeframe_from_name` (case-insensitive, raises on unknown). The
engine core itself uses a free-form `str` for timeframe and deliberately measures
the bar step from timestamps instead of parsing the label.

### 2.9 What the engine does NOT have

| Capability | Status |
|---|---|
| A `Signal` class | **DOES NOT EXIST** — a `dict` at `AnalysisResult.decision` |
| Callback / observer / event bus | **DOES NOT EXIST** |
| Position sizing / lot calculation | **DOES NOT EXIST** |
| Account balance, equity, margin, currency | **DOES NOT EXIST** |
| Symbol specification (contract size, tick size/value, volume step/min/max, digits, point) | **DOES NOT EXIST** — symbol is an opaque `str` |
| A structured logging subsystem | **DOES NOT EXIST** |
| Persistent state / dedup store | **DOES NOT EXIST** |
| `FLAT` action | **DOES NOT EXIST** — `WAIT` and `NO_TRADE` instead |

Symbol is an opaque `str` threaded through `BarSeries.symbol`,
`Analyzer.analyze(symbol=...)`, `AnalysisResult.symbol` and `MT5Feed`. There is
no instrument model anywhere. Timestamps are `float` Unix epoch seconds on
`Bar.time` — no `datetime`, no timezone type, no ISO parsing.

### 2.10 Test and tooling facts

* `pyproject.toml` `[tool.pytest.ini_options]` sets `testpaths = ["tests"]` and
  `addopts = "-v --strict-markers"`. **It does not set `pythonpath`** — see the
  defect below.
* `tests/conftest.py` inserts `src` on `sys.path` by hand. That is the only
  reason the suite can import `albrooks`.
* **Packaging defect, verified:** `pythonpath = ["src"]` sits *after*
  `[project.optional-dependencies]` and *before* `[tool.setuptools.packages.find]`
  in the `pyproject.toml` top level, so setuptools parses it as **an extra
  requirement group named `pythonpath`**, not as pytest config. The generated
  metadata says `Provides-Extra: pythonpath` and `Requires-Dist: src; extra ==
  "pythonpath"`. Installing `albrooks[pythonpath]` would try to resolve a package
  named `src` from PyPI.
* Ruff: `line-length = 100`, `target-version = "py310"`, lint `["E","F","W","I"]`.
* Mypy: `python_version = "3.10"`, `disallow_untyped_defs = true`,
  `check_untyped_defs = true`, with a targeted `ignore_missing_imports` override
  for `MetaTrader5` only.
* `scripts/check_docs_present.py` requires 34 named documents.
* `.github/workflows/ci.yml` runs lint, mypy, pytest, both gate scripts, the MQL5
  parity harness and a build. Live-terminal tests **skip** without a terminal —
  and the repository's own `HANDOFF.md` is emphatic that **a skip is not a pass**.

### 2.11 Portability defects to avoid inheriting

`scripts/build_mql5.py` hard-codes
`DEFAULT_PROGRAM = Path(r"C:\Program Files\Alpari MT5_5")` and is Windows-only.
The engine core itself is clean. The bridge must not copy this pattern.

---

## 3. Upstream B — `auto-trade`

Local path `E:\auto-trade`, remote `git@github.com:ybagheri/auto-trade.git`,
branch `main` at `b88e9e7`, plus a branch
`feat/observer-verification-and-dashboard` at `3bd3a98`.

### 3.1 Packaging

| Property | Value |
|---|---|
| Distribution name | `auto-trade` |
| Import name | `auto_trade` |
| Version | `0.1.0` |
| Layout | `src/` (setuptools) |
| Python | `>=3.11` |
| Runtime dependencies | **none** — standard library only |
| Extras | `dev` (pytest, ruff, mypy), `windows` (`pywinauto>=0.6`), `build` (pyinstaller) |
| Console script | `auto-trade = auto_trade.cli:main` |
| Transitive, undeclared | `comtypes`, `pywin32` (pulled in by pywinauto) |

`src/auto_trade/__init__.py` is a single line — `__version__ = "0.1.0"` — with no
re-exports. Everything is imported from a submodule. The cleanest stable surface
is `auto_trade.domain`, whose `__all__` names 24 types.

### 3.2 The decisive finding: there is no `MetaTrader5` package here

`auto-trade` does **not** use the official MetaTrader 5 Python bindings. There is
no `import MetaTrader5`, no `mt5.initialize()`, no retcode handling anywhere in
`src/` or `tests/`. It is a **Windows desktop UI-automation bridge**: `pywinauto`
with the `uia` backend drives the MT5 order dialog, writes the fields, and clicks
`Buy by Market` / `Sell by Market`.

This has two consequences the bridge must plan around:

1. **The bridge cannot get account data or symbol specification from
   `auto-trade`,** because `auto-trade` cannot either — it only reads what a
   human can see on screen. Its `AccountSnapshot` is three fields
   (`account_type`, `open_positions`, `connected`) and its `MT5DesktopAdapter.connect()`
   hard-returns `AccountSnapshot(AccountType.DEMO, connected=True)` without
   reading a number from the terminal.
2. **The bridge will need its own `MetaTrader5` bindings for market and account
   data,** taken from the `albrooks.adapters.mt5` pattern, which is a clean,
   injectable, already-tested design.

These are complementary: `albrooks` reads data through the official bindings;
`auto-trade` acts through the UI. The bridge sits between them and needs both.

### 3.3 The execution port to target

`src/auto_trade/domain/protocols.py` defines the abstraction the bridge should
program against, not the concrete `MT5DesktopAdapter`:

```python
class TradingTerminalAdapter(Protocol):
    def connect(self) -> AccountSnapshot: ...
    def get_terminal_state(self) -> str: ...
    def select_symbol(self, symbol: str) -> None: ...
    def prepare_order(self, request: OrderRequest) -> None: ...
    def execute_order(self, request: OrderRequest) -> ExecutionResult: ...
    def verify_execution(self, request: OrderRequest) -> ExecutionResult: ...
    def close_position(self, position_id: str) -> ExecutionResult: ...
```

**But the bridge should not call this protocol directly.** `ExecutionWorkflow` is
the orchestrator that sequences the risk engine, the kill switch, the ledger, the
state machine, the audit log and the terminal. Calling the adapter directly would
bypass every one of those gates. The bridge's execution adapter wraps
`ExecutionWorkflow`:

```python
class ExecutionWorkflow:
    def __init__(
        self,
        adapter: TradingTerminalAdapter,
        risk_engine: RiskEngine,
        profile: TerminalProfile,
        policy: ExecutionPolicy,
        kill_switch: KillSwitch,
        audit: Callable[[AuditEvent], None],
        now: Callable[[], datetime] | None = None,
        ledger: ExecutionLedger | None = None,
    ) -> None: ...

    def execute(self, signal: TradeSignal) -> ExecutionResult: ...
```

`execute` is entirely synchronous and returns `ExecutionResult` — never a ticket,
never a dict:

```python
class ExecutionResult:
    def __init__(
        self,
        execution_id: str,
        signal_id: str,
        status: ExecutionStatus,
        state: str,
        message: str,
        order_reference: str | None = None,
        error: str | None = None,
        evidence: VerificationEvidence | None = None,
    ) -> None: ...
```

`order_reference` is the **position ticket**, not the order ticket.

### 3.4 The input contract — exactly what the bridge must produce

```python
class TradeSignal:
    def __init__(
        self,
        signal_id: str,
        timestamp: datetime,
        source: str,
        symbol: str,
        action: OrderAction,
        volume: Decimal,
        order_type: str | None = None,
        price: Decimal | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        comment: str = "",
        strategy: str = "",
        confidence: float | None = None,
        expiration: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...
```

Validation it performs itself: id non-empty and passing `validate_signal_id`,
source non-empty, symbol non-empty, `volume > 0`, `0 <= confidence <= 1`. Symbol
is upper-cased; timestamp is converted to UTC.

`from_dict` requires exactly these keys: `id`, `timestamp`, `source`, `symbol`,
`action`, `volume`. Optional: `order_type`, `price`, `stop_loss`, `take_profit`,
`comment`, `strategy`, `confidence`, `expiration`, `metadata`.

Then:

```python
class OrderRequest:
    def __init__(self, signal: TradeSignal) -> None:
        self.signal = signal
        self.symbol = signal.symbol
        self.action = signal.action
        self.volume = signal.volume
        self.price = signal.price
        self.stop_loss = signal.stop_loss
        self.take_profit = signal.take_profit
```

`OrderRequest` has **no** order type, deviation, magic number, expiration,
filling mode or time mode. `price` and `order_type` are carried but never used —
only market BUY and SELL exist. `OrderAction` defines nine values, but
`ALLOWED_ACTIONS = {OrderAction.BUY, OrderAction.SELL}`; the other seven raise
`AutomationRejectedError`. There are no pending orders, no modifications, no
partial closes and no SL/TP amendment after the fact.

**Consequence for the bridge:** the bridge targets market orders only, and must
never present itself as able to place a limit or a stop order. This belongs in
the bridge's documentation, not only in a comment.

### 3.5 SL and TP handling is a field write and a read-back

`MT5DesktopAdapter.prepare_order` selects Market Execution, writes volume
(`10333`), stop loss (`10334`) and take profit (`10336`), reads volume back, and
captures a position baseline. That is the whole of it.

The only validation is `confirm_dialog_matches`, which re-reads symbol, volume,
SL and TP from the dialog and refuses to submit if any differs from what the risk
engine approved. There is **no** check for:

* stop level / stops-level distance
* freeze level
* digits or point rounding of SL and TP
* **an SL on the wrong side of entry** — an SL above the ask for a BUY is not
  rejected by this code

`SymbolInfo` in `auto_trade.domain.models` has three fields (`symbol`,
`available`, `digits`) and is **dead code** — verified: its only occurrences are
its own definition and two re-export lines. It is never constructed, returned or
read. There is no symbol-info provider.

**This is the strongest single argument for the bridge's validation phase.** The
downstream execution layer will happily forward a malformed stop to the broker
and let the broker refuse. The bridge is the last place where that can be caught
locally, and it is where it must be caught.

### 3.6 The idempotency mechanism already exists — reuse it

`auto_trade.application.ledger` provides exactly what Phase 9 needs:

```python
class ExecutionLedger(Protocol):
    def contains(self, signal_id: str) -> bool: ...
    def record_attempt(self, signal_id: str, execution_id: str) -> None: ...
    def record_result(self, result: ExecutionResult) -> None: ...
    def records(self) -> tuple[dict[str, Any], ...]: ...

class JsonExecutionLedger:
    def __init__(self, path: Path) -> None: ...
    def contains(self, signal_id: str) -> bool: ...
    def record_attempt(self, signal_id: str, execution_id: str) -> None: ...
    def record_result(self, result: ExecutionResult) -> None: ...
    def records(self) -> tuple[dict[str, Any], ...]: ...
    def pending(self) -> tuple[dict[str, Any], ...]: ...
    def reconcile(self, signal_id: str, observation: str) -> dict[str, Any]: ...
```

`JsonExecutionLedger` is a durable, cross-process, atomic-write JSON store
(writes to `.tmp` then `replace`). `ExecutionWorkflow` consults it:

```python
seen_signal_ids=(
    {signal.signal_id}
    if self.ledger is not None and self.ledger.contains(signal.signal_id)
    else self.seen_signal_ids
),
```

so a re-delivered `signal_id` is rejected by `RiskEngine` with
`"duplicate signal id"`. The bridge should reuse this rather than build a second
store. It should also make sure the `signal_id` it generates is **deterministic
from the signal content** — see §5.3.

### 3.7 The safety gates the bridge inherits

`auto_trade.infrastructure.automation.execution.ExecutionGate`:

```python
@dataclass(frozen=True)
class ExecutionGate:
    enabled: bool = False
    dry_run: bool = True
    demo_only: bool = True
    kill_switch_active: bool = False

    def refusal(self) -> str:
        if not self.enabled:
            return "execution is disabled; set AUTO_TRADE_ENABLE_EXECUTION=true to allow it"
        if self.kill_switch_active:
            return "kill switch is active"
        if self.dry_run:
            return "dry-run is enforced"
        if self.demo_only is False:
            return "demo-only policy is not satisfied"
        return ""
```

`enabled` is an explicit opt-in and is **never** derived from a default, so a
fresh checkout cannot execute. `FileKillSwitch` is a durable cross-process
sentinel file. `AppConfig.live_execution_refusal()` additionally refuses live
execution when the enabling `AUTO_TRADE_ENABLE_EXECUTION=true` came from an
untrusted `.env` found in the current working directory rather than a reviewed
checkout.

`MT5DesktopAdapter.connect()` also refuses unless the window title contains
`"demo"`.

**These are strong defaults and the bridge must not weaken them.** A separate
`AUTO_TRADE_ENABLE_EXECUTION` switch must not become a way around the bridge's
own dry-run switch, and the bridge's dry-run must not be defeatable by
`auto-trade` configuration.

### 3.8 The click-is-not-a-fill semantic

This is the most important semantic to internalise. `execute_order` returns
`ExecutionStatus.REQUESTED` with the message
`"...acceptance not yet observed"`. `ACCEPTED` comes only from
`verify_execution`, which polls an independently observed position list and
accepts only if **exactly one** new position matches symbol, side and volume. An
unreadable snapshot yields `UNKNOWN`, not failure, and the loop retries to a
deadline.

So `ExecutionResult.status` has these meanings, and the bridge must map them
onto its own outcome without collapsing them:

| `ExecutionStatus` | Meaning | Bridge should |
|---|---|---|
| `ACCEPTED` | independently observed position | success, with the position ticket |
| `REQUESTED` | control clicked, not observed | **not** success; report as unconfirmed |
| `DRY_RUN` | validated, control not used | success of the decision, explicitly not a trade |
| `REJECTED` | refused, or gate closed | failure, with the reason |
| `UNKNOWN` | control may have been used, unproven | **must not be retried blindly** |
| `CLOSED` | position closed | out of scope for signal handling |

`UNKNOWN` deserves care. `ExecutionWorkflow` also raises `ExecutionUnknownError`
when the final control was used or attempted and the outcome is unknown. The
bridge's rule: an `UNKNOWN` result is recorded and escalated, never
automatically retried, because a retry may open a second position.

### 3.9 Risk limits — what they are and are not

`RiskEngine.validate` produces exactly seven rejections, in order:

1. `"duplicate signal id"`
2. `"symbol is not allowed"`
3. `"volume exceeds configured limit"`
4. `"signal is expired"`
5. `"order rate limit reached"`
6. `"account is not connected"`
7. `"maximum open positions reached"`

These are **operational safety limits, not money-at-risk limits.** There is no
balance, no percentage, no monetary exposure check anywhere in `auto-trade`.
`RiskEngine` can only cap volume with an absolute ceiling
(`AUTO_TRADE_MAX_VOLUME`, default `1.0`) and a rate. The 0.5%-of-balance rule is
therefore entirely the bridge's, and the two mechanisms are complementary: the
bridge computes a volume from the balance, and `auto-trade` independently caps
that volume. The bridge should treat a `RiskEngine` rejection as a normal,
expected outcome and log it as such, not as a fault.

Note also that `max_open_positions` has **no env var and no config wiring**; it
defaults to `None`, and the real adapter never populates
`AccountSnapshot.open_positions`, so that gate is inert in production.

### 3.10 Configuration

`AppConfig.from_env()` reads 23 `AUTO_TRADE_*` environment variables, listed in
`docs/integration.md`. Two of them default to **absolute paths on another
machine**:

```python
terminal_path = Path(os.getenv("AUTO_TRADE_TERMINAL_PATH",
    r"C:\Program Files\Alpari MT5_2\terminal64.exe"))
data_path = Path(os.getenv("AUTO_TRADE_DATA_PATH",
    r"C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal\AF19ECCF568F855DF9D3196BBF8BF315"))
```

The bridge must never read or require these, and must not copy this defaulting
pattern. `_flag` treats `{"1","true","yes","on"}` as true. The signal
`data_path` is also where the MQL5 file bridge lives:
`MT5BridgeSignalProvider` reads `auto_trade_signal_*.json` from
`<data path>\MQL5\Files`.

### 3.11 What `auto-trade` does NOT have

| Capability | Status |
|---|---|
| Official `MetaTrader5` bindings | **DOES NOT EXIST** — UI automation via `pywinauto` |
| Account balance, equity, margin, currency | **DOES NOT EXIST** — zero occurrences in the tree |
| Symbol specification (contract size, tick size/value, volume step/min/max, point, spread, trade mode) | **DOES NOT EXIST** — zero occurrences in `src/`, `docs/`, `mql5/` |
| A working `SymbolInfo` | **DOES NOT EXIST** — the class is dead code with 3 fields |
| Position sizing / lot calculation | **DOES NOT EXIST** — the only volume treatment is a `> 0` check and `format(v, "f")` |
| Rounding to `volume_step` | **DOES NOT EXIST** |
| Risk as a percentage of balance | **DOES NOT EXIST** |
| Stop-level / freeze-level / side-of-entry validation | **DOES NOT EXIST** |
| Pending orders, SL/TP modification, partial close, hedging | **DOES NOT EXIST** — market BUY/SELL only |
| Terminal launch or login | **DOES NOT EXIST** — the terminal must already be running and logged in |
| Logout, session or trading-hours checks | **DOES NOT EXIST** |
| An inbound order API | **DOES NOT EXIST** — `api.py` refuses every account-changing route with HTTP 403 |

---

## 4. The gap analysis — what the bridge must own

| # | Capability | Upstream A | Upstream B | Bridge must provide |
|---|---|---|---|---|
| 1 | Signal semantics | `Action` enum + `Decision` dict | `OrderAction` enum + `TradeSignal` | **Own `Signal` value object**; map both vocabularies onto it, losslessly |
| 2 | Entry / stop / target prices | `TradePlan.entry/stop/target` + bases | SL/TP fields, passed through | **Own `Entry`/`StopLoss`/`TakeProfit`; validate geometry independently** |
| 3 | Account balance + currency | ✗ | ✗ | **New `AccountProvider` port + MT5 implementation + fake** |
| 4 | Symbol specification | ✗ | ✗ | **New `SymbolSpec` + `SymbolSpecProvider` port + MT5 implementation + fake** |
| 5 | Position sizing | ✗ | ✗ | **New sizing calculator, tick-value based, not Forex-assumed** |
| 6 | Risk as % of balance | ✗ | ✗ | **New `RiskParameters` + risk amount calculation** |
| 7 | Take-profit policy | `target` + `target_basis` | passed through | **Configurable `SIGNAL_IF_AVAILABLE_ELSE_RR`, never silently overriding** |
| 8 | Pre-trade validation | partial (`issues`, `is_valid`) | none (field read-back only) | **Full pipeline, fail-closed, structured reasons** |
| 9 | Execution | ✗ | `ExecutionWorkflow.execute` | **Thin adapter; delegate, never re-implement** |
| 10 | Idempotency | ✗ | `JsonExecutionLedger` | **Reuse; add a deterministic `signal_id` derived from signal content** |
| 11 | Structured logging | ✗ | `AuditLogger` (JSONL, `AuditEvent`) | **Bridge-level structured events; reuse `AuditLogger` for execution** |
| 12 | Market data (bars) | `MT5Feed`, `albrooks.adapters.mt5` | ✗ | **Reuse `MT5Feed`; wrap it behind the bridge's own `MarketDataProvider` port** |
| 13 | Timeframe vocabulary | `TIMEFRAMES`, `period_seconds`, `timeframe_from_name` | ✗ | **Reuse** |
| 14 | Dry run | ✗ | `ExecutionPolicy.dry_run`, `ExecutionGate.dry_run` | **Bridge-level dry run that short-circuits before the executor** |
| 15 | Kill switch | ✗ | `FileKillSwitch` | **Honour it in the bridge's own decision path, not only in the executor** |

Items 3, 4 and 5 are the substance of this project. Everything else is
adaptation.

---

## 5. Proposed bridge architecture

### 5.1 Layering

```
┌──────────────────────────────────────────────────────────────┐
│ interfaces/     CLI, wiring, composition root                │
├──────────────────────────────────────────────────────────────┤
│ application/    ProcessSignal — the single use case          │
├──────────────────────────────────────────────────────────────┤
│ domain/         Signal, TradeIntent, RiskParameters,         │
│                 PositionSize, TradeDecision, SymbolSpec,     │
│                 AccountBalance, and the pure calculations    │
│                 (sizing, RR, validation)                      │
├──────────────────────────────────────────────────────────────┤
│ ports/          Protocols: SignalSource, MarketDataProvider, │
│                 AccountProvider, SymbolSpecProvider,         │
│                 TradeExecutor, IdempotencyStore              │
├──────────────────────────────────────────────────────────────┤
│ adapters/       albrooks/  auto-trade/  mt5/  fake/           │
└──────────────────────────────────────────────────────────────┘
```

Dependency direction is strictly inward. `domain` imports nothing from any other
layer, and nothing from `adapters/`. Every external project is reached through a
protocol in `ports/`, so an upstream change is contained in one adapter.

`domain` uses `Decimal` for prices, volumes and money. Not `float`. The upstream
projects use `float` (albrooks) and `Decimal` (auto-trade) respectively, and
position sizing is arithmetic that must not drift: a `0.01` lot error on a gold
contract is a real-money error. `Decimal` is the correct choice at the boundary
and internally.

### 5.2 The decision pipeline

```
Signal (internal, normalised)
  → validate signal                      → TradeDecision(NO_TRADE, reason)
  → resolve stop  (signal, else refuse)   → TradeDecision(NO_TRADE, NO_VALID_STOP)
  → resolve take profit (policy)          → TradeDecision(NO_TRADE, INVALID_TAKE_PROFIT)
  → account balance + symbol spec         → TradeDecision(NO_TRADE, MARKET_DATA_UNAVAILABLE)
  → risk amount + position size           → TradeDecision(NO_TRADE, SIZING_FAILED)
  → broker constraints + volume           → TradeDecision(NO_TRADE, INVALID_VOLUME)
  → idempotency check                     → TradeDecision(NO_TRADE, DUPLICATE_SIGNAL)
  → dry run?  record and stop
  → executor.submit(intent)               → ExecutionResult
```

Every arrow is fail-closed. Each rejection carries a stable, machine-comparable
reason code — not a sentence to be parsed.

### 5.3 Signal identity, decided now

`auto-trade`'s dedup is keyed on `signal_id`. For that to prevent duplicates
across restarts, the bridge's `signal_id` must be **deterministic from signal
content**, not a fresh UUID. The components available from the engine:

* `symbol`, `timeframe` (`AnalysisResult`)
* `bar_index` / `last_closed_bar` — the newest bar the analysis was allowed to
  read, which is the closest thing the engine has to "this signal, on this bar"
* `direction` / `action`
* `subject` — `"<detector>#<position>"`, the engine's own candidate identifier
* `Bar.time` of the last closed bar — a wall-clock anchor

Proposed key: a SHA-256 over
`(symbol, timeframe, last_closed_bar, bar_time, action, subject, entry, stop, target)`
rendered as a prefixed hex string. This makes re-delivery of the same reading
across a process restart idempotent, and it makes a *genuinely different* reading
on the same bar — a different detector, a different stop — a different signal.
That last property is the one to be careful about: including the prices means a
recomputation that nudges a stop by one tick is a new signal. Phase 9 will
settle this against real engine output; the audit records it as an open
decision rather than pretending it is settled.

### 5.4 Stop-loss policy

Derived from §2.5, and it is not a free choice:

1. The engine's `plan["stop"]` is used whenever it is present and structurally
   defensible — that is, `has_structural_stop` is true (`stop_basis` is not
   `ATR_FALLBACK` and not `NONE`).
2. An `ATR_FALLBACK` stop is **refused by default**, not used. The engine itself
   documents that such a plan is "arithmetically sound and structurally empty",
   and the brief says not to invent a stop merely to make the system trade. A
   configuration flag may permit it, off by default, and when permitted the
   decision log must record that the stop was a volatility fallback.
3. `stop <= 0.0` or a missing `stop` is always `NO_TRADE` — reason
   `NO_VALID_STOP`. Never a synthesised stop.
4. The engine provides an absolute price, never a distance, so no conversion is
   needed. If a future engine version provides a distance instead, the adapter
   converts it and the conversion is covered by a test — the adapter is the only
   place that knows which form it received.

### 5.5 Take-profit policy

`TAKE_PROFIT_SOURCE` with two documented values, default
`SIGNAL_IF_AVAILABLE_ELSE_RR`:

* `SIGNAL_IF_AVAILABLE_ELSE_RR` — use `plan["target"]` when it is structurally
  defensible (`target_basis` in `MEASURED_MOVE`, `FADE_ORIGIN`, `SWING`) and on
  the correct side of entry; otherwise fall back to the R:R calculation.
* `RR_ONLY` — ignore any signal target and always use `reward_risk_ratio`. This
  exists because a 1:1 policy and a signal's own measured target are different
  intentions, and a trader may want the ratio to win.

Either way the bridge **never silently overrides** a signal target: when it
falls back, the decision records `tp_source = "RR_FALLBACK"` and the reason. A
`TARGET_UNDEFINED` or `TARGET_NOT_AHEAD` issue from the engine is treated as "no
usable signal target", not as an error to be papered over.

### 5.6 Position sizing — the part that must be built

No reusable implementation exists upstream, so the bridge writes one. It is
tick-value based and instrument-agnostic:

```
risk_amount      = account_balance × risk_percent / 100
stop_distance    = |entry − stop|                       (in price)
ticks            = stop_distance / tick_size
risk_per_unit    = ticks × tick_value
raw_volume       = risk_amount / risk_per_unit          (if risk_per_unit > 0)
volume           = clamp_to_step_then_bounds(raw_volume, step, min, max)
```

`tick_value` and `tick_size` come from the symbol specification, which is what
makes this correct for gold, indices, CFDs and futures-like instruments rather
than only for 5-digit Forex pairs. `tick_value` in MT5 is quoted in the
**account** currency for a volume of 1 lot, and the bridge normalises profit and
loss currencies explicitly: if `tick_value_profit` and `tick_value_loss` differ
(the profit/loss hedging case, and swap-rate-free CFDs), the **worse** of the two
is used, because a size that is safe on paper must be safe on the losing side.

Rounding is **down** to `volume_step`. Rounding up could exceed the risk budget;
rounding down leaves it slightly under, which is the safe direction. The result
is then clamped into `[volume_min, volume_max]`, and if clamping had to change
the value the decision records the fact, because a clamped volume is a volume
the trader did not ask for.

An important edge case, and one that must be a test: if
`clamp(raw) < volume_min`, the trade is **refused**, not floored up to the
minimum. Flooring to the minimum would place a position whose risk exceeds the
budget by an unbounded amount — the single most dangerous line in any position
sizer. Default: `NO_TRADE` with reason `VOLUME_BELOW_BROKER_MINIMUM`.

### 5.7 Execution — delegate, do not re-implement

`AutoTradeExecutor` wraps `ExecutionWorkflow`:

* builds an `auto_trade.domain.models.TradeSignal` from the bridge's
  `ExecutionRequest`
* sets `signal_id` to the deterministic key from §5.3
* sets `source` to a bridge identifier and `strategy` to the engine's
  `subject`, so an audit record can be traced back to the detector
* puts the engine's evidence score in `confidence` — **renamed and documented as
  an evidence score, not a probability**, because `auto-trade` stores it in a
  field called `confidence` and the two must not be confused
* puts the full upstream decision in `metadata`, so `auto-trade`'s own audit log
  keeps the provenance
* calls `workflow.execute(signal)` and maps the `ExecutionResult` status per
  §3.8

`FakeTradeExecutor` records every submitted request in memory for tests. It
implements the same port, so a test asserting on recorded orders exercises the
real pipeline.

### 5.8 The MT5 adapter for account and symbol data

Following the `albrooks.adapters.mt5` design, because it is already proven:
dependency-injected `mt5_module`, a `Protocol` for the subset of the bindings
used, and no import of `MetaTrader5` outside that one module. It supplies
`AccountProvider` (balance, equity, currency) and `SymbolSpecProvider`
(contract size, tick size, tick value profit/loss, volume step/min/max, digits,
point, spread, trade mode, filling modes).

A direct consequence for the dependency strategy: `albrooks` is a hard
dependency of the bridge's MT5 adapter, and `auto-trade` is a hard dependency of
the execution adapter. Neither belongs in the domain layer, and neither is
imported by a test of the domain layer.

### 5.9 Ports

```python
class SignalSource(Protocol):
    def latest_signal(self, symbol: str, timeframe: str) -> Signal | None: ...

class MarketDataProvider(Protocol):
    def closed_bars(self, symbol: str, timeframe: str, count: int) -> BarSeries: ...

class AccountProvider(Protocol):
    def balance(self) -> Decimal: ...
    def equity(self) -> Decimal: ...
    def currency(self) -> str: ...

class SymbolSpecProvider(Protocol):
    def spec(self, symbol: str) -> SymbolSpec: ...

class TradeExecutor(Protocol):
    def submit(self, request: ExecutionRequest) -> ExecutionResult: ...

class IdempotencyStore(Protocol):
    def contains(self, key: str) -> bool: ...
    def record(self, key: str, record: Mapping[str, Any]) -> None: ...
```

Narrow by design. `AccountProvider` does not also return positions;
`SymbolSpecProvider` does not also return quotes. Interface segregation matters
most here, because these are the ports a test will fake, and a fat port means
every fake must implement every method.

---

## 6. Anti-corruption layer

```
AlBrooksSignalSource ──▶ albrooks.Adapter ──▶ internal Signal
                                                      │
                                    RiskService ◀────┤
                                    ValidationService │
                                                      ▼
                                            TradeDecision
                                                      │
                                                      ▼
ExecutionRequest ──▶ auto-trade.Adapter ──▶ ExecutionWorkflow ──▶ MT5 UI
```

The direction of the arrows is the point. `albrooks.Adapter` knows about
`AnalysisResult.decision` keys and `Action` values; nothing downstream of it does.
`auto-trade.Adapter` knows about `TradeSignal`, `OrderAction` and
`ExecutionStatus`; nothing upstream of it does. If either upstream project
renames a field or changes a vocabulary, one adapter changes.

The `albrooks` adapter reads the decision dict defensively with `.get()`, because
§2.4 shows the degenerate path has a different shape, and a strict key access
would raise on exactly the input that most needs a clean `NO_TRADE`.

---

## 7. Dependency strategy

Both upstreams are **private GitHub repositories, not published to PyPI.** The
options and the verdict:

| Option | Verdict |
|---|---|
| PyPI dependency | **Impossible** — neither is published, and they are proprietary |
| Git dependency (`git+ssh://…@tag`) | **Rejected as the default** — couples every install to network access, a tag that may move, and SSH keys. Fails badly on a second laptop at the worst moment |
| Git submodule | **Rejected** — pins a commit but makes `git clone --recursive` mandatory, doubles the checkout, and puts two projects' histories inside this one |
| **Editable local path, explicitly configured** | **Chosen.** Both upstreams are already `src/`-layout setuptools projects, so `pip install -e <path>` is exact, offline, and works identically on every machine |

The mechanism, in `pyproject.toml`:

```toml
[project.optional-dependencies]
albrooks = ["albrooks @ file:///E:/al-brooks-price-action-engine"]
auto-trade = ["auto-trade @ file:///E:/auto-trade"]
```

with those two lines **generated per machine** from environment variables rather
than committed with a hard-coded drive letter. Concretely, a
`scripts/setup.ps1` that reads `ALBROOKS_PATH` and `AUTO_TRADE_PATH` and rewrites
the extras block, plus `scripts/setup.sh` for Linux and macOS. `E:\` appears in
this file as documentation of the current development machine and nowhere in
application logic.

`albrooks` requires Python ≥3.10, `auto-trade` requires ≥3.11, so the bridge
requires **≥3.11**. `MetaTrader5` and `pywinauto` are Windows-only and live in
the bridge's `windows` extra, so the domain and its tests run on any platform.

**The bridge's own test suite must not require either upstream to be
installed.** Domain tests import nothing from `adapters/`. Adapter tests use the
`mt5_module` injection both upstreams already support. This is what makes the
suite runnable on a laptop where the upstreams have not been cloned yet, and it
is enforced by a test that asserts the domain package's import closure.

---

## 8. Observability

The brief names an event vocabulary. Mapped to the two logging systems that
actually exist:

* Bridge-level events — `SIGNAL_RECEIVED`, `SIGNAL_REJECTED`, `STOP_RESOLVED`,
  `TAKE_PROFIT_RESOLVED`, `RISK_CALCULATED`, `POSITION_SIZED`,
  `TRADE_VALIDATED`, `TRADE_REJECTED`, `DRY_RUN_COMPLETED`, `EXECUTION_RESULT`,
  `DUPLICATE_SUPPRESSED`, each with a stable `reason` code.
* Execution events — delegated to `auto-trade`'s `AuditLogger`, which already
  writes append-only rotating JSONL and already carries `signal_id`,
  `execution_id`, symbol, action, volume, state, error and verification evidence.

The bridge does not re-implement an audit log for execution. It writes its own
decision events and lets `auto-trade` own execution events, and the two are
correlated by `signal_id` — which is why §5.3's determinism matters operationally
and not just for dedup.

Every decision is traceable end to end:

```
source signal → signal_id → symbol → timeframe → bar index / bar time
→ entry → stop → stop_basis → TP → tp_source → risk % → risk amount
→ tick size → tick value → volume → validation result → execution result
```

That is §34 of the brief, and it is also the debugging story.

---

## 9. Safety invariants

These are the rules the code must make true, stated now so that a later phase
cannot quietly relax one:

1. **No valid stop ⇒ no trade.** There is no code path that synthesises a stop.
2. **A volume below `volume_min` is a refusal, never a floor-up.**
3. **Rounding to `volume_step` is always down.**
4. **Every rejection carries a stable reason code.** No rejection is expressed
   only as log prose.
5. **`UNKNOWN` execution is never auto-retried.**
6. **The bridge's dry run short-circuits before the executor is called**, so
   `auto-trade` configuration cannot cause an order during a bridge dry run.
7. **The bridge honours `FileKillSwitch` in its own decision path**, not only
   inside `auto-trade`.
8. **The default configuration cannot execute.** `execution_enabled` defaults to
   false, and turning it on is a separate, explicit, documented act.
9. **A stop on the wrong side of entry is refused locally**, because the
   downstream adapter does not check it (§3.5).
10. **Confidence is never described as a probability** — in code, in config names
    or in documentation.

---

## 10. What Phase 0 did not decide

Recorded honestly rather than guessed:

* The exact `signal_id` composition, pending Phase 9 against real engine output
  (§5.3). The tension between "same reading redelivered" and "recomputation moved
  the stop by a tick" is real and must be settled with data.
* Whether the MT5 account/symbol adapter extends `albrooks.adapters.mt5` or
  stands alone. Both are defensible; the decision depends on whether
  `albrooks` would accept a new public surface, which is the maintainer's call.
* Whether live MT5 validation runs against Alpari demo
  (`C:\Users\bagheri\AppData\Roaming\Alpari MT5\terminal64.exe`, data folder
  `…\MetaQuotes\Terminal\1BFBA8D123B04AAD5E48746348E9B594`). Not touched in
  Phase 0. `auto-trade`'s own control ids were measured on **Alpari build 6184**,
  so a build mismatch is a live risk for Phase 11.
* `minimum_signal_confidence` as a configuration name. The underlying quantity is
  an evidence score, not a probability (§2.6), so the name is likely to change to
  something like `minimum_evidence_score`.
