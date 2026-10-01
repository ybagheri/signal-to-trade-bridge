"""The execution envelope: what must exist before the pipeline may send anything.

Phase 9. Until this module, "may the pipeline execute?" was answered by a test
reading a source file and asserting the *absence* of an import. That test did its
job for three phases, and now it has to be replaced with something that can also
answer "yes" safely — which an absence cannot.

### Why a type and not four constructor arguments

An executor, a ledger and a kill switch passed separately is three optional
arguments, and any combination of them is constructible. Every wrong combination is
a way to place a real order without the record that proves what was placed:

* an executor with no ledger — a duplicate is free to repeat
* an executor with no kill switch — nothing can stop it
* an executor with neither — the failure this architecture was drawn to prevent

So they arrive **together**, in one object with no public constructor. There is no
way to hold an executor here without the other two, because there is no way to
construct this at all except :func:`build_execution_envelope`, which takes all three
and refuses a missing one.

:meth:`ProcessSignal.wire_execution` takes this object and nothing else. It cannot be
handed an executor, so it cannot be given one without the envelope.

### What is still *not* here, and why that is not this module's job

An envelope does not make sending safe; it makes sending **accountable**. Two more
things are required and neither is a constructor argument here:

* **the audit log** lives in the `ExecutionWorkflow` the executor already wraps, so
  it arrives with the executor rather than beside it. A separate audit sink would be
  a second source of truth about what was placed.
* **the composition root's own refusal** lives there. This module can say "these
  three exist"; it cannot say "this configuration is allowed to use them", which is
  a decision about a machine and an operator, not about a trade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from signal_to_trade_bridge.ports import IdempotencyStore, KillSwitch, TradeExecutor

__all__ = ["ExecutionEnvelope", "build_execution_envelope"]


class ExecutionEnvelope:
    """The three collaborators that must travel together, or not at all.

    **Not constructible from outside this module.** ``__init__`` exists only to
    refuse, and the fields are set through ``object.__setattr__`` by
    :func:`build_execution_envelope`, which takes all three as required arguments.
    That is what makes "an executor without a ledger" unrepresentable rather than
    merely discouraged.

    Readable and immutable: a caller must be able to *check* what it has been given,
    and must not be able to swap the ledger out afterwards. The three properties are
    read-only, and ``__setattr__`` refuses, so neither the object nor its ``__dict__``
    offers a way through.

    A frozen dataclass would have given the immutability for free but could not have
    given the refusal: its generated ``__init__`` would have accepted three
    collaborators happily, which is the whole thing that must not happen. ``slots``
    is not used either, because ``object.__new__`` plus ``object.__setattr__`` on a
    slotted class raises -- a detail worth recording, because it is the reason this
    is a plain class and not a tidier one.
    """

    __slots__ = ("__dict__", "_executor", "_idempotency", "_kill_switch")

    _executor: TradeExecutor
    _idempotency: IdempotencyStore
    _kill_switch: KillSwitch

    def __init__(self, executor: None = None, **_: object) -> None:
        raise TypeError(
            "ExecutionEnvelope is not constructed directly. Use "
            "build_execution_envelope(executor, idempotency, kill_switch), so that an "
            "executor can never arrive without the ledger recording what it placed and "
            "without the kill switch that stops it."
        )

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse every assignment.

        Immutability is the point: an envelope that could have its ledger swapped
        after construction would be an executor with a ledger that records nothing.
        """
        raise AttributeError(
            f"{name!r} cannot be reassigned on an ExecutionEnvelope. Build a new one."
        )

    @property
    def executor(self) -> TradeExecutor:
        """The executor. Reachable only with the other two present."""
        return self._executor

    @property
    def idempotency(self) -> IdempotencyStore:
        """The durable record of what has been acted on."""
        return self._idempotency

    @property
    def kill_switch(self) -> KillSwitch:
        """The switch consulted before anything is sent."""
        return self._kill_switch

    def __repr__(self) -> str:
        # Types, not instances. An executor's repr could carry a terminal path and a
        # ledger's could carry a filename with a username in it.
        return (
            f"ExecutionEnvelope(executor={type(self._executor).__name__}, "
            f"idempotency={type(self._idempotency).__name__}, "
            f"kill_switch={type(self._kill_switch).__name__})"
        )


def build_execution_envelope(
    executor: TradeExecutor,
    idempotency: IdempotencyStore,
    kill_switch: KillSwitch,
) -> ExecutionEnvelope:
    """Assemble the envelope, or refuse.

    Every check below is a case where the object *looks* complete and is not, and
    where refusing is the safe direction:

    * a missing collaborator — stated above
    * an executor that is also the ledger or the switch — a stub passed twice would
      satisfy "all three present" while recording nothing
    """
    if executor is None:
        raise ValueError("an execution envelope needs an executor")
    if idempotency is None:
        raise ValueError(
            "an execution envelope needs an idempotency store. An executor without one can "
            "place the same trade twice, which is the single failure this architecture "
            "exists to prevent."
        )
    if kill_switch is None:
        raise ValueError(
            "an execution envelope needs a kill switch. One that has to be consulted "
            "through a ledger cannot stop anything while the ledger is unavailable."
        )

    for name, value, others in (
        ("executor", executor, (idempotency, kill_switch)),
        ("idempotency", idempotency, (executor, kill_switch)),
        ("kill_switch", kill_switch, (executor, idempotency)),
    ):
        if any(value is other for other in others):
            raise ValueError(
                f"the same object was passed as both the {name} and another collaborator. "
                f"All three must be distinct: a stub satisfying two roles records nothing "
                f"and would satisfy 'the envelope is complete' while placing nothing."
            )

    envelope = object.__new__(ExecutionEnvelope)
    object.__setattr__(envelope, "_executor", executor)
    object.__setattr__(envelope, "_idempotency", idempotency)
    object.__setattr__(envelope, "_kill_switch", kill_switch)
    return envelope
