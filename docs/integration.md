# Integration Contracts — Upstream APIs

> **Read `docs/architecture.md` first.** This file is the reference the adapters
> will be written against: the exact upstream signatures, the exact data shapes,
> and the exact things that do not exist. Everything here is quoted from the real
> source of the two upstream repositories as of the audit.

---

## 1. `albrooks` — what the bridge calls

### 1.1 Import and construct

```python
from albrooks import Analyzer, AnalyzerConfig, Bar, BarSeries
from albrooks.engine.pipeline import analyze_multi_timeframe
from albrooks.decision.engine import Action
from albrooks.trade.plan import (
    STOP_BASIS_ATR,
    STOP_BASIS_NONE,
    TARGET_BASIS_MEASURED_MOVE,
    TARGET_BASIS_FADE_ORIGIN,
    TARGET_BASIS_SWING,
    BLOCKING_ISSUES,
)
from albrooks.adapters.mt5 import MT5Feed, M15, M30, H1, H4, TIMEFRAMES
```

`albrooks.__init__` exports exactly six names. Everything else must be imported
from its module.

`Analyzer()` takes an optional `AnalyzerConfig` and an optional `SetupRegistry`.
It builds a fresh default registry per instance; `DEFAULT_REGISTRY` is
deliberately not read, so behaviour does not depend on import order.

### 1.2 `Analyzer.analyze` — the only call the signal adapter needs

```python
def analyze(
    self,
    bars: Sequence[Bar | dict[str, Any]] | BarSeries,
    symbol: str = "GENERIC",
    timeframe: str = "UNKNOWN",
    last_closed: int | None = None,
) -> AnalysisResult: ...
```

Behaviour that the adapter depends on:

* `last_closed=None` means the newest bar; a value past the end is **clamped**,
  not an error.
* Degenerate input returns `self._empty(series, reason)` — it does **not** raise.
  `reason` is `NO_BARS`, `NEGATIVE_LAST_CLOSED` or `ATR_UNAVAILABLE`.
* `bars` accepts `Bar` objects, dicts (long or short keys) or a `BarSeries`.

### 1.3 `Bar` and `BarSeries`

```python
@dataclass(frozen=True, slots=True)
class Bar:
    time: float  # bar OPEN time, Unix epoch SECONDS
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    index: int = 0
```

`__post_init__` raises `ValueError` if `high < low`, if open or close is outside
`[low, high]`, or if `volume < 0`. **It does not reject non-positive prices.**

```python
@dataclass(frozen=True)
class BarSeries:
    bars: tuple[Bar, ...]
    symbol: str = "GENERIC"
    timeframe: str = "UNKNOWN"
```

Indexing is oldest-first, and the engine assumes it throughout. `BarSeries`
registers as a `collections.abc.Sequence`, so it is a valid
`Sequence[Bar | dict[str, Any]]`. Slicing returns a `BarSeries`.

Timestamps are `float` Unix seconds. **There is no `datetime`, no timezone type
and no ISO-8601 parsing anywhere in the library.** Any conversion to a
timezone-aware `datetime` happens in the bridge, and the bridge owns the decision
of which timezone a signal is stamped in.

### 1.4 `AnalysisResult` — the fields the adapter reads

```python
@dataclass(frozen=True)
class AnalysisResult:
    symbol: str
    timeframe: str
    bars_processed: int
    last_closed_bar: int = -1
    market_state: dict[str, Any] = field(default_factory=dict)
    swings: list[dict[str, Any]] = field(default_factory=list)
    legs: list[dict[str, Any]] = field(default_factory=list)
    patterns: list[dict[str, Any]] = field(default_factory=list)
    measured_moves: list[dict[str, Any]] = field(default_factory=list)
    setups: list[dict[str, Any]] = field(default_factory=list)
    trade_plans: list[dict[str, Any]] = field(default_factory=list)
    decision: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    bar_features: list[dict[str, Any]] = field(default_factory=list)
    trends: list[dict[str, Any]] = field(default_factory=list)
    channels: list[dict[str, Any]] = field(default_factory=list)
    pullbacks: list[dict[str, Any]] = field(default_factory=list)
    breakouts: list[dict[str, Any]] = field(default_factory=list)
    reversals: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    detectors: dict[str, Any] = field(default_factory=dict)
    layers: dict[str, bool] = field(default_factory=dict)
```

### 1.5 `AnalysisResult.decision` — **two different shapes**

This is the single most important gotcha in the integration.

**Normal path** — 13 keys, `Decision.to_dict()`:

| Key | Type | Notes |
|---|---|---|
| `action` | `str` | `BUY` \| `SELL` \| `WAIT` \| `NO_TRADE` |
| `reason` | `str` | stable code |
| `subject` | `str` | `"<detector>#<position>"`; `""` on abstentions |
| `direction` | `int` | `+1` / `-1` / `0` |
| `plan` | `dict \| None` | `TradePlan.to_dict()`; **`None` on abstentions** |
| `evidence` | `dict` | `EvidenceScore.to_dict()` plus `ppts` |
| `explanation` | `list[str]` | ordered prose |
| `vetoes` | `list[dict]` | each a `Veto.to_dict()` |
| `considered` | `dict` | counts |
| `ranking_basis` | `list[str]` | the three ranking criteria |
| `sides` | `dict[str, float]` | `bull_ppts`, `bear_ppts` |
| `is_actionable` | `bool` | `action in ("BUY","SELL")` |
| `is_probability` | `bool` | always `False` |

