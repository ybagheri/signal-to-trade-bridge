"""The dry run: turning a validated intent into the order that *would* be sent.

Phase 6 made ``DRY_RUN`` an honest placeholder — "every check passed, nothing was
sent, and nothing could have been". That is true and it is not much use. A dry run
exists to answer one question before real money is involved: **what exactly would
this system do?**

So this module makes it answer. Two pieces:

* :func:`build_execution_request` — the missing link from
  :class:`~domain.models.TradeIntent` to
  :class:`~domain.models.ExecutionRequest`. Until Phase 7b nothing in the codebase
  constructed one, which means the boundary type the executor accepts had no
  producer: the mapping was tested by hand in a test fixture and nowhere else.
  **This is the actual content of Phase 8.**
* :class:`DryRunReport` — the artefact a human reads before enabling execution. It
  carries the request, the arithmetic behind it, and the two things that most often
  decide whether a "passing" trade would really be placed: whether execution is
  enabled at all, and **whether the downstream project's own risk gates would
  accept it**.

### Why the downstream gates are checked here, and it matters more than it sounds

The bridge validates against *its own* rules and then hands the order to
`auto-trade`, which validates it against *its* rules again. Those are two
independent rulebooks:

| Check | Owner | Default |
|---|---|---|
| risk per trade | this bridge | 0.5% of balance |
| minimum volume | this bridge | refuse below broker minimum |
| allowed symbols | `auto-trade` | `EURUSD,XAUUSD,YM` |
| maximum volume | `auto-trade` | `1.0` |
| orders per minute | `auto-trade` | `5` |

**A signal can pass every check in this bridge and still be refused downstream** —
`GBPJPY` is not in `auto-trade`'s default allow-list, and a 2-lot order exceeds its
default cap regardless of how small the bridge decided the risk was. Before this
phase that gap was invisible until the order was refused, which is the worst moment
to learn it: in production, on a live account, with the reason arriving as a
`REJECTED` result instead of a decision.

So the dry run asks `auto-trade`'s own `RiskEngine` what it would say, and reports
the answer. It is a **prediction, not a guarantee** — the real gates also depend on
account type, the kill switch, the terminal, and the ledger — and the report says so
in those words rather than claiming to have placed anything.

### What this module deliberately does not do

**It does not reference a `TradeExecutor`.** Not an import, not a type hint, not a
call. `test_this_module_holds_no_executor` asserts that the pipeline imports none,
and this module is in the pipeline's package.

That constraint looks awkward — an executor exists two packages away — and it is the
single most important rule in the project right now. Execution arrives with the
idempotency ledger and the kill switch around it (Phase 9). An executor reachable
from the pipeline before then is a second way to open a duplicate position, and the
one thing this architecture was drawn to prevent is a trade that happens twice.

So what this module *does* is construct the request and answer "what would happen",
which is safe precisely because it can do nothing. It builds an
:class:`ExecutionRequest` and calls `RiskEngine.validate` — a pure function of its
inputs — and never touches a terminal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from signal_to_trade_bridge.domain.enums import Direction
from signal_to_trade_bridge.domain.models import (
    ExecutionRequest,
    TradeIntent,
)

__all__ = [
    "DownstreamVerdict",
    "DryRunReport",
    "build_execution_request",
    "default_comment",
    "direction_word",
    "report_for",
]


def build_execution_request(intent: TradeIntent) -> ExecutionRequest:
    """The order this intent would send, as an explicit boundary object.

    **The numbers are copied, not recomputed.** ``volume`` is the intent's sized
    volume, ``entry`` its entry, ``stop_loss`` the resolved price and ``take_profit``
    the target price or ``None``. A dry run that re-derived any of them would be a
    second sizing pass, and two sizing passes that disagree produce a report
    describing an order the bridge would never actually send -- which is worse than
    no report, because it looks authoritative.

    ``take_profit=None`` is the ``TakeProfitSource.NONE`` policy -- exits managed
    elsewhere -- and it is passed through as absent rather than refused. Phase 8
    raised here instead, because ``ExecutionRequest.take_profit`` was required while
    this project's own ``TradeIntent.take_profit`` and ``auto-trade``'s
    ``TradeSignal.take_profit`` are both optional. **Phase 9 made the field
    optional**, and this function had no branch to remove: the same expression
    serves both cases.
    """
    return ExecutionRequest(
        signal_id=intent.signal.signal_id,
        symbol=intent.symbol,
        direction=intent.direction,
        volume=intent.volume,
        entry=intent.entry,
        stop_loss=intent.stop_loss.price,
        take_profit=None if intent.take_profit is None else intent.take_profit.price,
        comment=default_comment(intent),
        strategy=intent.signal.setup_id or "",
        # Carried for traceability and explicitly not a probability. See
        # `ExecutionRequest.evidence_score`.
        evidence_score=intent.signal.evidence_score,
        metadata=dict(intent.to_dict()),
    )


def default_comment(intent: TradeIntent) -> str:
    """The order comment, which MT5 shows the trader in the terminal.

    A short, factual line naming the setup. **Not** the whole decision: MT5
    truncates comments, and a comment long enough to be truncated is a comment
    whose useful half is gone. The full record is in ``metadata`` and in the log.

    No risk figure in it. A comment travels to the broker and back out again on the
    account history, and a bridge that prints its own risk budget into a broker's
    record is publishing an operating parameter to a third party.
    """
    setup = intent.signal.setup_id or "signal"
    return f"stb {intent.symbol} {intent.direction.value} {setup}"[:64]


@dataclass(frozen=True, slots=True)
class DownstreamVerdict:
    """What the execution project's own risk engine would say.

    A **prediction**, and the field names say so. ``accepted`` is what its
    ``RiskEngine`` returns for these inputs *right now*; it is not a promise about
    what will happen when the order is actually sent, because the real gates also
    depend on the account type, the kill switch, the terminal window and the ledger.
    """

    #: Whether ``auto-trade``'s ``RiskEngine`` would accept the request as sent.
    accepted: bool

    #: Its refusal message verbatim, when it refuses. The upstream messages are
    #: fixed strings ("symbol is not allowed", "volume exceeds configured limit"),
    #: so this is both human-readable and matchable in a test.
    reason: str = ""

    #: Whether the check actually ran. ``False`` means the verdict is "unknown",
    #: which is **not** the same as "accepted" and is never collapsed into it --
    #: an unevaluated downstream gate is exactly the gap this class exists to
    #: surface, so it reports itself as unevaluated rather than as a pass.
    evaluated: bool = True

    #: Why it was not evaluated. Present when ``evaluated`` is ``False``.
    unevaluated_because: str = ""

    def __post_init__(self) -> None:
        if self.accepted and not self.evaluated:
            # An unevaluated gate cannot have accepted. Allowing it would let a
            # missing engine read as a passing check, which is the one direction
            # this class refuses to be wrong in.
            raise ValueError("an unevaluated downstream gate cannot be accepted")

    @classmethod
    def not_evaluated(cls, because: str) -> DownstreamVerdict:
        return cls(accepted=False, evaluated=False, unevaluated_because=because)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "evaluated": self.evaluated,
            "unevaluated_because": self.unevaluated_because,
        }


@dataclass(frozen=True, slots=True)
class DryRunReport:
    """What would have happened, in full, and why nothing was sent.

    Deliberately a **report** rather than a decision. It is not an outcome: no
    order exists, no gate was applied to a real submission, and a
    :class:`~domain.models.TradeDecision` already carries the decision. Mixing the
    two would invite a caller to treat this as a result.
    """

    #: The order that would be sent. ``None`` when the intent could not be turned
    #: into one -- see :func:`build_execution_request` for the one supported
    #: configuration where that happens, and why the report still exists.
    request: ExecutionRequest | None

    #: The intent's own arithmetic, so a reader can see the reasoning rather than
    #: only the conclusion.
    risk_amount: Decimal
    planned_loss: Decimal
    reward_to_risk: Decimal | None
    volume: Decimal

    # What the execution project's own gates would say. See
    # :class:`DownstreamVerdict`.
    downstream: DownstreamVerdict

    #: Whether the bridge is configured to execute at all. ``False`` on the default
    #: configuration, and the reason a dry run is the *only* outcome available.
    execution_enabled: bool

    #: Whether dry-run mode is on. Independent of the above: both ``False`` is a
    #: live order, which is a different thing from either one alone.
    dry_run: bool

    #: The account and spec the sizing was computed from. Recorded so a report read
    #: later can be checked against the balance it used, and not silently believed.
    balance: Decimal
    currency: str
    contract_size: Decimal | None = None
    symbol_spec_source: str = ""

    #: Free-form additions, for callers that want to record something of their own
    #: without this class growing a field per idea.
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def would_be_sent(self) -> bool:
        """Whether this request *would* be placed, if the blockers were removed.

        Both conditions, and both reported separately: a bridge with execution
        enabled but dry-run on is not going to place anything, and neither is one
        with execution disabled. A single boolean would hide which of the two is
        the blocker.
        """
        return self.execution_enabled and not self.dry_run

    @property
    def downstream_would_accept(self) -> bool:
        """Whether the execution project's gates would admit the request.

        ``False`` when unevaluated. See :attr:`DownstreamVerdict.evaluated` for why
        "unknown" is not "yes".
        """
        return self.downstream.evaluated and self.downstream.accepted

    def blockers(self) -> tuple[str, ...]:
        """Everything standing between this report and a placed order.

        Every blocker, not the first. A report that names only the first blocker
        sends an operator through them one at a time, and each fix reveals the
        next, which is the slowest possible way to answer "can this trade ever
        run here?".
        """
        found: list[str] = []
        if self.request is None:
            found.append("the intent could not be expressed as an execution request")
        if not self.execution_enabled:
            found.append("execution is not enabled (BRIDGE_EXECUTION_ENABLED)")
        if self.dry_run:
            found.append("dry-run mode is on (BRIDGE_DRY_RUN)")
        if not self.downstream.evaluated:
            found.append(
                f"the downstream gates were not evaluated: {self.downstream.unevaluated_because}"
            )
        elif not self.downstream.accepted:
            found.append(f"the downstream gates would refuse it: {self.downstream.reason}")
        return tuple(found)

    def to_dict(self) -> dict[str, Any]:
        """The whole report as plain data, for a log line or an audit record.

        Nested one level under ``request`` rather than flattened, because a flat
        record has two ``symbol``-shaped fields and a reader cannot tell which
        belongs to the order and which to the arithmetic that produced it.
        """
        return {
            "request": self.request.to_dict() if self.request is not None else None,
            "would_be_sent": self.would_be_sent,
            "downstream_would_accept": self.downstream_would_accept,
            "downstream": self.downstream.to_dict(),
            "blockers": list(self.blockers()),
            "arithmetic": {
                "volume": str(self.volume),
                "risk_amount": str(self.risk_amount),
                "planned_loss": str(self.planned_loss),
                "reward_to_risk": None if self.reward_to_risk is None else str(self.reward_to_risk),
                "balance": str(self.balance),
                "currency": self.currency,
                "contract_size": None if self.contract_size is None else str(self.contract_size),
                "symbol_spec_source": self.symbol_spec_source,
            },
            "configuration": {
                "execution_enabled": self.execution_enabled,
                "dry_run": self.dry_run,
            },
            **dict(self.extra),
        }


def report_for(
    intent: TradeIntent,
    *,
    execution_enabled: bool,
    dry_run: bool,
    ask_downstream: Callable[[ExecutionRequest], DownstreamVerdict] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> DryRunReport:
    """Build the report for a validated intent.

    :param ask_downstream: calls the execution project's ``RiskEngine`` for this
        request and returns its verdict. **Optional, and ``None`` produces an
        unevaluated verdict rather than an accepted one** -- see
        :class:`DownstreamVerdict`. Callers that have no engine wired should see
        "not checked", not a green tick.

        The signature takes the request rather than the intent so a caller cannot
        accidentally check the downstream gates against something other than the
        order it is about to describe.

    ``request`` is not optional any more. It was, in Phase 8, because
    ``ExecutionRequest`` required a take profit and an intent under
    ``TakeProfitSource.NONE`` had none; Phase 9 made the field optional, so every
    validated intent now produces a request. The field stays nullable on
    :class:`DryRunReport` because a caller holding a hand-built report should not
    have to prove it is well-formed, but a report built here always has one.
    """
    request = build_execution_request(intent)

    if ask_downstream is None:
        downstream = DownstreamVerdict.not_evaluated(
            "no downstream risk engine is wired into this dry run"
        )
    else:
        downstream = ask_downstream(request)

    return DryRunReport(
        request=request,
        risk_amount=intent.risk_amount,
        planned_loss=intent.position_size.planned_loss,
        reward_to_risk=intent.reward_to_risk,
        volume=intent.volume,
        downstream=downstream,
        execution_enabled=execution_enabled,
        dry_run=dry_run,
        balance=intent.account_balance.balance,
        currency=intent.account_balance.currency,
        contract_size=getattr(intent.symbol_spec, "contract_size", None),
        symbol_spec_source=getattr(intent.symbol_spec, "source", "") or "",
        extra=dict(extra or {}),
    )


def direction_word(direction: Direction) -> str:
    """The direction as the words a person would use in a sentence.

    Not a rename for the sake of it: this exists so the comment and the report can
    be read by someone, and ``LONG``/``SHORT`` in a broker's order history is jargon
    where "long" is not.
    """
    return "buy" if direction is Direction.LONG else "sell"
