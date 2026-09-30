"""Application layer: use cases.

Two exist so far:

* :class:`~signal_to_trade_bridge.application.risk_service.RiskService` (Phase 4)
  obtains the account and symbol facts through ``ports`` and hands them to the
  domain's pure functions. It exists because ``test_domain_isolation`` walks the
  domain package's AST and fails on any edge to ``ports``, so nothing in
  ``domain`` may ask a provider for a balance.
* :class:`~signal_to_trade_bridge.application.process_signal.ProcessSignal`
  (Phase 6) is the single use case: it runs every stage in order, stops at the
  first refusal, and turns whatever happened into one ``TradeDecision``.

Neither owns arithmetic of its own; every number either reports came from a
domain function, which is what makes those functions testable in isolation.
Neither reaches outside the process: there is no executor here yet, and adding one
before Phase 7 would be an execution path without the kill switch, the idempotency
ledger and the audit log around it.
"""
