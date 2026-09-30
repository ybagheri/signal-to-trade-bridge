"""Risk management as a service: obtain the facts, then apply the policies.

The domain holds the arithmetic and the refusals; this holds the wiring. That
split is forced by the architecture rather than chosen for tidiness. ``domain``
is forbidden from importing ``ports`` -- ``test_domain_isolation`` walks the
package's AST and fails on the edge -- so the domain cannot ask an
:class:`~signal_to_trade_bridge.ports.AccountProvider` for anything. It takes the
balance and the specification as arguments and computes from them. Somebody has to
close the gap, and that somebody is the application layer.

Which is also where the log events belong. The domain has no logger and must not
acquire one: a domain that wrote its own log lines would be a domain whose
behaviour changed with the logging configuration. So ``RISK_CALCULATED`` and
``POSITION_SIZED`` are emitted here, over the same resolutions the domain
returned, and the fields are the resolution's own ``details`` rather than
something reassembled -- so a log line cannot disagree with the decision.

**Both events are emitted on refusals too.** A size refused because the budget is
too small for this instrument's stop distance is one of the most useful lines in
the whole log: it says the configuration and the market do not fit together, and
it says it with the numbers. An event emitted only on success would leave an
operator with a silence and no way to tell a refusal from a signal that never
arrived.

There is no decision pipeline here yet -- no signal, no stop resolution, no
validation, no executor. That is Phase 6's ``ProcessSignal``. This service exists
because the sizing arithmetic needs something to obtain the account and symbol
facts with, and the fakes of Phase 4 need a caller to be worth having.
"""

from __future__ import annotations

from typing import Any

from signal_to_trade_bridge.domain.enums import RejectionReason
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    PositionSize,
    RiskBudget,
    RiskParameters,
    StopLoss,
    SymbolSpec,
)
from signal_to_trade_bridge.domain.resolution import Resolution, refused
from signal_to_trade_bridge.domain.risk import check_currency_compatibility, resolve_risk_budget
from signal_to_trade_bridge.domain.sizing import resolve_position_size
from signal_to_trade_bridge.infrastructure.logging import Event, StructuredLogger, get_logger
from signal_to_trade_bridge.ports import AccountProvider, SymbolSpecProvider

__all__ = ["RiskService"]