**Degenerate path** (`Analyzer._empty`) — **3 keys only**:

```python
decision = {
    "action": "NO_TRADE",
    "reason": reason,  # NO_BARS | NEGATIVE_LAST_CLOSED | ATR_UNAVAILABLE
    "note": "No analysis was run; see market_state.reason.",
}
```

**Adapter rule: every key except `action` is read with `.get()`.** The engine's
own serialiser does exactly this.

### 1.6 `decision["plan"]` — `TradePlan.to_dict()`

```python
{
    "subject": str,
    "direction": int,
    "entry": float,
    "stop": float,
    "target": float,
    "entry_basis": str,
    "stop_basis": str,
    "target_basis": str,
    "entry_reference": float,
    "stop_reference": float,
    "target_reference": float,
    "bar_index": int,
    "signal_bar": int,
    "risk": float,
    "reward": float,
    "reward_to_risk": float,
    "risk_to_reward": float,
    "is_valid": bool,
    "has_structural_stop": bool,
    "invalidation": str,
    "management": list[str],
    "issues": list[str],
    "warnings": list[str],
    "evidence_score": float | None,
    "is_recommendation": False,
}
```

`risk` is `abs(entry - stop)` — never negative, even for a stop on the wrong
side. `reward_to_risk` is `0.0` when `risk == 0` (never `inf`).
`is_valid` is `not any(issue in BLOCKING_ISSUES for issue in issues)` and is
arithmetic, not approval.

> **The bridge reads none of `risk`, `reward`, `reward_to_risk` or
> `risk_to_reward`.** The adapter's mapped decision fields are `action`, `reason`,
> `subject`, `direction`, `plan` and `evidence`; the plan keys it reads are
> `entry`, `stop`, `target`, `stop_basis`, `target_basis` and `entry_basis`.
>
> The engine does *rank* candidates partly on `reward_to_risk`, so discarding it
> loses one line of diagnostic detail — a decision record cannot say the engine
> chose this candidate partly on its own ratio. That is accepted on purpose: the
> engine documents its own targets as unvalidated, and the bridge computes the
> same ratio itself from distances it has already validated. Recorded here because
> it looks like an oversight otherwise, and because a future upstream field that
> *is* ratio-driven should be a decision rather than an accident.

`BLOCKING_ISSUES` = `NO_DIRECTION`, `NO_ATR`, `ENTRY_UNDEFINED`,
`STOP_UNDEFINED`, `STOP_NOT_PROTECTIVE`, `TARGET_UNDEFINED`, `TARGET_NOT_AHEAD`,
`RISK_NOT_POSITIVE`.

### 1.7 Stop and target bases — the policy input

`stop_basis` ∈ `PULLBACK_EXTREME` | `BREAKOUT_REFERENCE` | `PATTERN_EXTREME` |
`SWING` | `ATR_FALLBACK` | `NONE`

`has_structural_stop` is `stop_basis not in ("ATR_FALLBACK", "NONE")`.

`target_basis` ∈ `MEASURED_MOVE` | `FADE_ORIGIN` | `SWING` | `ATR_FALLBACK` |
`NONE`

`entry_basis` ∈ `SETUP_REFERENCE` | `LAST_CLOSE` | `NONE`

**These are absolute prices, never distances.** No conversion is needed. The
adapter still handles a distance defensively, in case a future engine version
emits one, and that path is covered by a test.

### 1.8 Vetoes and reasons — for diagnostics, not for gating

11 veto codes: `NO_DIRECTION`, `NO_OWN_EVIDENCE`, `INVALID_GEOMETRY`,
`TERMINAL_SETUP`, `NO_ATR`, `EVIDENCE_TOO_WEAK`, `RISK_REWARD_TOO_LOW`,
`TRADE_IS_LATE`, `TOO_MANY_FAILED_ATTEMPTS`, `AGAINST_HIGHER_TIMEFRAME`,
`VOLATILITY_STOP_ONLY` (the only non-blocking one).

Decision reasons: `DECISION_DISABLED`, `NO_ANALYSIS`, `NO_CANDIDATES`,
`ALL_CANDIDATES_VETOED`, `EVIDENCE_CONFLICT`, `RANKED_CANDIDATE`, `AGAINST_HTF`.

Analyzer reasons for degenerate input: `NO_BARS`, `NEGATIVE_LAST_CLOSED`,
`ATR_UNAVAILABLE`.

The bridge **records** these; it does not re-derive them. Its own rejection
reasons are a separate vocabulary (§1.10), because the engine's reasons describe
the *reading* and the bridge's describe the *trade*.

### 1.9 `MT5Feed` — reusable for bars and for account data

