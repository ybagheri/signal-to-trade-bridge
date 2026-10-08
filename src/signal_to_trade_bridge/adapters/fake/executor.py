"""A `TradeExecutor` that records instead of trading.

The second implementation of
:class:`~signal_to_trade_bridge.ports.TradeExecutor`, and the reason that port
is worth having. Two implementations that are structurally interchangeable is
what lets a test written against this one exercise the same call path
:class:`~signal_to_trade_bridge.adapters.auto_trade.executor.AutoTradeExecutor`
drives -- which is the whole argument for ports over concrete classes.

### It is an adapter, not a fixture

It lives in the package rather than in ``tests/`` for the reason
:mod:`signal_to_trade_bridge.adapters.fake` documents: a fixture returning a
tuple would test a function that does not exist in production. This returns a real
:class:`~signal_to_trade_bridge.domain.models.ExecutionResult` through the real
port, so the pipeline cannot tell it from the terminal adapter.

### It records what it was asked, which is the point

:attr:`submitted` is the whole value. A test that asserts on it is asserting on
what the pipeline *decided*, before any adapter existed -- the numbers, the
direction, the identifier, the comment -- and it would catch a sizing regression
in a test that never mentions MetaTrader.

### It can be told to fail, on purpose

:attr:`error` makes the executor raise. That path is the interesting one and it
is easy to leave untested: it is the only place the pipeline meets an adapter
that does not behave, and an untested failure path here is an untested ``UNKNOWN``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from signal_to_trade_bridge.application.pre_submit import roll_delay_ms
from signal_to_trade_bridge.domain.models import (
    ExecutionRequest,
    ExecutionResult,
    PreSubmitDelay,
)

__all__ = ["FakeTradeExecutor"]


@dataclass(slots=True)
class FakeTradeExecutor:
    """Records every request and returns a canned result.

    Defaults to a dry run, and that default is the safe direction: a fake that
    answered ``ACCEPTED`` unless told otherwise would make a test suite in which
    somebody forgot to configure something look like a passing suite.
    """

    #: Every request passed to :meth:`submit`, in order. Compared with ``==`` in
    #: tests, and the object is the real one -- ``ExecutionRequest`` is frozen and
    #: value-comparable, so a list of them compares by value.
    submitted: list[ExecutionRequest] = field(default_factory=list)

    #: The pre-submit pause policy to mirror. When enabled, each submission
    #: rolls and records the pause the real executor would have waited --
    #: without waiting, because a recorder that slept would make previews and
    #: tests wait on UI pacing that involves no UI.
    pre_submit_delay: PreSubmitDelay | None = None

    #: The rolled pauses, in milliseconds, one per submission that would have
    #: waited. Empty when the policy is absent or disabled.
    pre_submit_delays: list[int] = field(default_factory=list)

    #: The draw for the recorded pause. Injected (seeded) in tests for a
    #: reproducible recording.
    rng: random.Random | None = None

    #: The result to return. Set it to simulate an acceptance, a rejection or an
    #: unknown.
    result: ExecutionResult | None = None

    #: When set, :meth:`submit` raises it instead of answering. For the path where
    #: an adapter misbehaves.
    error: BaseException | None = None

    def submit(self, request: ExecutionRequest) -> ExecutionResult:
        """Record the request and return the configured outcome."""
        if self.error is not None:
            raise self.error
        self.submitted.append(request)
        delay_ms = roll_delay_ms(self.pre_submit_delay, self.rng or random.Random())
        if delay_ms is not None:
            self.pre_submit_delays.append(delay_ms)
        if self.result is not None:
            return self.result
        return ExecutionResult(
            signal_id=request.signal_id,
            status=ExecutionResult.STATUS_DRY_RUN,
            message="recorded by FakeTradeExecutor; nothing was sent",
        )
