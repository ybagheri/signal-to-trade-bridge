"""The risk budget: how much money a trade is allowed to lose.

This is the first step that needs facts the bridge does not have yet, and the
reason is the substance of the whole project rather than an adapter detail.
Neither upstream project can supply them:

* ``albrooks`` treats a symbol as an opaque ``str`` and has no account layer at
  all. The Phase 0 audit verified that ``balance``, ``equity`` and ``margin``
  appear nowhere in either upstream tree.
* ``auto-trade`` drives the MetaTrader order dialog by clicking on it. It can only
  read what a human could see on screen, and the balance is not one of those
  things.

So the rule this module enforces is the same one :mod:`stops` enforces, one level
up:

    **There is no code path that invents an account balance.**

If the balance is unavailable, the answer is a refusal. Not a remembered figure,
not a configured default, not the last value that was seen. A position sized
against an invented balance is arithmetically correct and financially meaningless,
and it is arithmetically correct in a way that is very hard to notice afterwards.
The companion test walks this module's AST for the same reason the stop resolver's
does: the rule has to be enforced by something, not merely written down.

What this module does *not* do is fetch the balance. It cannot: the
:class:`~signal_to_trade_bridge.ports.AccountProvider` protocol lives in
``ports``, and ``domain`` is forbidden from importing it -- that boundary is
enforced by ``test_domain_isolation`` walking the package's AST. So the domain
takes the facts as arguments and the application layer obtains them. A useful
consequence of the layering, rather than a cost of it: everything in here is a
pure function of its arguments, so it is testable without a terminal, a broker or
an account.

The currency check lives here rather than in :mod:`sizing` even though sizing is
where the tick values are used, because the question it asks -- "is the money I
am about to divide denominated in the same currency as the number I am going to
divide it by?" -- is about the *money facts* being coherent, and it is answerable
before any size is computed. Answering it early means a mismatch is reported as a
mismatch rather than as a volume that looks plausible.
"""

from __future__ import annotations

from signal_to_trade_bridge.domain.enums import RejectionReason
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    RiskBudget,
    RiskParameters,
    SymbolSpec,
)
from signal_to_trade_bridge.domain.resolution import Resolution, refused, resolved

__all__ = ["check_currency_compatibility", "resolve_risk_budget"]


def resolve_risk_budget(
    balance: AccountBalance | None,
    risk: RiskParameters,
) -> Resolution[RiskBudget]:
    """The maximum planned loss for this trade, or a refusal.

    ``balance * risk_percent / 100``. The division by 100 is what makes
    ``risk_percent = 0.5`` mean half a percent rather than half the account.

    ``balance`` is ``None``-able rather than required, because "the terminal did
    not answer" is a real outcome and not a programming error. The provider port
    raises when it cannot reach the account, and the application layer converts
    that into ``None`` here; modelling the absence in the signature means every
    caller has to decide what to do about it, instead of only the ones who
    remembered.

    The percentage is re-checked even though :class:`RiskParameters` validates it
    at construction. That is the same deliberate duplication as the stop-side
    check in :func:`~signal_to_trade_bridge.domain.validation.validate_geometry`:
    this function is public, it can be called directly, and a risk calculation
    that trusts its input to have been validated somewhere else is a risk
    calculation that fails the first time somebody constructs the parameters by
    hand.
    """
    if balance is None:
        return refused(
            RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE,
            (
                "the account balance is not available, so there is no budget to size a "
                "position against. This bridge does not substitute a remembered, configured "
                "or default balance: a position sized against an invented balance is "
                "arithmetically correct and financially meaningless."
            ),
            risk_percent=str(risk.risk_percent),
        )

    details: dict[str, object] = {
        "balance": str(balance.effective_balance),
        "risk_percent": str(risk.risk_percent),
        "currency": balance.currency,
        "account_login": balance.account_login,
        "open_positions": balance.open_positions,
    }

    effective = balance.effective_balance
    if effective <= 0:
        # `AccountBalance` refuses a non-positive balance at construction, so
        # reaching this means the value arrived by a path that skipped it --
        # a deserialiser, or a future provider that builds the object loosely.
        # Checked because this is the function that decides how much money the
        # bridge is about to risk.
        return refused(
            RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE,
            (
                f"the account reports a balance of {effective}, which cannot be used as the "
                f"basis for a risk budget. A percentage of nothing is not a small budget; it "
                f"is an unbounded one."
            ),
            **details,
        )

    percent = risk.risk_percent
    if percent <= 0 or percent > 100:
        return refused(
            RejectionReason.INVALID_RISK_PARAMETERS,
            (
                f"the configured risk percentage is {percent}, which is not a fraction of an "
                f"account. It must be greater than zero and no greater than 100."
            ),
            **details,
        )

    budget = RiskBudget(
        amount=risk.risk_amount(effective),
        balance=effective,
        risk_percent=percent,
        currency=balance.currency,
        reward_amount=risk.reward_amount(effective),
        reward_risk_ratio=risk.reward_risk_ratio,
    )
    return resolved(
        budget,
        risk_amount=str(budget.amount),
        reward_amount=str(budget.reward_amount),
        reward_risk_ratio=str(budget.reward_risk_ratio),
        **details,
    )


