"""Application layer: use cases.

One use case so far -- :class:`ProcessSignal`, added in Phase 6. It orchestrates
the domain's pure calculations and depends on ``ports`` for everything external.
It owns no arithmetic of its own; every number it reports came from a domain
function, which is what makes those functions testable in isolation.
"""
