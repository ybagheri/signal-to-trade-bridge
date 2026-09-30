"""Domain layer.

Pure business concepts with no external dependencies. This package imports
nothing from ``ports``, ``adapters``, ``infrastructure``, ``application`` or
``configuration``, and nothing from any third-party package. A test enforces
that, because the property is what makes the trading logic testable and
importable on a machine where neither upstream project is installed.
"""

from signal_to_trade_bridge.domain.enums import (
    DecisionAction,
    Direction,
    RejectionReason,
    SignalAction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.errors import (
    AutoTradeBridgeError,
    ConfigurationError,
    ExecutionFailedError,
    ExecutionRejectedError,
    ExecutionUnknownError,
    InsufficientMarketDataError,
    IntegrationError,
    InvalidRiskParametersError,
    InvalidSignalError,
    InvalidStopLossError,
    InvalidTakeProfitError,
    InvalidVolumeError,
    TradingError,
)
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionRequest,
    ExecutionResult,
    PositionSize,
    RiskBudget,
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
    TradeDecision,
    TradeIntent,
    utc_now,
)
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved
from signal_to_trade_bridge.domain.risk import check_currency_compatibility, resolve_risk_budget
from signal_to_trade_bridge.domain.sizing import check_broker_constraints, resolve_position_size
from signal_to_trade_bridge.domain.stops import (
    STRUCTURAL_STOP_BASES,
    is_protective,
    is_structural_basis,
    resolve_stop,
)
from signal_to_trade_bridge.domain.take_profit import (
    STRUCTURAL_TARGET_BASES,
    is_favourable,
    is_structural_target_basis,
    resolve_take_profit,
    target_from_ratio,
)
from signal_to_trade_bridge.domain.validation import (
    validate_against_spec,
    validate_geometry,
    validate_policy,
    validate_signal,
)

__all__ = [
    "STRUCTURAL_STOP_BASES",
    "STRUCTURAL_TARGET_BASES",
    "AccountBalance",
    "AutoTradeBridgeError",
    "ConfigurationError",
    "DecisionAction",
    "Direction",
    "ExecutionFailedError",
    "ExecutionRejectedError",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionUnknownError",
    "InsufficientMarketDataError",
    "IntegrationError",
    "InvalidRiskParametersError",
    "InvalidSignalError",
    "InvalidStopLossError",
    "InvalidTakeProfitError",
    "InvalidVolumeError",
    "PositionSize",
    "RejectionReason",
    "Resolution",
    "RiskBudget",
    "RiskParameters",
    "Signal",
    "SignalAction",
    "StopLoss",
    "StopSource",
    "SymbolSpec",
    "TakeProfit",
    "TakeProfitSource",
    "TradeDecision",
    "TradeIntent",
    "TradingError",
    "check_broker_constraints",
    "check_currency_compatibility",
    "is_favourable",
    "is_protective",
    "is_structural_basis",
    "is_structural_target_basis",
    "refused",
    "resolve_position_size",
    "resolve_risk_budget",
    "resolve_stop",
    "resolve_take_profit",
    "resolved",
    "target_from_ratio",
    "utc_now",
    "validate_against_spec",
    "validate_geometry",
    "validate_policy",
    "validate_signal",
]