```python
class MT5Feed:
    def __init__(self, mt5_module: Any | None = None) -> None: ...

    def connect(
        self, symbol: str | None = None, *, path: str | None = None,
        login: int = 0, timeout_ms: int = 60_000,
    ) -> None: ...
    def shutdown(self) -> None: ...
    def __enter__(self) -> "MT5Feed": ...
    def __exit__(self, *exc: object) -> None: ...
    def select_symbol(self, symbol: str) -> None: ...
    def bars(self, ...) -> ...
    def closed_bars(self, ...) -> FrozenSeries: ...
    def frozen_bars(self, *args, **kwargs) -> FrozenSeries: ...
    @staticmethod
    def period(timeframe: int) -> float: ...
    def server_time(self, symbol: str | None = None) -> float: ...
```

Documented usage, from the class docstring:

```python
feed = MT5Feed()
feed.connect("EURUSD", path=r"C:\\...\\terminal64.exe")
frozen = feed.closed_bars("EURUSD", M15, 300)
result = AnalysisSession().on_bars(frozen.series, freeze=frozen.freeze)
```

Three properties make this the right thing to build the bridge's MT5 adapter on:

1. `mt5_module` is injectable with the same shape as the real bindings — so every
   branch is testable without a terminal, and `MetaTrader5` need not appear in
   any test file.
2. `path` is a parameter, never a constant. Nothing launches the terminal; it
   must already be running and logged in.
3. It never imports `MetaTrader5` outside `albrooks/adapters/mt5/`, which a CI
   gate enforces.

Errors: `MT5AdapterError` (base), `MT5Unavailable`, `SymbolNotFound`,
`HistoryUnavailable`, `TimeframeUnsupported`. All live in
`albrooks.adapters.mt5.errors`.

### 1.10 Timeframes

```python
TIMEFRAMES: dict[str, int]  # "M1".."MN1" -> ENUM_TIMEFRAMES values
canonical_name(timeframe: int) -> str        # 16388 -> "H4", else "UNKNOWN(<n>)"
period_seconds(timeframe: int) -> float      # raises TimeframeUnsupported for MN1
timeframe_from_name(name: str) -> int        # case-insensitive; raises on unknown
```

Constants: `M1 M2 M3 M4 M5 M6 M10 M12 M15 M20 M30 H1 H2 H3 H4 H6 H8 H12 D1 W1 MN1`.
Hour frames are `16384 + n`, `W1` is `32769`, `MN1` is `49153`.

The **core** uses a free-form `str` for timeframe and measures the bar step from
timestamps rather than parsing the label. The bridge should hold the label and
resolve it through `timeframe_from_name` when it needs a period.

### 1.11 Multi-timeframe (optional, recommended)

```python
def analyze_multi_timeframe(
    lower: Sequence[Bar | dict[str, Any]] | BarSeries,
    higher: Sequence[Bar | dict[str, Any]] | BarSeries,
    *,
    ratio: int = 0,
    config: AnalyzerConfig | None = None,
    symbol: str = "GENERIC",
    ...
) -> MultiTimeframeResult: ...
```

`MultiTimeframeResult` has `.lower`, `.higher` (may be `None`), `.bias`
(`HTFBias`), `.decision`, `.symbol`, `.lower_timeframe`, `.higher_timeframe`.

`apply_htf_veto` turns a `BUY`/`SELL` into `WAIT` with reason `AGAINST_HTF` when
the higher timeframe opposes it strongly. **Withheld, never reversed.** A
`NO_TRADE` or `WAIT` decision is returned untouched.

### 1.12 Exceptions

`albrooks.adapters.mt5.errors`:
`MT5AdapterError`, `MT5Unavailable`, `SymbolNotFound`, `HistoryUnavailable`,
`TimeframeUnsupported`.

Registry errors in `albrooks.setups.registry`: `RegistryError`,
`DuplicateDetectorError`, `UnknownDetectorError`.

The engine core **does not define domain exceptions**; it uses `ValueError` in
`Bar.__post_init__` and returns explanatory results rather than raising. The
adapter must therefore not rely on catching engine exceptions to detect bad
input — it must inspect the result.

