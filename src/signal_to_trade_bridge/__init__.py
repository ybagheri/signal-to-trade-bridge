"""Signal-to-Trade Bridge.

A safe, object-oriented integration layer between the Al Brooks price-action
engine, which owns the analysis, and the ``auto-trade`` project, which owns
execution.

This package's job is the translation between those two domains: normalise a
reading, size a position against real account and symbol data, validate the
result, and either produce a finished order or a structured refusal. It is not a
trading strategy, it does not detect setups, and it does not choose a direction.

The layering, and the rule that governs it:

    interfaces -> application -> domain
                               ^
                          ports
                               ^
                          adapters

``domain`` imports nothing from any other layer and nothing from ``adapters``.
Every external project is reached through a Protocol in ``ports``. That is what
keeps the domain importable and testable on a machine where neither upstream
project is installed -- which matters here, because both are private
repositories that cannot be resolved from an index.
"""

from signal_to_trade_bridge.version import __version__

__all__ = ["__version__"]
