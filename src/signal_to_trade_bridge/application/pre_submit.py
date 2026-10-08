"""The pre-submit pause, shared by both executors.

The real executor (:class:`~signal_to_trade_bridge.adapters.auto_trade.executor.AutoTradeExecutor`)
sleeps the rolled duration; the recording one
(:class:`~signal_to_trade_bridge.adapters.fake.executor.FakeTradeExecutor`) records it
without waiting. Both decide *whether* and *how long* through this module, so the
two cannot disagree about what "the configured delay" means.

Neither function here sleeps. Sleeping is the executor's decision -- it owns the
``sleeper`` callable -- and a helper that slept would make the recording executor
wait, which is exactly what must not happen in a preview or a test.
"""

from __future__ import annotations

import random

from signal_to_trade_bridge.domain.models import PreSubmitDelay

__all__ = ["roll_delay_ms"]


def roll_delay_ms(policy: PreSubmitDelay | None, rng: random.Random) -> int | None:
    """The pause to take for one submission, or ``None`` for none.

    ``None`` covers both "no policy was wired" and "the policy is disabled",
    because both mean the same thing downstream: submit immediately, log
    nothing. A disabled policy producing a zero-millisecond pause instead would
    be a log line claiming a delay happened when it did not.
    """
    if policy is None:
        return None
    return policy.roll_ms(rng)