class RiskService:
    """Computes the risk budget and the position size for one trade.

    Every method returns a
    :class:`~signal_to_trade_bridge.domain.resolution.Resolution` and none raises
    for an ordinary refusal. That is the same contract the domain functions have,
    and it is deliberate at this layer too: **a terminal that is not running is
    an expected condition**, and a trading loop that crashed on one would stop
    processing the signals it could still act on.
    """

    def __init__(
        self,
        account: AccountProvider,
        symbols: SymbolSpecProvider,
        *,
        logger: StructuredLogger | None = None,
    ) -> None:
        self._account = account
        self._symbols = symbols
        self._log = logger or get_logger("application.risk")

    # -- obtaining the facts ---------------------------------------------

    def read_account(self) -> AccountBalance | None:
        """The account state, or ``None`` when it could not be read.

        ``None`` rather than an exception, because "we could not ask" is a value
        the caller has to handle explicitly and an exception is something a
        caller can forget to handle. The port raises, and that raise is caught
        here -- once, in one place -- rather than in every caller that happens to
        need a balance.

        The ``except`` is deliberately broad. The port's contract is "raises when
        the terminal is unreachable" without saying which exception that will be:
        the MetaTrader bindings raise several unrelated types, and a future
        provider could raise something new. Narrowing this to a known list would
        mean a new failure mode escaped as an exception into a trading loop,
        which is the outcome the ``None`` exists to prevent. The refusal message
        downstream carries the type, so the information is not lost.
        """
        try:
            return self._account.balance()
        except Exception:
            return None

    def read_spec(self, symbol: str) -> SymbolSpec | None:
        """A symbol's specification, or ``None`` when it could not be read.

        Same reasoning as :meth:`read_account`, and for a more pointed reason: an
        unknown symbol raises rather than returning a default, and a default
        specification would be an invented contract. The refusal is the answer.
        """
        try:
            return self._symbols.spec(symbol)
        except Exception:
            return None

    # -- step 7: the risk amount -----------------------------------------

    def risk_budget(
        self,
        risk: RiskParameters,
        *,
        symbol: str = "",
    ) -> Resolution[RiskBudget]:
        """The maximum planned loss, with ``RISK_CALCULATED`` emitted either way."""
        balance = self.read_account()
        resolution = resolve_risk_budget(balance, risk)

        fields: dict[str, Any] = {
            "symbol": symbol or None,
            "balance": str(balance.balance) if balance is not None else None,
            "currency": balance.currency if balance is not None else None,
            "account_login": balance.account_login if balance is not None else None,
            "open_positions": balance.open_positions if balance is not None else None,
            "risk_percent": str(risk.risk_percent),
            "reward_risk_ratio": str(risk.reward_risk_ratio),
            "ok": resolution.ok,
            "reason": resolution.reason_code,
            "explanation": resolution.explanation,
            **dict(resolution.details),
        }
        budget = resolution.value
        if isinstance(budget, RiskBudget):
            fields["risk_amount"] = str(budget.amount)
            fields["reward_amount"] = str(budget.reward_amount)

        if resolution.ok:
            self._log.event(Event.RISK_CALCULATED, **fields)
        else:
            self._log.warning(Event.RISK_CALCULATED, **fields)
        return resolution

    # -- step 8: the position size ---------------------------------------

    def position_size(
        self,
        symbol: str,
        stop: StopLoss,
        risk: RiskParameters,
    ) -> Resolution[PositionSize]:
        """The volume for this trade, with ``POSITION_SIZED`` emitted either way.

        Three refusals are possible before any arithmetic, in this order:

        1. the account did not answer, or the balance is unusable;
        2. the symbol did not answer, or its specification is incomplete;
        3. the account's money and the symbol's tick values are different
           currencies, so the division below would be meaningless.

        The order matters because the first two are *facts missing* and the third
        is *facts that contradict each other*. An operator debugging a refusal
        needs to know which they are looking at, and a single ``SIZING_FAILED``
        would not tell them.
        """
        budget_resolution = self.risk_budget(risk, symbol=symbol)
        if not budget_resolution.ok:
            return self._log_size(
                symbol,
                refused(
                    budget_resolution.reason or RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE,
                    budget_resolution.explanation,
                    **dict(budget_resolution.details),
                ),
            )
        budget = budget_resolution.unwrap()

        spec = self.read_spec(symbol)
        if spec is None:
            return self._log_size(
                symbol,
                refused(
                    RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
                    (
                        f"no trading specification could be read for {symbol}. Position sizing "
                        f"needs a tick size, a tick value and the broker's volume constraints, "
                        f"and none of them is available from either upstream project: the "
                        f"price-action engine treats a symbol as an opaque string and the "
                        f"execution project drives the order dialog by clicking on it. The trade "
                        f"is refused rather than sized against a guessed contract."
                    ),
                    symbol=symbol,
                ),
            )

        currency = check_currency_compatibility(_account_for(budget), spec)
        if not currency.ok:
            return self._log_size(
                symbol,
                refused(
                    currency.reason or RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
                    currency.explanation,
                    **dict(currency.details),
                ),
            )

        return self._log_size(symbol, resolve_position_size(stop, budget, spec))

    def _log_size(
        self,
        symbol: str,
        resolution: Resolution[Any],
    ) -> Resolution[PositionSize]:
        """Emit ``POSITION_SIZED`` over a resolution, and pass it through.

        The event carries the resolution's own ``details``, so the log cannot
        report a different computation from the one that was made. Emitted on
        refusals as well as successes, at a different level, because a size that
        was refused is information rather than an absence of it.
        """
        fields: dict[str, Any] = {
            "symbol": symbol,
            "ok": resolution.ok,
            "reason": resolution.reason_code,
            "explanation": resolution.explanation,
            **dict(resolution.details),
        }
        size = resolution.value
        if isinstance(size, PositionSize):
            fields["volume"] = str(size.volume)
            fields["raw_volume"] = str(size.raw_volume)
            fields["planned_loss"] = str(size.planned_loss)
            fields["risk_amount"] = str(size.risk_amount)
            fields["rounded"] = size.rounded
            fields["clamped_to_maximum"] = size.clamped_to_maximum
            fields["within_budget"] = size.is_within_budget

        if resolution.ok:
            self._log.event(Event.POSITION_SIZED, **fields)
        else:
            self._log.warning(Event.POSITION_SIZED, **fields)
        return resolution


def _account_for(budget: RiskBudget) -> AccountBalance:
    """The account, rebuilt from the budget that was resolved from it.

    The currency check needs an :class:`AccountBalance` and only the budget
    survives this far. Rebuilding it -- rather than threading the original
    through -- keeps :class:`RiskBudget` the single record of the money facts, and
    the two values here are exactly the ones the budget was built from, so there
    is nothing to get out of step.

    ``AccountBalance`` validates on construction, so a budget carrying a
    non-positive balance also fails here loudly rather than reaching a currency
    comparison it should never have been allowed to.
    """
    return AccountBalance(balance=budget.balance, currency=budget.currency)