### 1.13 Test and tooling facts

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "-v --strict-markers"
# NOTE: no pythonpath key here — see the defect below.
```

**Defect, verified.** In `albrooks`'s `pyproject.toml`, `pythonpath = ["src"]`
sits at the top level *after* `[project.optional-dependencies]` and *before*
`[tool.setuptools.packages.find]`, so it is parsed as **an extra requirement
group named `pythonpath`**, not as pytest config. Generated metadata confirms:
`Provides-Extra: pythonpath` and `Requires-Dist: src; extra == "pythonpath"`.
`tests/conftest.py` inserts `src` on `sys.path` manually, which is the only
reason the suite imports.

* Ruff: line length 100, target py310, rules `E,F,W,I`.
* Mypy: py310, `disallow_untyped_defs`, `check_untyped_defs`,
  `ignore_missing_imports` override for `MetaTrader5` only.
* CI (`.github/workflows/ci.yml`): lint, mypy, pytest, two gate scripts, the MQL5
  parity harness, build. Live-terminal tests **skip** without a terminal; the
  repository's `HANDOFF.md` states that **a skip is not a pass**.

---

## 2. `auto_trade` — what the bridge calls

### 2.1 Import

`auto_trade/__init__.py` is one line with no re-exports. Import from submodules.

```python
from auto_trade.domain.models import (
    AccountSnapshot,
    AuditEvent,
    ExecutionPolicy,
    ExecutionResult,
    OrderRequest,
    RiskLimits,
    TerminalProfile,
    TradeSignal,
    VerificationEvidence,
)
from auto_trade.domain.enums import ExecutionStatus, ExecutionState, OrderAction, AccountType
from auto_trade.domain.exceptions import (
    AutoTradeError,
    AutomationError,
    AutomationRejectedError,
    AutomationTimeoutError,
    ExecutionUnknownError,
    InvalidSignalError,
    NoSignalAvailable,
    SafetyViolation,
    SignalSourceError,
    TerminalNotFoundError,
    PositionSnapshotUnavailable,
)
from auto_trade.domain.protocols import KillSwitch, TradingTerminalAdapter
from auto_trade.application.workflow import ExecutionWorkflow
from auto_trade.application.risk import RiskEngine
from auto_trade.application.ledger import ExecutionLedger, JsonExecutionLedger, LedgerError
from auto_trade.application.kill_switch import FileKillSwitch
from auto_trade.infrastructure.logging import AuditLogger
from auto_trade.infrastructure.configuration import AppConfig, load_env_file
from auto_trade.infrastructure.terminal import WindowsTerminalDiscovery
from auto_trade.infrastructure.automation import MT5DesktopAdapter
from auto_trade.adapters import DryRunTerminalAdapter, UnavailableMT5DesktopAdapter
```

### 2.2 `ExecutionWorkflow` — the single entry point the bridge wraps

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

Entirely synchronous. Returns an `ExecutionResult`, never a ticket and never a
dict. Its sequence, which the bridge inherits and must not bypass:

1. record `received` audit event
2. `RiskEngine.validate` → on rejection, `REJECTED` / `INVALID_SIGNAL`
3. kill switch check → `SafetyViolation` → `REJECTED`
4. demo-only check against `AccountSnapshot.account_type`
5. `adapter.select_symbol` → `OrderRequest(signal)` → `adapter.prepare_order`
6. `ledger.record_attempt`
7. if `policy.dry_run` → `DRY_RUN` / `DRY_RUN_COMPLETED` and **return**
8. `adapter.execute_order` → `REQUESTED` on success
9. `adapter.verify_execution` → `ACCEPTED` only on independent observation

Any exception is mapped to a status, and the mapping is deliberate: an
`AutomationError` while still preparing the dialog is `REJECTED` (nothing was
clicked), while the same error during `EXECUTING` is `UNKNOWN` (something may
have been clicked). `ExecutionUnknownError` is never downgraded to a refusal.

### 2.3 `TradeSignal` — the exact input to build

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

Own validation: `id` non-empty and passing `validate_signal_id`; `source`
non-empty; `symbol` non-empty; `volume > 0`; `0 <= confidence <= 1`. It
upper-cases the symbol and converts the timestamp to UTC.

`from_dict` required keys: `id`, `timestamp`, `source`, `symbol`, `action`,
`volume`.

`to_dict()` serialises `volume`, `price`, `stop_loss` and `take_profit` as
**strings** — a detail that matters if the bridge ever round-trips through JSON.

### 2.4 `OrderRequest` — what the adapter actually receives

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

**No order type, no deviation, no magic number, no expiration, no filling mode,
no time mode.** `price` and `order_type` are carried and never used.

`OrderAction` defines `BUY, SELL, BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP,
CLOSE, MODIFY, CANCEL`, but `ALLOWED_ACTIONS = {BUY, SELL}`. The other seven
raise `AutomationRejectedError("… only BUY and SELL are implemented")`.

**Therefore: the bridge targets market orders only.** Its documentation must say
so, because a caller reading only the bridge's interface would otherwise assume
limit orders are available.

### 2.5 `ExecutionResult` — the output to map

```python
class ExecutionResult:
    def __init__(
        self,
        execution_id: str,
        signal_id: str,
        status: ExecutionStatus,
        state: str,
        message: str,
        order_reference: str | None = None,  # the POSITION ticket
        error: str | None = None,
        evidence: VerificationEvidence | None = None,
    ) -> None: ...
