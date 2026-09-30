"""Configuration: how the bridge is told what it may do.

Separated from the domain so that a machine-specific path can never reach a risk
calculation. Settings are grouped by *why they change* -- a risk percentage is a
trading decision, a terminal path is a fact about one laptop -- and mixing those
two groups is how a terminal path ends up in a risk formula.
"""

from signal_to_trade_bridge.configuration.config import (
    BRIDGE_ENV_PREFIX,
    BridgeConfig,
    config_from_env,
    load_dotenv,
)

__all__ = [
    "BRIDGE_ENV_PREFIX",
    "BridgeConfig",
    "config_from_env",
    "load_dotenv",
]
