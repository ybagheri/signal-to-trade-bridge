"""Application layer: use cases.

Three exist so far:

* :class:`~signal_to_trade_bridge.application.risk_service.RiskService` (Phase 4)
  obtains the account and symbol facts through ``ports`` and hands them to the
  domain's pure functions. It exists because ``test_domain_isolation`` walks the
  domain package's AST and fails on any edge to ``ports``, so nothing in
  ``domain`` may ask a provider for a balance.
* :class:`~signal_to_trade_bridge.application.process_signal.ProcessSignal`
  (Phase 6) is the single use case: it runs every stage in order, stops at the
  first refusal, and turns whatever happened into one ``TradeDecision``.
* :mod:`~signal_to_trade_bridge.application.dry_run` (Phase 8) turns a validated
  intent into the order that *would* be sent, and reports in full why nothing was.
  It is what makes ``DRY_RUN`` an answer rather than a placeholder.

None of them owns arithmetic of its own; every number either reports came from a
domain function, which is what makes those functions testable in isolation.

**And none of them references an executor.** Not even ``dry_run``, which is the
module closest to the line: it builds the request an executor would take and asks
the downstream project what it would say about it, and it cannot send anything. That
constraint is enforced by ``test_this_module_holds_no_executor`` and it is the rule
that most needs restating as the project grows: execution arrives with the
idempotency ledger and the kill switch around it, and an executor reachable from the
pipeline before then is a second way to open a duplicate position.
"""