```

| `ExecutionStatus` | Meaning | Bridge treatment |
|---|---|---|
| `ACCEPTED` | position independently observed | success, carry `order_reference` |
| `REQUESTED` | control clicked, not observed | **not success**; report unconfirmed |
| `DRY_RUN` | validated, control not used | decision succeeded, no trade |
| `REJECTED` | refused or gate closed | failure, carry the message |
| `UNKNOWN` | may have been used, unproven | record and escalate; **never auto-retry** |
| `CLOSED` | position closed | out of scope for signal handling |

`ExecutionState` has 25 members; the ones reachable from `execute` are
`SIGNAL_RECEIVED, VALIDATING, VALIDATED, INVALID_SIGNAL, LOCATING_TERMINAL,
PREPARING_UI, ORDER_READY, EXECUTING, EXECUTION_DETECTED, VERIFYING, SUCCESS,
DRY_RUN_COMPLETED, ORDER_REJECTED, VERIFICATION_FAILED, UNKNOWN_EXECUTION,
UNKNOWN_STATE, TERMINAL_NOT_FOUND, TIMEOUT`.

### 2.6 The click-is-not-a-fill contract

`MT5DesktopAdapter.execute_order` returns `REQUESTED` with the message
`"final control used for {action} {symbol} {volume}; acceptance not yet
observed"`. `ACCEPTED` comes only from `verify_execution`, which polls an
independently observed position list — read from an MQL5 indicator's JSON — and
accepts only if **exactly one** new position matches symbol, side and volume.

`execute_order` refuses, in order: the gate refusal, an unsupported action, a
missing position baseline, a **baseline that changed between preparing the order
and the click**, and a dialog whose re-read fields do not match what the risk
engine approved.

### 2.7 `RiskEngine` — operational limits, not money at risk

```python
class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None: ...

    def validate(
        self,
        signal: TradeSignal,
        account: AccountSnapshot | None = None,
        seen_signal_ids: Iterable[str] = (),
        recent_executions: Iterable[datetime] = (),
        now: datetime | None = None,
    ) -> RiskDecision: ...
```

Seven rejections, in evaluation order:

| # | Reason string | Condition |
|---|---|---|
| 1 | `duplicate signal id` | `signal_id in seen_signal_ids` |
| 2 | `symbol is not allowed` | not in `limits.allowed_symbols` |
| 3 | `volume exceeds configured limit` | `volume <= 0` or `> limits.max_volume` |
| 4 | `signal is expired` | `now >= expiration` |
| 5 | `order rate limit reached` | ≥ `max_orders_per_minute` in the last minute |
| 6 | `account is not connected` | `account is not None and not account.connected` |
| 7 | `maximum open positions reached` | `max_open_positions is not None and open_positions >= it` |

**There is no balance, no percentage and no monetary exposure check.**
`max_open_positions` has no env var and no config wiring, and
`AccountSnapshot.open_positions` is never populated by `auto-trade`'s own adapter,
so **gate 7 is inert inside `auto-trade`.** It is not inert in the bridge: the
bridge populates the same field from the terminal's published position snapshot, so
`BRIDGE_MAX_OPEN_POSITIONS` is the enforced one. The bridge must treat a
`RiskEngine` rejection as an expected outcome, not a fault.

### 2.8 `ExecutionLedger` — reuse, do not rebuild

```python
class ExecutionLedger(Protocol):
    def contains(self, signal_id: str) -> bool: ...
    def record_attempt(self, signal_id: str, execution_id: str) -> None: ...
    def record_result(self, result: ExecutionResult) -> None: ...
    def records(self) -> tuple[dict[str, Any], ...]: ...


class JsonExecutionLedger:
    def __init__(self, path: Path) -> None: ...
    # plus, not in the protocol:
    def pending(self) -> tuple[dict[str, Any], ...]: ...
    def reconcile(self, signal_id: str, observation: str) -> dict[str, Any]: ...
```

`JsonExecutionLedger` is durable, cross-process and atomic (writes `.tmp`, then
`replace`). `ExecutionWorkflow` consults it via
`seen_signal_ids={signal_id} if ledger.contains(signal_id) else self.seen_signal_ids`,
so a re-delivered `signal_id` is rejected with `duplicate signal id`.

**This only works if the bridge's `signal_id` is deterministic across restarts.**
A fresh UUID per call would defeat the entire mechanism, because the ledger's key
would never match. This is the reason §5.3 of the architecture document is not an
optional refinement.

### 2.9 `ExecutionGate`, `FileKillSwitch`, `AppConfig`

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

Field automation ids, measured on **Alpari MT5 build 6184**:
`BUY_BUTTON_ID = "10408"`, `SELL_BUTTON_ID = "10409"`, `SYMBOL_FIELD_ID =
"10325"`, `VOLUME_FIELD_ID = "10333"`, `STOP_LOSS_FIELD_ID = "10334"`,
`TAKE_PROFIT_FIELD_ID = "10336"`. The comment field is `"1001"`, hard-coded
inline and not covered by `terminal-check`.

`CloseGate` mirrors `ExecutionGate` for closing, behind a separate
`AUTO_TRADE_ENABLE_CLOSE` opt-in.

`AppConfig.from_env()` reads 23 variables:

