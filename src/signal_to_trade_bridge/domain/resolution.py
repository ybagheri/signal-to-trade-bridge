"""The result of a step that can fail with a reason.

Every stage of the decision pipeline either produces a value or refuses with a
stable reason code. That is a two-outcome shape, and it appears in every stage
from the stop resolution in Phase 3 to the position sizer in Phase 4.

Modelled once, here, rather than four times in four modules. A generic type with
a single obvious shape is easier to reason about than four bespoke result classes
that differ in field names, and it means a caller learns the pattern once:

    resolution = resolve_stop(signal, risk)
    if not resolution.ok:
        return no_trade(resolution.reason, resolution.explanation)

Failures are **values, not exceptions**. A missing stop is an expected outcome
that happens on most signals, not an exceptional condition, and an exception
would force every caller into a try/except to handle the common case. The
exception hierarchy in :mod:`errors` is for genuine faults -- a misconfiguration,
an unreachable broker -- where continuing is not reasonable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from signal_to_trade_bridge.domain.enums import RejectionReason

__all__ = ["Resolution", "refused", "resolved"]

T = TypeVar("T")


def _frozen(details: Mapping[str, Any]) -> Mapping[str, Any]:
    """A read-only view of the details.

    Not cosmetic. A ``Resolution`` is a decision record: it is written to a log,
    put in an audit trail, and read back months later to answer "why was this
    trade refused". If ``details`` were a plain dict, any code holding a reference
    could rewrite that record after the fact, and the log would disagree with what
    actually happened -- with nothing recording the edit.

    The copy is shallow, which is enough: the values are strings, numbers and
    booleans, none of which are themselves mutable containers the caller would
    reach through. A test asserts the immutability, because this is a property
    that is easy to lose in a refactor and impossible to notice afterwards.
    """
    return MappingProxyType(dict(details))


@dataclass(frozen=True, slots=True)
class Resolution(Generic[T]):
    """Either a value, or a reason why there is not one.

    Both fields are inspectable, so a refused trade can still report what it had
    managed to work out. A stop resolution that fails because the stop is on the
    wrong side still knows the entry, the stop, and the distance it computed, and
    recording that is what makes a refusal debuggable rather than merely negative.

    **``value is None`` does not by itself mean failure.** A take-profit step can
    succeed with the answer "there is no take profit", and that is a successful
    resolution of a deliberate configuration rather than a problem. Success is
    therefore carried by an explicit flag, set by :func:`resolved` and cleared by
    :func:`refused`, instead of being inferred from the value. Inferring it would
    make the one resolution that legitimately produces ``None`` indistinguishable
    from a refusal -- and a log full of refusals for a deliberate configuration
    trains people to ignore the reason codes, which defeats their purpose.
    """

    #: The resolved value, or ``None`` when the step resolved to nothing.
    value: T | None = None
    #: Why there is no value. ``None`` on success, always set on failure.
    reason: RejectionReason | None = None
    #: A sentence a human can act on. Never the only record of a failure -- the
    #: reason code is, because prose cannot be counted or alerted on.
    explanation: str = ""
    #: Whatever the step worked out before refusing. The partial arithmetic is
    #: usually the interesting part of a refusal. Read-only: a decision record
    #: that could be edited after the fact would not be a record.
    details: Mapping[str, Any] = MappingProxyType({})
    #: Explicit success flag. Not derived from ``value``, for the reason in the
    #: class docstring.
    succeeded: bool = False

    @property
    def ok(self) -> bool:
        """Whether the step succeeded.

        True for a successful resolution whose value happens to be ``None``.
        """
        return self.succeeded

    def __bool__(self) -> bool:
        # So `if resolution:` reads correctly at a call site. Defined alongside
        # `ok` so there is only one notion of success, not two that could drift.
        return self.ok

    @property
    def reason_code(self) -> str:
        """The reason as a string, for a decision record.

        ``"OK"`` on success rather than an empty string, so a log line always has
        something in the field and a query for refusals never has to distinguish
        "no reason" from "success".
        """
        return self.reason.value if self.reason is not None else "OK"

    def unwrap(self) -> T:
        """The value, or raise if the step did not succeed.

        For the rare call site that genuinely cannot continue -- a composition
        root, a test. Everywhere else, check :attr:`ok` and return a refusal,
        because raising here would reintroduce exactly the exception-per-expected-
        outcome pattern this type exists to avoid.

        Note that unwrapping a *successful* resolution whose value is ``None``
        returns ``None`` rather than raising: it succeeded, and ``None`` was the
        answer.
        """
        if not self.succeeded:
            raise ValueError(f"resolution failed with {self.reason_code}: {self.explanation}")
        if self.value is None:
            raise TypeError(
                f"unwrap() cannot return {type(None).__name__}; the step succeeded with no "
                f"value, so read `.value` directly"
            )
        return self.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason_code,
            "explanation": self.explanation,
            "details": dict(self.details),
        }


def resolved(value: T, **details: Any) -> Resolution[T]:
    """A successful resolution.

    A function rather than a bare constructor so a call site cannot accidentally
    produce a ``Resolution`` with both a value and a reason -- which would be a
    state with no meaning, and which the properties above would then have to
    guess about.
    """
    return Resolution(value=value, succeeded=True, details=_frozen(details))


def refused(
    reason: RejectionReason,
    explanation: str,
    **details: Any,
) -> Resolution[Any]:
    """A failed resolution, with whatever was worked out on the way."""
    return Resolution(
        value=None,
        reason=reason,
        explanation=explanation,
        details=_frozen(details),
        succeeded=False,
    )
