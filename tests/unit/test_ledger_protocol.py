"""The ledger must satisfy **both** protocols, not just ours.

`AutoTradeLedger` exists to satisfy this project's `IdempotencyStore` *and* upstream's
`ExecutionLedger`, because the same object is handed to both: ours as the envelope's
idempotency store, upstream's as `ExecutionWorkflow(ledger=...)`.

**Every test before this one checked only the first.** Upstream's workflow calls
`ledger.record_result(result)` with an already-built `ExecutionResult`; the wrapper
only had `record_outcome(key, execution_id, outcome)`. So the call raised
`AttributeError` -- and it raised *after* the order had already gone to the terminal,
which is precisely the moment where an exception is most expensive.

The failure surfaced as `UNKNOWN`, which is the right verdict for an exception out of
the recording path, and the reason no automated test caught it is that the tests all
either passed a real `JsonExecutionLedger` through directly or used a fake executor.
This file is the missing check: it reads the *real* upstream workflow's source and
asserts the wrapper has every method it calls.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from signal_to_trade_bridge.adapters.auto_trade.ledger import AutoTradeLedger

workflow = pytest.importorskip("auto_trade.application.workflow")


def _methods_called_on_self_ledger() -> set[str]:
    """Every ``self.ledger.<name>(...)`` in upstream's workflow module.

    Read from the source rather than from a list written here, because a hand-written
    list is exactly what went wrong: the author believed the wrapper satisfied the
    protocol, so the list was never derived from the caller.
    """
    source = inspect.getsource(workflow)
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "ledger"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
        ):
            found.add(node.func.attr)
    return found


class TestTheWrapperSatisfiesUpstreamsProtocol:
    def test_it_calls_at_least_one_method(self) -> None:
        # Guarding the guard: if the AST walk found nothing, every assertion below
        # would be vacuously true, and "the wrapper has all the methods it is
        # called with" would pass on a workflow that calls none.
        called = _methods_called_on_self_ledger()
        assert called, "found no `self.ledger.*` calls; the extraction is broken"

    def test_it_has_every_method_upstream_calls(self) -> None:
        called = _methods_called_on_self_ledger()
        missing = sorted(name for name in called if not hasattr(AutoTradeLedger, name))
        assert not missing, (
            f"auto_trade's ExecutionWorkflow calls {sorted(called)} on the object it is "
            f"given as `ledger`, and AutoTradeLedger has no {missing}. A method that is "
            f"missing here fails *after* an order has been sent -- see the class "
            f"docstring for how that was found."
        )

    def test_the_methods_it_calls_are_the_ones_we_expect(self) -> None:
        # Not a behaviour change -- a tripwire. If upstream's workflow starts calling
        # a fourth method, this fails and someone has to decide whether the wrapper
        # should forward it, rather than the failure arriving as an UNKNOWN order on
        # a live account.
        #
        # **Three methods, and the third was the near miss.** I expected two:
        # `contains` and `record_result`. Reading the source found
        # `record_attempt(signal_id, execution_id)` at line 112 as well, which the
        # wrapper happens to have because this project's port has the same shape. Had
        # I written this assertion from expectation rather than from the source, the
        # list would have been two entries long and the gap would have stayed
        # invisible -- the failure mode is a wrapper that is *almost* right.
        assert _methods_called_on_self_ledger() == {
            "contains",
            "record_attempt",
            "record_result",
        }


class TestTheForwardingIsReal:
    """Not just present: the call has to reach the real ledger."""

    def test_record_result_reaches_the_inner_ledger(self, tmp_path: Path) -> None:
        from auto_trade.application.ledger import JsonExecutionLedger

        inner = JsonExecutionLedger(tmp_path / "idempotency.json")
        wrapper = AutoTradeLedger(inner)

        class _Result:
            signal_id = "sig-1"
            execution_id = "exec-1"
            status = type("S", (), {"value": "SUBMITTED"})()
            state = "submitted"
            message = "sent"
            order_reference = "ref-1"
            evidence = None

        wrapper.record_result(_Result())
        # Read back through the *inner* object, not the wrapper: the point is that
        # the write landed in the durable file, which is the whole value of the thing.
        assert inner.contains("sig-1") is True

    def test_a_failing_inner_ledger_is_refused_not_swallowed(self, tmp_path: Path) -> None:
        # The wrapper's contract is that it refuses when the ledger cannot be written,
        # rather than substituting. A `record_result` that quietly did nothing would
        # leave a pending entry nobody settles.
        from signal_to_trade_bridge.adapters.auto_trade.bindings import AutoTradeUnavailable

        class _Broken:
            def record_result(self, result: object) -> None:
                raise OSError("disk full")

            def contains(self, key: str) -> bool:
                return False

            def record_attempt(self, key: str, execution_id: str) -> None:
                raise OSError("disk full")

        class _Result:
            signal_id = "sig-1"
            execution_id = "exec-1"

        with pytest.raises(AutoTradeUnavailable):
            AutoTradeLedger(_Broken()).record_result(_Result())


class TestOurOwnPortStillWorks:
    def test_record_outcome_is_unchanged(self, tmp_path: Path) -> None:
        from auto_trade.application.ledger import JsonExecutionLedger

        inner = JsonExecutionLedger(tmp_path / "idempotency.json")
        wrapper = AutoTradeLedger(inner)
        wrapper.record_attempt("sig-2", "exec-2")
        wrapper.record_outcome("sig-2", "exec-2", {"status": "FILLED", "message": "ok"})
        assert inner.contains("sig-2") is True
        assert wrapper.contains("sig-2") is True