| Variable | Default |
|---|---|
| `AUTO_TRADE_TERMINAL_PATH` | `C:\Program Files\Alpari MT5_2\terminal64.exe` |
| `AUTO_TRADE_DATA_PATH` | `C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal\AF19E…` |
| `AUTO_TRADE_SIGNAL_DIR` | `signals` |
| `AUTO_TRADE_LOG_DIR` | `logs` |
| `AUTO_TRADE_ALLOWED_SYMBOLS` | `EURUSD,XAUUSD,YM` |
| `AUTO_TRADE_INSTANCE_NAME` | `Alpari-MT5-Demo` |
| `AUTO_TRADE_DRY_RUN` | `true` |
| `AUTO_TRADE_DEMO_ONLY` | `true` |
| `AUTO_TRADE_CONFIRMATION` | `SINGLE_CONFIRMATION` |
| `AUTO_TRADE_ENABLE_EXECUTION` | `false` |
| `AUTO_TRADE_ENABLE_CLOSE` | `false` |
| `AUTO_TRADE_STRATEGY` | `""` |
| `AUTO_TRADE_API_URL` / `AUTO_TRADE_API_TOKEN` | `""` |
| `AUTO_TRADE_HTTP_SIGNAL_URL` / `_TOKEN` | `""` |
| `AUTO_TRADE_PIPE_SIGNAL_NAME` / `_TOKEN` | `""` |
| `AUTO_TRADE_WS_SIGNAL_URL` / `_TOKEN` | `""` |
| `AUTO_TRADE_MAX_VOLUME` | `1.0` |
| `AUTO_TRADE_MAX_ORDERS_PER_MINUTE` | `5` |
| `AUTO_TRADE_SIGNAL_EXPIRATION_SECONDS` | `10` |
| `AUTO_TRADE_ENV_FILE` | read by `load_env_file_with_origin`, not by `from_env` |

`AppConfig.live_execution_refusal()` refuses live execution when
`AUTO_TRADE_ENABLE_EXECUTION=true` came from an untrusted `.env` in the current
working directory.

**The bridge must not read any of these.** They are `auto-trade`'s configuration

### 2.10 `MT5BridgeSignalProvider` — a possible alternative path

Reads `auto_trade_signal_*.json` from `<data path>\MQL5\Files`, single-shot pull
via `receive()`, deletes each file after parsing, raises `NoSignalAvailable` when
idle. The JSON schema is exactly `TradeSignal.from_dict`'s.

This is a viable decoupling for a later phase: the bridge writes a signal file
and `auto-trade` picks it up, with no shared process. It is **not** the Phase 7
design, because the brief asks the bridge to call the execution layer, and
because this provider is currently exported but not wired into `AppConfig` or
any CLI command. Recorded as an option for Phase 10+.

### 2.11 `AuditLogger`

Append-only rotating JSONL, taking `AuditEvent`s. `AuditEvent` carries
`component, event_type, message, signal_id, execution_id, symbol, action, volume,
state, error, evidence` and a `to_dict()`. The bridge does not re-implement an
execution audit log; it correlates with it by `signal_id`.

### 2.12 Strategy plugin seam

`AUTO_TRADE_STRATEGY=package.module:attribute` → `load_strategy(spec)`. A file in
`auto-trade`'s `strategies/` directory is importable without installing anything.
A strategy *proposes*; `auto-trade` still decides. This is the documented inbound
advice path, distinct from the outbound execution path the bridge uses.

---

### 2.13 The `AutoTradePositionReader` snapshot — where `open_positions` comes from

**This section was written in Phase 7 from the indicator's source
(`mql5/Indicators/AutoTradePositionReader.mq5`) and confirmed against a live
terminal.** It is not a transcription; it is the contract the bridge depends on.

Two files under `<data path>\MQL5\Files`:

```
auto_trade_positions_a.json
auto_trade_positions_b.json
```

**Only one of them normally exists.** The writer picks the target by
`sequence % 2` and then **deletes the other one**. The two-file state exists only
in the window between truncating the target and deleting its predecessor — and
that window is the hazard, because `FileOpen(..., FILE_WRITE)` truncates before
it writes. So:

* **never** treat "both files present" as normal;
* a missing file is not an error while the other parses;
* a **parse** failure on one file is the signal to fall back to the other;
* the write happens about **once per second**, so a **30-second** freshness limit
  is thirty consecutive missed writes. `auto-trade`'s own reader uses the same
  figure, and the two constants are asserted equal by a test — they must agree, or
  one terminal looks alive to one reader and dead to the other.

Top level:

| Field | Type | Note |
|---|---|---|
| `schema` | `int` | `1`. Anything else is refused. |
| `sequence` | `int` | Monotonic. The reader picks the highest it can parse. |
| `complete` | `bool` | Always written as the literal `true`. The check is defensive. |
| `written_at` | ISO-8601 `Z` | Compared against an injected clock with a 30 s allowance. |
| `account` | login | The bridge cross-checks this against `account_info().login`. |
| `server` | `str` | e.g. `Alpari-MT5-Demo`. Recorded, not enforced. |
| `terminal_build` | `int` | The bridge reads and logs it. It is a load-bearing fact. |
| `positions` | `list` | Empty on an idle account. |

Each entry, **as written by the indicator** — not as this bridge models it:

| Field | Type |
|---|---|
| `ticket`, `magic`, `symbol` | `int`, `int`, `str` |
| `type` | `"BUY"` or `"SELL"` |
| `volume`, `price_open`, `sl`, `tp`, `profit` | float |
| `opened_at` | ISO-8601 `Z` |

