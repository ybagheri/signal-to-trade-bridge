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
    RiskParameters,
    Signal,
    StopLoss,
    SymbolSpec,
    TakeProfit,
    TradeDecision,
    TradeIntent,
    utc_now,
)

__all__ = [
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
    "utc_now",
]
