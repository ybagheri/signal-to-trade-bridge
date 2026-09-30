"""Application layer: use cases.

One exists so far -- :class:`~signal_to_trade_bridge.application.risk_service.RiskService`,
added in Phase 4. It obtains the account and symbol facts through ``ports`` and
hands them to the domain's pure functions, which is a job the domain cannot do
itself: ``test_domain_isolation`` walks that package's AST and fails on any edge
to ``ports``, so nothing in ``domain`` may ask a provider for a balance.

It owns no arithmetic of its own; every number it reports came from a domain
function, which is what makes those functions testable in isolation.

The full use case -- ``ProcessSignal``, which wires a signal through validation,
stop, take profit, sizing and into a decision -- is Phase 6.
"""
