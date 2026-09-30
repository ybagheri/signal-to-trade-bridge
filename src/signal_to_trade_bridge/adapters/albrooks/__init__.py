"""The Al Brooks price-action engine, as a signal source.

The only names the rest of the project imports from this package. Everything
upstream-shaped is confined to :mod:`mapper` and :mod:`source`, so an engine
change lands in one adapter rather than across the bridge.
"""

from signal_to_trade_bridge.adapters.albrooks.identity import (
    SIGNAL_ID_PREFIX,
    compute_signal_id,
)
from signal_to_trade_bridge.adapters.albrooks.mapper import (
    GEOMETRY_ISSUES,
    MAPPED_FIELDS,
    STRUCTURAL_STOP_BASES,
    STRUCTURAL_TARGET_BASES,
    AnalysisOutcome,
    map_result_to_signal,
)
from signal_to_trade_bridge.adapters.albrooks.source import (
    AlBrooksSignalSource,
    AnalyzerLike,
    abstention_reason,
    is_tradable_signal,
)

__all__ = [
    "GEOMETRY_ISSUES",
    "MAPPED_FIELDS",
    "SIGNAL_ID_PREFIX",
    "STRUCTURAL_STOP_BASES",
    "STRUCTURAL_TARGET_BASES",
    "AlBrooksSignalSource",
    "AnalysisOutcome",
    "AnalyzerLike",
    "abstention_reason",
    "compute_signal_id",
    "is_tradable_signal",
    "map_result_to_signal",
]
