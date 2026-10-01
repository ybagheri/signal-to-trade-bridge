"""The idempotency ledger, over the execution project's own JSON ledger.

Phase 9, and the last piece of the safety envelope. The bridge's
:class:`~signal_to_trade_bridge.ports.IdempotencyStore` is satisfied by
``auto_trade``'s ``JsonExecutionLedger`` rather than by anything new, for the reason
the port's docstring gives: two stores would be two sources of truth, and the one
that mattered least would be the one deciding whether a duplicate trade happened.

### What the real ledger guarantees, and what it costs to use

Read from ``application/ledger.py``, because these four properties are the whole
mechanism and none of them is obvious from the interface:

1. **The write is atomic.** ``_write`` goes to a ``.tmp`` sibling and then
   ``replace()``, so a crash mid-write leaves the previous file intact rather than a
   truncated one. On Windows ``replace`` is also the operation that cannot fail
   halfway with both files present.
2. **The attempt is recorded before the click.** ``record_attempt`` writes
   ``status="REQUESTED"``, ``state="EXECUTING"`` and returns — and upstream's
   workflow calls it *before* ``adapter.execute_order``. A process that dies
   between the two leaves a pending record an operator can settle with
   ``reconcile()``, rather than no record at all.
3. **A second attempt cannot take over the first one's record.**
   ``record_result`` returns silently when the stored ``execution_id`` differs.
   That is what makes a crashed pending attempt survive a later retry rather than
   being overwritten by it.
4. **An unreadable ledger is a refusal, not an empty one.** ``_read`` raises
   ``ExecutionUnknownError`` for a file that is corrupt or has the wrong shape.
   **This is the property that makes the adapter below fail closed**, and it is
   worth being explicit about why it matters: a ledger that silently read as empty
   would answer "no, this signal has not been acted on" for every signal ever
   recorded, which is a green light on a duplicate.

### The disagreement with ``is_retryable``, stated rather than resolved

``ExecutionResult.is_retryable`` is ``True`` for a clean rejection: the caller may
send it again. **Upstream's ledger disagrees.** ``contains()`` is a plain key lookup,
so an entry written for a ``REJECTED`` result still matches, and the same signal id
is refused with ``"duplicate signal id"``.

Both are true and they are about different moments, so neither is changed here.
``is_retryable`` describes what a caller may do with a result it already holds;
``contains`` describes what a *new* attempt would meet. But an operator who retries
on ``is_retryable`` will be refused, and pretending otherwise would be worse than
the refusal — so it is written down in the port's docstring and in
``docs/risk-management.md`` rather than left for them to discover.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from signal_to_trade_bridge.adapters.auto_trade.bindings import (
    AutoTradeBindings,
    AutoTradeUnavailable,
)

__all__ = ["AutoTradeLedger", "open_ledger"]


class AutoTradeLedger:
    """The bridge's idempotency port, backed by ``JsonExecutionLedger``.

    Thin by necessity. It translates three method calls and refuses rather than
    substituting, because the ledger's value is entirely in what it guarantees about
    durability and ordering, and a wrapper that added a fallback would hide exactly
    the failure that matters.
    """

    __slots__ = ("_ledger",)

    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    def contains(self, key: str) -> bool:
        """Whether this signal id has any record at all.

        Raises :class:`AutoTradeUnavailable` rather than answering ``False`` if the
        ledger cannot be read. **The distinction is the whole point**: "not recorded"
        and "could not ask" are different answers, and only the first one may let a
        trade proceed.
        """
        try:
            return bool(self._ledger.contains(key))
        except Exception as exc:  # broad on purpose: see the class docstring
            raise _unreadable(key, exc) from exc

    def record_attempt(self, key: str, execution_id: str) -> None:
        """Record the attempt durably, before anything is sent.

        Raised before the call returns rather than after, so a caller cannot mistake
        a partially-written attempt for a recorded one.
        """
        try:
            self._ledger.record_attempt(key, execution_id)
        except Exception as exc:  # broad on purpose: see the class docstring
            raise _unreadable(f"attempt on {key}", exc) from exc

    def record_outcome(self, key: str, execution_id: str, outcome: Mapping[str, Any]) -> None:
        """Record how the attempt turned out.

        The bridge's ``outcome`` mapping is turned into an upstream
        ``ExecutionResult`` because that is the only thing ``record_result``
        accepts, and because the upstream type already carries the fields the ledger
        persists. Building it here rather than asking the caller to means a caller
        cannot record an outcome the ledger will silently drop.
        """
        result = self._to_result(key, execution_id, outcome)
        try:
            self._ledger.record_result(result)
        except Exception as exc:  # broad on purpose: see the class docstring
            raise _unreadable(f"outcome for {key}", exc) from exc

    def _to_result(self, key: str, execution_id: str, outcome: Mapping[str, Any]) -> Any:
        from auto_trade.domain.enums import ExecutionStatus
        from auto_trade.domain.models import ExecutionResult

        raw = str(outcome.get("status", "") or "").strip().upper()
        try:
            status = ExecutionStatus(raw)
        except ValueError:
            # Recorded as UNKNOWN rather than refused. A status the ledger does not
            # recognise still happened, and refusing to record it would leave a
            # pending entry that an operator has to reconcile by hand -- which is
            # safe but is a worse outcome than a record that says "we did not
            # understand this".
            status = ExecutionStatus.UNKNOWN

        evidence = outcome.get("evidence")
        return ExecutionResult(
            execution_id,
            key,
            status,
            str(outcome.get("state", "") or ""),
            str(outcome.get("message", "") or ""),
            _optional_str(outcome.get("order_reference")),
            _optional_str(outcome.get("error")),
            evidence if hasattr(evidence, "to_dict") else None,
        )

    def pending(self) -> tuple[Mapping[str, Any], ...]:
        """Attempts recorded but never settled.

        Upstream's own ``pending()``. **Not** part of the bridge's port — it is
        here for the composition root and for an operator, because "this signal was
        attempted and we never found out" is a question the ledger can answer and
        the bridge otherwise cannot.
        """
        try:
            pending = self._ledger.pending()
        except Exception as exc:  # broad on purpose: see the class docstring
            raise _unreadable("pending attempts", exc) from exc
        # Copied rather than passed through, so a caller cannot reach the ledger's
        # own entry objects and change what it has recorded.
        return tuple(dict(entry) for entry in pending)

    def reconcile(self, key: str, observation: str) -> Mapping[str, Any]:
        """Settle an unprovable attempt on an operator's word.

        Upstream refuses to reconcile anything already recorded as settled, and that
        refusal is load-bearing: it means the record cannot be rewritten by someone
        who merely wants it to say something different.
        """
        try:
            settled = self._ledger.reconcile(key, observation)
        except Exception as exc:  # broad on purpose: see the class docstring
            raise _unreadable(f"reconciliation of {key}", exc) from exc
        # Upstream returns a plain dict; the cast keeps mypy from widening the
        # whole method to `Any` because the ledger itself is untyped.
        return cast(Mapping[str, Any], settled)

    def __repr__(self) -> str:
        # The path is the only thing worth knowing, and it can contain a username.
        # The other reprs in this project redact for the same reason.
        return f"AutoTradeLedger(entries={len(self._ledger.records())})"


def _unreadable(what: str, exc: Exception) -> AutoTradeUnavailable:
    """The one message every ledger failure produces.

    **One message, for one failure.** An unreadable ledger cannot answer *any*
    question -- not "is this recorded", not "record this attempt" -- so a single
    phrasing is not a simplification but an accurate description. The alternative is
    three messages differing only in the noun, and a reader who saw two of them
    would reasonably conclude they were two different faults needing two different
    responses.
    """
    return AutoTradeUnavailable(
        f"the idempotency ledger could not be read while handling {what}: "
        f"{type(exc).__name__}: {exc}. Refused rather than treated as an empty ledger, "
        f"because an unreadable ledger that answered 'nothing has been recorded' would "
        f"permit every signal ever attempted to be sent a second time."
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def open_ledger(bindings: AutoTradeBindings, path: Any) -> AutoTradeLedger:
    """Open the real ledger at ``path``, or refuse.

    Construction reads the file, and upstream's reader **raises on a corrupt or
    wrongly-shaped one** rather than starting empty. That refusal is preserved here
    rather than caught: it arrives at the composition root as an error, which is the
    correct place for it, because a process that cannot read its own record of what
    it has already done must not proceed to do more.
    """
    try:
        from auto_trade.application.ledger import JsonExecutionLedger
    except ImportError as exc:  # pragma: no cover - the bindings check covers this
        raise AutoTradeUnavailable(
            f"the execution project's ledger could not be imported: {exc}"
        ) from exc

    try:
        ledger = JsonExecutionLedger(path)
    except Exception as exc:
        raise AutoTradeUnavailable(
            f"the idempotency ledger at {path} could not be read: {type(exc).__name__}: "
            f"{exc}. Refusing to start with an unreadable ledger: treating it as empty "
            f"would allow every signal ever attempted to be sent a second time."
        ) from exc

    return AutoTradeLedger(ledger)