def check_currency_compatibility(
    balance: AccountBalance,
    spec: SymbolSpec,
) -> Resolution[SymbolSpec]:
    """Whether the account's money and the symbol's tick values are the same money.

    MT5 quotes ``trade_tick_value_profit`` in the account (deposit) currency for
    one lot. A position size is that budget divided by ``ticks * tick_value``, and
    the division is only meaningful when the numerator and the denominator are
    denominated the same way. Dividing dollars by euros produces a number that
    looks exactly like a volume and is not one, which is why this is checked here
    rather than left to the result looking plausible.

    **Only the profit currency is checked.** The margin currency legitimately
    differs from the account currency -- EURUSD margin is quoted in EUR on a USD
    account, and that is normal rather than a fault -- so it is recorded and not
    refused. Refusing it would make the bridge refuse every forex pair on a USD
    account, which is the opposite of useful.

    **An unstated profit currency is refused rather than assumed.** A
    specification that does not say what its tick values are denominated in
    cannot be checked, and a check that passes on missing data is not a check. The
    MT5 adapter of Phase 7 populates the field from ``SYMBOL_CURRENCY_PROFIT``, so
    an empty one means the adapter did not run, not that the value is fine.
    """
    account_currency = balance.currency.strip().upper()
    profit_currency = spec.currency_profit.strip().upper()

    details: dict[str, object] = {
        "symbol": spec.symbol_normalised,
        "account_currency": account_currency,
        "currency_profit": spec.currency_profit or None,
        "currency_base": spec.currency or None,
        "currency_margin": spec.currency_margin or None,
        "tick_value_profit": str(spec.tick_value_profit),
        "tick_value_loss": str(spec.tick_value_loss),
        "conservative_tick_value": str(spec.conservative_tick_value),
    }

    if not profit_currency:
        return refused(
            RejectionReason.SYMBOL_SPEC_UNAVAILABLE,
            (
                f"the specification for {spec.symbol_normalised} does not state the currency its "
                f"tick values are denominated in. Without it, the risk budget cannot be divided "
                f"by the tick value in any meaningful way, so the trade is refused rather than "
                f"computed against an assumed currency."
            ),
            **details,
        )

    if profit_currency != account_currency:
        return refused(
            RejectionReason.INVALID_RISK_PARAMETERS,
            (
                f"the account is denominated in {account_currency} but {spec.symbol_normalised} "
                f"reports its tick values in {profit_currency}. A position size divides the risk "
                f"budget by the tick value, and dividing across two currencies produces a number "
                f"that looks like a volume without being one."
            ),
            **details,
        )

    return resolved(spec, currencies_match=True, **details)