**Only `ticket`, `symbol`, `type` and `volume` are modelled here** — the four
`auto-trade`'s verifier matches on. The rest stay in `ObservedPosition.raw` and are
deliberately absent from the model, so nothing can come to depend on a field
nobody has thought about. `type` is mapped `"BUY" → LONG`, `"SELL" → SHORT`.

**Two parsing rules that are not obvious:**

* **`parse_float=Decimal`.** `volume` is matched by `auto-trade`'s verifier on exact
  `Decimal` equality. Round-tripping through `float` first would drift, and the
  drift would fail the verification of a trade that actually succeeded.
* **A malformed entry refuses the whole snapshot** rather than being skipped.
  Skipping under-counts, and an under-count admits a trade the concurrency gate
  should have refused.

### 2.14 `ExecutionWorkflow` -- what the bridge calls, and what it does not

**Written in Phase 7b by reading the source, not from the Phase 0 transcription.**
The checkout is at `D:\Projects\auto-trade` and is installed editable, so
`tests/unit/test_auto_trade_executor.py` now exercises the **real** workflow,
the **real** `RiskEngine` and a real `DryRunTerminalAdapter`.

The exact constructor, from `application/workflow.py:32-55`:

``
ExecutionWorkflow(
    adapter: TradingTerminalAdapter,
    risk_engine: RiskEngine,
    profile: TerminalProfile,
    policy: ExecutionPolicy,
    kill_switch: KillSwitch,
    audit: Callable[[AuditEvent], None],
    now: Callable[[], datetime] | None = None,
    ledger: ExecutionLedger | None = None,
)
``

and `execute(signal: TradeSignal) -> ExecutionResult`.

**Three things a caller must know and would otherwise get wrong.**

1. **`execute` is *not* exception-free.** For expected failures it converts
   exceptions into a result -- but around the click its `except Exception`
   **re-raises** `ExecutionUnknownError`, so that one *escapes*. An adapter
   that assumed a return would crash on exactly the outcome that most needs a
   record. The bridge catches broadly and maps everything to `UNKNOWN`.
2. **`profile` is required and never read.** The field appears once in the whole
   class, in the assignment at line 46, and upstream's own tests pass
   `type("Profile", (), {})()` -- an object with no attributes at all. The
   bridge passes `SENTINEL_PROFILE` for the same reason, rather than fabricating
   plausible-looking terminal attributes nobody has established.
3. **There is no factory.** No `build_workflow`, no `create_workflow`. The only
   assembly in upstream is two private CLI functions, `_dry_run_workflow` and
   `_execute_live` in `cli/main.py`. So the bridge's composition root has to
   build the workflow itself -- which is why `AutoTradeExecutor` takes an
   already-assembled one and refuses to construct its own.

### 2.14.1 The dry run has two independent guards

Both are needed, and dropping either is a real hole:

* **the workflow refuses to call `execute_order`** when `policy.dry_run` --
  `workflow.py:102-110`, returning `DRY_RUN` / `DRY_RUN_COMPLETED` with the
  message exactly `"validated; final execution control not used"`. The branch is
  *after* `select_symbol` and `prepare_order`, so a dry run does exercise the
  real order dialog up to the final control.
* **the adapter closes the dialog** when the gate says dry-run --
  `MT5DesktopAdapter.prepare_order` calls `window_manager.close_order_dialog()`
  at `window_manager.py:644-645`.

The CLI's dry-run path passes `ExecutionGate(enabled=False, dry_run=True)`
regardless of configuration, so the opt-in is not even consulted. **The bridge
keeps no way to reach either guard**, and a test fails if this repository's
adapter module names `MT5DesktopAdapter` or `ExecutionGate` at all.

### 2.14.2 The five gates `RiskEngine` applies, in order

Refusal messages are fixed strings, so they are usable as assertions:
`"duplicate signal id"`, `"symbol is not allowed"`, `"volume exceeds
configured limit"`, `"signal is expired"`, `"order rate limit reached"`,
`"account is not connected"`, `"maximum open positions reached"`.

**There is no balance, no percentage and no monetary exposure check.**
`max_open_positions` has no env var and no config wiring, and
`AccountSnapshot.open_positions` is never populated by `auto-trade`'s own
adapter, so **gate 7 is inert inside `auto-trade`.** It is not inert in the
bridge: the bridge populates the same field from the terminal's published position
snapshot, so `BRIDGE_MAX_OPEN_POSITIONS` is the enforced one. The bridge must
treat a `RiskEngine` rejection as an expected outcome, not a fault.

### 2.14.3 The six statuses, and why the sixth matters

