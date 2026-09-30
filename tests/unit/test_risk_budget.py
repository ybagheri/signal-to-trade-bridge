"""The risk budget.

Organised around the rule the module enforces:

    **There is no code path that invents an account balance.**

So there is a test that asserts no such path exists, a test that the refusal
names the missing fact rather than substituting one, and tests for the currency
coherence that decides whether the budget can be divided at all.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain import risk as module
from signal_to_trade_bridge.domain.enums import RejectionReason, TakeProfitSource
from signal_to_trade_bridge.domain.models import AccountBalance, RiskParameters, SymbolSpec
from signal_to_trade_bridge.domain.risk import check_currency_compatibility, resolve_risk_budget


def _account(currency: str = "USD", balance: str = "10000") -> AccountBalance:
    """A dollar account, so 0.5% is a round $50."""
    return AccountBalance(
        balance=Decimal(balance),
        currency=currency,
        equity=Decimal(balance),
        open_positions=0,
        account_login=12345678,
        server="Alpari-Demo",
    )


def _spec(**overrides: object) -> SymbolSpec:
    """A gold specification: 0.01 ticks at $1, so $300 a lot over a $3.00 stop."""
    defaults: dict[str, object] = {
        "symbol": "XAUUSD",
        "contract_size": "100",
        "tick_size": "0.01",
        "tick_value_profit": "1.0",
        "tick_value_loss": "1.0",
        "volume_min": "0.01",
        "volume_max": "50.0",
        "volume_step": "0.01",
        "digits": 2,
        "point": "0.01",
        "currency": "USD",
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    defaults.update(overrides)
    return SymbolSpec(**_decimals(defaults))  # type: ignore[arg-type]


#: The ``SymbolSpec`` fields that are numeric. Everything else is a string -- a
#: symbol name is not a number, and ``Decimal("EURUSD")`` is an error rather than
#: a conversion.
_NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        "contract_size",
        "tick_size",
        "tick_value_profit",
        "tick_value_loss",
        "volume_min",
        "volume_max",
        "volume_step",
        "point",
    }
)


def _decimals(defaults: dict[str, object]) -> dict[str, object]:
    """The numeric fields as ``Decimal``, everything else untouched.

    The builders above are written with string literals so the numbers read as the
    numbers a broker publishes -- ``"0.00001"`` rather than ``1e-05`` -- and this
    is where they become the ``Decimal`` the model requires. Converting here rather
    than at each call site means no test can accidentally hand the domain a
    ``float``, which is the one thing the ``Decimal`` decision exists to prevent.
    """
    return {
        key: Decimal(value) if key in _NUMERIC_FIELDS and isinstance(value, str) else value
        for key, value in defaults.items()
    }


def _risk(**overrides: object) -> RiskParameters:
    defaults: dict[str, object] = {
        "risk_percent": Decimal("0.5"),
        "reward_risk_ratio": Decimal("1.0"),
        "take_profit_source": TakeProfitSource.RR_FALLBACK,
    }
    defaults.update(overrides)
    return RiskParameters(**defaults)  # type: ignore[arg-type]


class _UnvalidatedAccount:
    """An account whose balance never went through ``AccountBalance``.

    ``AccountBalance`` refuses a non-positive balance at construction, so the only
    way a zero can reach :func:`resolve_risk_budget` is a path that skipped it --
    a deserialiser, or a future provider building the object loosely. A stub
    rather than an ``object.__new__`` dance, because the point of the test is
    that **the policy re-checks**, and the stub says so in one line.
    """

    balance = Decimal("0")
    currency = "USD"
    equity: Decimal | None = None
    open_positions = 0
    account_login: int | None = None
    server: str | None = None

    @property
    def effective_balance(self) -> Decimal:
        return Decimal("0")


class _UnvalidatedRisk:
    """Risk parameters with a percentage ``RiskParameters`` would have refused."""

    def __init__(self, risk_percent: Decimal) -> None:
        self.risk_percent = risk_percent
        self.reward_risk_ratio = Decimal("1.0")

    def risk_amount(self, balance: Decimal) -> Decimal:
        return balance * self.risk_percent / Decimal(100)

    def reward_amount(self, balance: Decimal) -> Decimal:
        return self.risk_amount(balance) * self.reward_risk_ratio


class TestTheAmount:
    def test_half_a_percent_of_ten_thousand_is_fifty(self) -> None:
        resolution = resolve_risk_budget(_account(), _risk())
        assert resolution.ok
        assert resolution.unwrap().amount == Decimal("50")

    def test_the_division_by_one_hundred_is_what_makes_half_a_percent_half(self) -> None:
        # `risk_percent = 0.5` means half a percent, not half the account. The
        # assertion is written as the arithmetic a human would do, because that
        # is the only kind a reviewer re-derives.
        assert resolve_risk_budget(_account(), _risk()).unwrap().amount == (
            Decimal("10000") * Decimal("0.5") / Decimal(100)
        )

    @pytest.mark.parametrize(
        ("percent", "expected"),
        [("0.25", "25"), ("1", "100"), ("2.5", "250"), ("10", "1000")],
    )
    def test_the_percentage_is_honoured_exactly(self, percent: str, expected: str) -> None:
        budget = resolve_risk_budget(_account(), _risk(risk_percent=Decimal(percent))).unwrap()
        assert budget.amount == Decimal(expected)

    def test_the_reward_is_the_amount_at_the_configured_ratio(self) -> None:
        budget = resolve_risk_budget(_account(), _risk(reward_risk_ratio=Decimal("2"))).unwrap()
        assert budget.reward_amount == Decimal("100")

    def test_the_balance_never_the_equity_is_the_basis(self) -> None:
        # Equity moves with open positions, so sizing on it would make the risk of
        # a new trade depend on trades already running.
        account = AccountBalance(
            balance=Decimal("10000"),
            currency="USD",
            equity=Decimal("5000"),
        )
        assert resolve_risk_budget(account, _risk()).unwrap().balance == Decimal("10000")

    def test_every_input_is_kept_so_the_number_can_be_recomputed(self) -> None:
        # A budget on its own is an assertion. These four are what a reviewer
        # checks by hand, so they travel with it.
        budget = resolve_risk_budget(_account(), _risk()).unwrap()
        assert budget.balance == Decimal("10000")
        assert budget.risk_percent == Decimal("0.5")
        assert budget.currency == "USD"
        assert budget.reward_risk_ratio == Decimal("1.0")

    def test_the_fraction_of_the_balance_is_the_configured_fraction(self) -> None:
        budget = resolve_risk_budget(_account(), _risk()).unwrap()
        assert budget.fraction_of_balance == Decimal("0.005")

    def test_it_serialises(self) -> None:
        payload = resolve_risk_budget(_account(), _risk()).unwrap().to_dict()
        # Compared numerically rather than as a string, because `Decimal`
        # arithmetic preserves an exponent: `10000 * 0.5 / 100` is `50.0`, not
        # `50`. Both are the same money and both round-trip exactly, so asserting
        # the text would be asserting a formatting choice this project has not
        # made a rule about.
        assert Decimal(payload["amount"]) == Decimal("50")
        assert payload["currency"] == "USD"

    def test_the_resolution_reports_what_produced_it(self) -> None:
        details = resolve_risk_budget(_account(), _risk()).details
        assert Decimal(str(details["risk_amount"])) == Decimal("50")
        assert details["balance"] == "10000"


class TestAMissingBalance:
    def test_no_balance_is_refused_rather_than_substituted(self) -> None:
        resolution = resolve_risk_budget(None, _risk())
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE
        assert resolution.value is None

    def test_the_message_says_no_default_is_supplied(self) -> None:
        # The refusal is only useful if it tells the operator what was *not*
        # done. A message that merely said "unavailable" invites somebody to add
        # a fallback.
        assert "does not substitute" in resolve_risk_budget(None, _risk()).explanation

    def test_a_missing_balance_still_records_the_configured_percentage(self) -> None:
        # So a log line can say what the bridge *would* have risked had it known,
        # without the reader assuming it did.
        assert resolve_risk_budget(None, _risk()).details["risk_percent"] == "0.5"

    def test_a_refusal_arrives_as_a_value_not_an_exception(self) -> None:
        # A terminal that is not running is an expected condition. A loop that
        # crashed on one would stop processing the signals it could still act on.
        assert resolve_risk_budget(None, _risk()).reason_code == "ACCOUNT_BALANCE_UNAVAILABLE"

    def test_there_is_no_fallback_that_produces_a_balance(self) -> None:
        """No code path invents an account balance.

        The mirror of ``test_there_is_no_fallback_that_produces_a_stop``, and for
        the same reason. The stop resolver may not invent a stop; this module may
        not invent the money. A balance is the denominator of the entire risk
        calculation, so an invented one would not merely be wrong -- it would be
        wrong in a way the arithmetic downstream could not detect.

        The module's own AST is walked rather than trusting a review, because the
        failure is invisible at runtime: an invented balance produces a plausible
        volume.
        """
        tree = ast.parse(inspect.getsource(module))

        # No numeric literal that could serve as a balance, a percentage, or a
        # money amount. The only permitted numbers are the comparison bounds in
        # the guards, which are compared rather than returned.
        allowed_numbers = {0, 1, 100}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                assert node.value in allowed_numbers, (
                    f"risk.py contains the literal {node.value!r} at line {node.lineno}. Every "
                    f"number in this module is expected to be a comparison bound, not a balance: "
                    f"a literal here is how an invented account balance gets introduced."
                )

        # And no account is ever constructed here. This module reads the account
        # the application layer obtained; it does not create one, and a
        # constructed balance is an invented one.
        constructions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "AccountBalance"
        ]
        assert not constructions, (
            "risk.py constructs an AccountBalance. This module reads the account the application "
            "layer obtained; it does not create one."
        )

        # The budget's amount and its planned gain come from the one implementation
        # of each calculation, so there cannot be a second one here that disagrees
        # with it later.
        budget_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RiskBudget"
        ]
        assert budget_calls, "expected the module to construct a RiskBudget"
        expected_source = {
            "amount": "risk.risk_amount(effective)",
            "reward_amount": "risk.reward_amount(effective)",
        }
        for call in budget_calls:
            for keyword, source in expected_source.items():
                value = next(kw for kw in call.keywords if kw.arg == keyword)
                assert ast.unparse(value.value) == source, (
                    f"the budget's {keyword} came from {ast.unparse(value.value)!r} rather "
                    f"than from RiskParameters. Two implementations of the same calculation "
                    f"would eventually disagree."
                )


class TestImpossibleParameters:
    def test_a_percentage_of_nothing_is_refused(self) -> None:
        resolution = resolve_risk_budget(_UnvalidatedAccount(), _risk())  # type: ignore[arg-type]
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE

    def test_the_message_says_a_zero_balance_is_unbounded_rather_than_small(self) -> None:
        # The distinction that matters: "0.5% of nothing" is not a very small
        # risk budget, it is an undefined one, and dividing by it later produces
        # a nonsense volume rather than a refusal.
        explanation = resolve_risk_budget(_UnvalidatedAccount(), _risk()).explanation  # type: ignore[arg-type]
        assert "unbounded" in explanation

    @pytest.mark.parametrize("percent", ["0", "-1", "150"])
    def test_an_impossible_percentage_is_refused(self, percent: str) -> None:
        # `RiskParameters` refuses these at construction, so reaching the guard
        # requires bypassing it -- which is exactly what a deserialiser would do.
        resolution = resolve_risk_budget(_account(), _UnvalidatedRisk(Decimal(percent)))  # type: ignore[arg-type]
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.INVALID_RISK_PARAMETERS

    def test_the_percentage_is_checked_before_any_arithmetic(self) -> None:
        # A refusal that divided first would report a nonsense amount alongside
        # its reason.
        details = resolve_risk_budget(_account(), _UnvalidatedRisk(Decimal("150"))).details  # type: ignore[arg-type]
        assert "risk_amount" not in details


class TestCurrencyCompatibility:
    def test_matching_currencies_pass(self) -> None:
        resolution = check_currency_compatibility(_account(), _spec())
        assert resolution.ok
        assert resolution.unwrap().symbol_normalised == "XAUUSD"

    def test_a_profit_currency_in_another_currency_is_refused(self) -> None:
        # Dividing dollars by euros produces a number that looks exactly like a
        # volume and is not one. This is the check that catches it.
        resolution = check_currency_compatibility(_account(), _spec(currency_profit="EUR"))
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.INVALID_RISK_PARAMETERS

    def test_the_message_names_both_currencies(self) -> None:
        explanation = check_currency_compatibility(
            _account(), _spec(currency_profit="EUR")
        ).explanation
        assert "USD" in explanation
        assert "EUR" in explanation

    def test_an_unstated_profit_currency_is_refused_rather_than_assumed(self) -> None:
        # A specification that does not say what its tick values are in cannot be
        # checked, and a check that passes on missing data is not a check. The MT5
        # adapter populates this field, so an empty one means the adapter did not
        # run rather than that the value is fine.
        resolution = check_currency_compatibility(_account(), _spec(currency_profit=""))
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.SYMBOL_SPEC_UNAVAILABLE

    def test_the_margin_currency_is_recorded_but_not_refused(self) -> None:
        # EURUSD margin is quoted in EUR on a USD account. That is normal, not a
        # fault, and refusing it would refuse every forex pair on a dollar
        # account.
        spec = _spec(symbol="EURUSD", currency="EUR", currency_margin="EUR")
        resolution = check_currency_compatibility(_account(), spec)
        assert resolution.ok
        assert resolution.details["currency_margin"] == "EUR"

    def test_an_account_in_another_currency_is_matched_against_its_own_spec(self) -> None:
        spec = _spec(currency_profit="EUR")
        assert check_currency_compatibility(_account("EUR"), spec).ok

    def test_the_comparison_ignores_case_and_whitespace(self) -> None:
        assert check_currency_compatibility(_account(), _spec(currency_profit=" usd ")).ok

    def test_the_conservative_tick_value_is_reported(self) -> None:
        # The pass carries it as well as the refusal, so a log line explains
        # which tick value the sizing used without the reader re-deriving it.
        details = check_currency_compatibility(_account(), _spec()).details
        assert details["conservative_tick_value"] == "1.0"
