"""Stubs shaped like the upstream engine's real output.

Built by hand from the audit's verbatim quotes rather than by importing
``albrooks``, for three reasons:

* the upstream projects are private and cannot be installed from an index, so a
  test that imported them would only run on one machine
* the two shapes below are the *contract*, and a contract expressed as a literal
  cannot drift silently when the upstream project changes
* the degenerate three-key shape is the one most likely to be forgotten, and a
  hand-written stub makes it impossible to forget

Where a stub is used, the real analyzer is exercised separately by an opt-in
integration test in ``tests/integration``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "StubAnalyzer",
    "StubMarketData",
    "StubResult",
    "stub_abstention",
    "stub_buy",
    "stub_degenerate_no_atr",
    "stub_degenerate_no_bars",
    "stub_sell",
]


@dataclass
class StubResult:
    """Shaped like ``albrooks.AnalysisResult``.

    Only the fields the adapter reads are present, and each is named after the
    real one. The real class carries twenty-three fields; a stub carrying all of
    them would be a maintenance liability for no benefit, because the adapter
    reads six.
    """

    symbol: str
    timeframe: str
    decision: dict[str, Any] = field(default_factory=dict)
    last_closed_bar: int = 299
    bar_features: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "decision": self.decision,
            "last_closed_bar": self.last_closed_bar,
            "bar_features": self.bar_features,
        }


def _features(bar_index: int, bar_time: float) -> list[dict[str, Any]]:
    # The real engine computes features only for bars 0..last_closed, so the
    # list's final row is not necessarily the analysed bar. The stub reproduces
    # that by putting an unrelated later row at the end, which is what catches an
    # adapter that naively takes `bar_features[-1]`.
    return [
        {"index": bar_index - 1, "time": bar_time - 3600.0},
        {"index": bar_index, "time": bar_time},
        {"index": bar_index + 1, "time": bar_time + 3600.0},
    ]


def _plan(
    *,
    entry: float,
    stop: float,
    target: float,
    stop_basis: str = "PULLBACK_EXTREME",
    target_basis: str = "SWING",
    issues: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "subject": "PULLBACK_H",
        "direction": 1,
        "entry": entry,
        "stop": stop,
        "target": target,
        "entry_basis": "SETUP_REFERENCE",
        "stop_basis": stop_basis,
        "target_basis": target_basis,
        "entry_reference": entry,
        "stop_reference": stop,
        "target_reference": target,
        "bar_index": 299,
        "signal_bar": 298,
        "risk": abs(entry - stop),
        "reward": abs(target - entry),
        "reward_to_risk": 1.0,
        "risk_to_reward": 1.0,
        "is_valid": not issues,
        "has_structural_stop": stop_basis not in ("ATR_FALLBACK", "NONE"),
        "invalidation": "",
        "management": [],
        "issues": list(issues),
        "warnings": [],
        "evidence_score": 0.72,
        "is_recommendation": False,
    }


BAR_TIME = 1727740800.0


def stub_buy(
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    *,
    entry: float = 1.10000,
    stop: float = 1.09700,
    target: float = 1.10300,
    stop_basis: str = "PULLBACK_EXTREME",
    target_basis: str = "SWING",
    issues: tuple[str, ...] = (),
) -> StubResult:
    """A ranked BUY, the normal thirteen-key shape."""
    return StubResult(
        symbol=symbol,
        timeframe=timeframe,
        last_closed_bar=299,
        bar_features=_features(299, BAR_TIME),
        decision={
            "action": "BUY",
            "reason": "RANKED_CANDIDATE",
            "subject": "pullback_h#0",
            "direction": 1,
            "plan": _plan(
                entry=entry,
                stop=stop,
                target=target,
                stop_basis=stop_basis,
                target_basis=target_basis,
                issues=issues,
            ),
            "evidence": {
                "value": 0.72,
                "band": "MODERATE",
                "by_source": {"PULLBACK": 0.8},
                "quantified_share": 0.7,
                "warnings": [],
                "is_probability": False,
                "ppts": 72.0,
            },
            "explanation": [
                "3 candidate plans were gated and 1 survived every gate",
                "selected pullback_h#0 on the declared criteria",
            ],
            "vetoes": [],
            "considered": {"considered": 3, "eligible": 1, "rejected": 2},
            "ranking_basis": [
                "evidence_score (0..1, higher first)",
                "reward_to_risk (higher first)",
            ],
            "sides": {"bull_ppts": 72.0, "bear_ppts": 0.0},
            "is_actionable": True,
            "is_probability": False,
        },
    )


def stub_sell(symbol: str = "EURUSD", timeframe: str = "H1") -> StubResult:
    """A ranked SELL, mirroring :func:`stub_buy`."""
    result = stub_buy(symbol, timeframe)
    decision = result.decision
    decision["action"] = "SELL"
    decision["direction"] = -1
    decision["subject"] = "breakout_pullback#1"
    decision["sides"] = {"bull_ppts": 0.0, "bear_ppts": 65.0}
    plan = decision["plan"]
    plan["subject"] = "BREAKOUT_PULLBACK"
    plan["direction"] = -1
    plan["entry"] = 1.10000
    plan["stop"] = 1.10300
    plan["target"] = 1.09700
    plan["risk"] = 0.003
    plan["reward"] = 0.003
    plan["stop_basis"] = "SWING"
    plan["target_basis"] = "MEASURED_MOVE"
    return result


def stub_abstention(action: str = "WAIT", reason: str = "ALL_CANDIDATES_VETOED") -> StubResult:
    """A ``WAIT`` or ``NO_TRADE`` with the full shape and no plan.

    The real engine returns a complete decision with ``plan`` set to ``None`` and
    ``is_actionable`` false. Reproduced rather than shortened, because an adapter
    that assumed a plan was always present would pass against a stub that had
    invented one.
    """
    return StubResult(
        symbol="EURUSD",
        timeframe="H1",
        last_closed_bar=299,
        bar_features=_features(299, BAR_TIME),
        decision={
            "action": action,
            "reason": reason,
            "subject": "",
            "direction": 0,
            "plan": None,
            "evidence": {},
            "explanation": [
                "3 candidate plans were gated and none survived",
                "nothing is being ranked",
            ],
            "vetoes": [
                {
                    "code": "EVIDENCE_TOO_WEAK",
                    "subject": "pullback_h#0",
                    "detail": "evidence 0.21 below min_score 0.40",
                    "blocking": True,
                    "config_key": "min_score",
                }
            ],
            "considered": {"considered": 3, "eligible": 0, "rejected": 3},
            "ranking_basis": ["evidence_score (0..1, higher first)"],
            "sides": {},
            "is_actionable": False,
            "is_probability": False,
        },
    )


def stub_degenerate_no_bars(symbol: str = "EURUSD") -> StubResult:
    """The three-key shape ``Analyzer._empty`` returns when there are no bars.

    The single most important stub in this file. The real shape is
    ``{"action", "reason", "note"}`` -- three keys, not thirteen -- and an adapter
    that read ``decision["plan"]`` directly would raise on exactly the input that
    most needs a clean no-trade.
    """
    return StubResult(
        symbol=symbol,
        timeframe="H1",
        last_closed_bar=-1,
        bar_features=[],
        decision={
            "action": "NO_TRADE",
            "reason": "NO_BARS",
            "note": "No analysis was run; see market_state.reason.",
        },
    )


def stub_degenerate_no_atr(symbol: str = "EURUSD") -> StubResult:
    """The other degenerate shape: a series with no volatility reference."""
    return StubResult(
        symbol=symbol,
        timeframe="H1",
        last_closed_bar=299,
        bar_features=_features(299, BAR_TIME),
        decision={
            "action": "NO_TRADE",
            "reason": "ATR_UNAVAILABLE",
            "note": "No analysis was run; see market_state.reason.",
        },
    )


class StubAnalyzer:
    """Stands in for ``albrooks.Analyzer``.

    Returns a pre-set result and counts its calls, so a test can assert both what
    was mapped and that the engine was actually asked. Raises whatever it was
    given, to exercise the error path.
    """

    def __init__(self, result: Any | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def analyze(
        self,
        bars: list[Any],
        symbol: str = "GENERIC",
        timeframe: str = "UNKNOWN",
        last_closed: int | None = None,
    ) -> Any:
        self.calls.append(
            {
                "bars": bars,
                "symbol": symbol,
                "timeframe": timeframe,
                "last_closed": last_closed,
            }
        )
        if self._error is not None:
            raise self._error
        return self._result


class StubMarketData:
    """Stands in for ``MarketDataProvider``.

    Returns a fixed list of bars, which the adapter only passes through -- it does
    not inspect them, because the upstream engine defines its own ``Bar`` and
    naming a bridge type here would couple the port to a package the domain must
    not import.
    """

    def __init__(self, bars: list[Any] | None = None, error: Exception | None = None) -> None:
        self._bars = bars if bars is not None else [{"close": 1.1}, {"close": 1.2}]
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> list[Any]:
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "count": count})
        if self._error is not None:
            raise self._error
        return self._bars