| Situation | Status | State |
|---|---|---|
| risk engine refused | `REJECTED` | `INVALID_SIGNAL` |
| kill switch active | `REJECTED` | `INVALID_SIGNAL` |
| `demo_only` and account not DEMO | `REJECTED` | `INVALID_SIGNAL` |
| terminal not found | `REJECTED` | `TERMINAL_NOT_FOUND` |
| UI/broker timeout | `REJECTED` | `TIMEOUT` |
| broker rejection | `REJECTED` | `ORDER_REJECTED` |
| gate refusal from the adapter | `REJECTED` | `GATE_REFUSED` |
| **dry run** | `DRY_RUN` | `DRY_RUN_COMPLETED` |
| verification did not prove a fill | `UNKNOWN` | `VERIFICATION_FAILED` |
| `execute_order` raised after the click | `UNKNOWN` | `UNKNOWN_EXECUTION` *(raised, not returned)* |
| verified single new matching position | `ACCEPTED` | `SUCCESS` |
| position closed | `CLOSED` | `POSITION_CLOSED` -- **never by the workflow**, only by `close_position` |

**`CLOSED` is mapped to `UNKNOWN`, and that is the load-bearing decision.**
The bridge knows five statuses; upstream has six. A closed position means the order
*was* placed and later closed, so re-sending is the duplicate rule 5 forbids --
whereas `REJECTED` says "definitely not placed", which is a licence to retry. The
bridge never closes, so this is a status it should never see; it is handled rather
than ignored because a status arriving from nowhere is the signal that something
upstream changed.

### 2.14.4 Two fields that do not exist, and must not be invented

* **No fill price.** `VerificationEvidence` carries `baseline`, `observed`
  and `position_id` -- references to the snapshots it compared -- and nothing
  else. A click is not a fill, so `execute_order` returns `REQUESTED`, never
  `ACCEPTED`; only a verified single new matching position yields `ACCEPTED`.
  So `ExecutionResult.executed_price` is always `None` in the bridge. Putting
  the requested price there would be the most confident-looking fabrication in the
  adapter.
* **No `confidence` for an evidence score.** `TradeSignal.confidence` is
  validated as `0..1` and downstream code would reasonably read it as a
  probability. The bridge's `evidence_score` is explicitly *not* one. It travels
  in `metadata` under its own name, so a field typed as a probability cannot
  carry a score that is not one.


---

## 3. Gaps the bridge must fill — consolidated

| Need | Why neither upstream can supply it |
|---|---|
| Account balance, equity, currency | `albrooks` has no account layer; `auto-trade` reads nothing numeric from the terminal — its `AccountSnapshot` has 3 fields and `connect()` hard-returns `AccountType.DEMO` |
| Symbol specification | `albrooks`: symbol is an opaque `str`. `auto-trade`: `SymbolInfo` is dead code with 3 fields; `trade_contract_size`, `volume_step`, `trade_tick_value` appear **nowhere** in `src/`, `docs/` or `mql5/` |
| Position sizing | No occurrence of a sizing or lot-calculation routine in either tree; `auto-trade` treats volume as an opaque `Decimal` with a `> 0` check |
| Stop side-of-entry validation | `albrooks` reports it as a plan issue; `auto-trade` does not check it and lets the broker refuse |
| Stop-level / freeze-level / digits rounding | Not present in either project |
| Structural logging for decisions | Neither has a decision-level event stream (`auto-trade`'s `AuditLogger` is execution-scoped) |
| Deterministic `signal_id` | `auto-trade`'s ledger is keyed on it, but nothing upstream produces one — the engine has no signal identity at all |

**Three of these rows are now closed, and it is worth saying how:**

* *Account balance* and *Symbol specification* are supplied by the bridge's own
  `adapters/mt5/` — Phase 7.
* *Open position count* was on this list in an earlier draft and **no longer is.**
  `account_info()` has no count, which is why Phase 7 first reported zero; but
  `auto-trade`'s own `AutoTradePositionReader` indicator publishes the positions as
  JSON, so the answer exists — see §2.13. The bridge reads it rather than adding
  `positions_get()`, because a published snapshot is readable without the Python
  bindings and does not depend on a call whose failure is easy to swallow.

**And one thing that was never a gap but was nearly built as one:** there is no
row for "an execution path", because `auto-trade` has always had one. What the
bridge needed was not a second one — it was a way to reach the existing one
*without skipping its safety envelope*. Calling `TradingTerminalAdapter` directly
would have skipped the risk engine, the kill switch, the ledger, the state machine
and the audit log, which is the failure this architecture exists to prevent. So the
bridge wraps `ExecutionWorkflow` (§2.14) and a structural test fails if the
adapter module can name the clicking class at all.

---

## 4. Do-not-assume checklist

Verified absent. A future phase that writes code calling any of these is calling
something that does not exist.

**In `albrooks`:** a `Signal` class · a `generate_signal()` method · callbacks,
observers or an event bus · position sizing · lot calculation · account info ·
symbol info or an instrument class · a `FLAT` action · a logging subsystem ·
persistent state · a timeframe enum in the core · `datetime` or timezone types.

**In `auto_trade`:** a `MetaTrader5` import · `mt5.initialize()` · retcode
handling · `account_info` / `balance` / `equity` / `margin` · `trade_contract_size`
/ `volume_step` / `trade_tick_value` / `volume_min` / `volume_max` · a usable
`SymbolInfo` · position sizing · stop-level or freeze-level checks · a limit or
stop order · SL/TP modification · partial close · hedging · terminal launch ·
terminal login · trading-hours checks · an inbound order API · a round-to-step
helper.
