"""The risk service, and the fakes it reads from.

Two things are being tested together, and that is deliberate. The service exists
because ``domain`` cannot reach ``ports`` -- ``test_domain_isolation`` walks the
package's AST and fails on the edge -- so somebody has to close the gap, and the
fakes exist because neither upstream project has an account balance or a symbol
specification. A test that wired the service to the fakes therefore exercises the
same path the MetaTrader terminal will drive, which is why these are in one file
rather than two: separating them would test the service against doubles of its own
fakes and prove nothing about either.

The subject throughout is **what happens when the facts are missing.** The happy
path is one call and one number; the paths that matter are the ones where the
terminal is not answering, and the assertion in each is that the trade is refused
with a reason rather than sized against something invented.
"""

from __future__ import annotations

import json
from decimal import Decimal
from io import StringIO

import pytest

from signal_to_trade_bridge.adapters.fake import (
    FakeAccountProvider,
    FakeSymbolSpecProvider,
    eurusd_spec,
    gold_spec,
    unusual_spec,
)
from signal_to_trade_bridge.application.risk_service import RiskService
from signal_to_trade_bridge.domain.enums import RejectionReason, StopSource, TakeProfitSource
from signal_to_trade_bridge.domain.errors import IntegrationError
from signal_to_trade_bridge.domain.models import AccountBalance, RiskParameters, StopLoss
from signal_to_trade_bridge.infrastructure.logging import configure_logging
from signal_to_trade_bridge.ports import AccountProvider, SymbolSpecProvider

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _logs() -> StringIO:
    """Capture the bridge's own log output for the whole module.

    Autouse because the logging here is behaviour under test, not a side effect:
    ``RISK_CALCULATED`` and ``POSITION_SIZED`` were reserved in Phase 1 and are
    emitted for the first time in this phase, including on refusals, and that is
    only checkable by reading the log.
    """
    stream = StringIO()
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    return stream


def _account(balance: str = "10000", currency: str = "USD") -> AccountBalance:
    return AccountBalance(
        balance=Decimal(balance),
        currency=currency,
        equity=Decimal(balance),
        account_login=12345678,
        server="Alpari-Demo",
    )


def _risk(**overrides: object) -> RiskParameters:
    defaults: dict[str, object] = {
        "risk_percent": Decimal("0.5"),
        "reward_risk_ratio": Decimal("1.0"),
        "take_profit_source": TakeProfitSource.RR_FALLBACK,
    }
    defaults.update(overrides)
    return RiskParameters(**defaults)  # type: ignore[arg-type]


def _stop(distance: str = "0.00300", price: str = "1.09700") -> StopLoss:
    return StopLoss(price=Decimal(price), distance=Decimal(distance), source=StopSource.SIGNAL)


def _service(
    *,
    balance: AccountBalance | None = None,
    specs: dict[str, object] | None = None,
) -> tuple[RiskService, FakeAccountProvider, FakeSymbolSpecProvider]:
    account = FakeAccountProvider(balance if balance is not None else _account())
    symbols = FakeSymbolSpecProvider(specs or {"EURUSD": eurusd_spec()})  # type: ignore[arg-type]
    return RiskService(account, symbols), account, symbols


def _events(stream: StringIO, name: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if line.strip() and json.loads(line).get("event") == name
    ]


class TestTheHappyPath:
    def test_a_sized_trade_comes_back_from_the_account_and_the_symbol(self) -> None:
        service, _, _ = _service()
        size = service.position_size("EURUSD", _stop(), _risk())
        assert size.ok
        assert size.unwrap().volume == Decimal("0.16")

    def test_the_balance_is_read_once_per_call(self) -> None:
        # So a test can assert a refused trade did not keep asking a terminal that
        # was not answering.
        service, account, _ = _service()
        service.position_size("EURUSD", _stop(), _risk())
        assert account.calls == 1

    def test_the_size_follows_a_balance_that_changes(self) -> None:
        # A closed position changes the balance, and the next trade must be sized
        # on the new one rather than on the number read earlier.
        service, account, _ = _service()
        service.position_size("EURUSD", _stop(), _risk())
        account.set_balance(_account(balance="20000"))
        doubled = service.position_size("EURUSD", _stop(), _risk())
        assert doubled.unwrap().volume == Decimal("0.33")

    def test_the_risk_percentage_is_honoured_end_to_end(self) -> None:
        service, _, _ = _service()
        size = service.position_size("EURUSD", _stop(), _risk(risk_percent=Decimal("1.0")))
        assert size.unwrap().volume == Decimal("0.33")

    def test_the_symbol_is_looked_up_case_insensitively(self) -> None:
        # MT5 is case-insensitive, so a signal naming `eurusd` has to work. A
        # fake that was stricter than the terminal would let this pass here and
        # fail in production.
        service, _, _ = _service()
        assert service.position_size("  eurusd ", _stop(), _risk()).ok

    def test_gold_and_forex_reach_the_same_size_by_different_arithmetic(self) -> None:
        forex, _, _ = _service()
        gold, _, _ = _service(
            specs={"XAUUSD": gold_spec()},
        )
        forex_size = forex.position_size("EURUSD", _stop(), _risk()).unwrap()
        gold_size = gold.position_size(
            "XAUUSD", _stop(distance="3.00", price="2397.00"), _risk()
        ).unwrap()
        assert forex_size.risk_per_unit == gold_size.risk_per_unit
        assert forex_size.volume == gold_size.volume

    def test_an_asymmetric_symbol_is_sized_on_its_worse_tick_value(self) -> None:
        # The Phase 1 correction, verified through the whole service rather than
        # only through the model. `unusual_spec` reports $0.50 a tick in profit
        # and $2.00 in loss, with a 0.05 tick size: a 2.50 stop is 50 ticks, so
        # $100 a lot against the worse value and $25 a lot against the cheaper
        # one. Dividing by the cheaper tick -- which is what `min` did -- would
        # have given 2.0 lots, four times the correct size.
        service, _, _ = _service(specs={"ODDPAIR": unusual_spec()})
        size = service.position_size("ODDPAIR", _stop(distance="2.50"), _risk()).unwrap()
        assert size.tick_value == Decimal("2.0")
        assert size.risk_per_unit == Decimal("100")
        assert size.volume == Decimal("0.5")
        assert size.planned_loss <= Decimal("50")


class TestWhenTheAccountIsUnavailable:
    def test_an_unreachable_terminal_refuses_the_trade(self) -> None:
        service, account, _ = _service()
        account.fail_with(IntegrationError("terminal is not running"))
        size = service.position_size("EURUSD", _stop(), _risk())
        assert size.ok is False
        assert size.reason is RejectionReason.ACCOUNT_BALANCE_UNAVAILABLE

    def test_an_account_with_no_balance_configured_refuses_the_trade(self) -> None:
        # The fake with nothing in it stands in for a terminal that has not been
        # asked yet. It must read as "no balance", not as a zero balance.
        service = RiskService(FakeAccountProvider(), FakeSymbolSpecProvider())
        assert service.position_size("EURUSD", _stop(), _risk()).ok is False

    def test_an_exception_from_the_provider_never_escapes(self) -> None:
        # A trading loop that crashed here would stop processing the signals it
        # could still act on. Every exception type the terminal might raise has to
        # land as a refusal.
        service, account, _ = _service()
        for error in (
            RuntimeError("boom"),
            OSError("pipe"),
            ValueError("bad state"),
            KeyError("x"),
        ):
            account.fail_with(error)
            assert service.position_size("EURUSD", _stop(), _risk()).ok is False

    def test_the_symbol_is_not_even_asked_when_the_account_is_gone(self) -> None:
        # There is no point reading a specification that cannot be used, and a
        # provider call has a cost on a live terminal.
        service, account, symbols = _service()
        account.fail_with(IntegrationError("down"))
        service.position_size("EURUSD", _stop(), _risk())
        assert symbols.calls == 0

    def test_the_service_recovers_on_the_next_signal(self) -> None:
        # The failure mode that matters most: a terminal restarting mid-session
        # must not end the loop.
        service, account, _ = _service()
        account.fail_with(IntegrationError("down"))
        assert service.position_size("EURUSD", _stop(), _risk()).ok is False
        account.recover()
        assert service.position_size("EURUSD", _stop(), _risk()).ok is True


class TestWhenTheSymbolIsUnavailable:
    def test_an_unknown_symbol_refuses_the_trade(self) -> None:
        # Not a default specification. A default would be an invented contract,
        # which is precisely what this project refuses to supply.
        service, _, _ = _service()
        size = service.position_size("NOSUCH", _stop(), _risk())
        assert size.ok is False
        assert size.reason is RejectionReason.SYMBOL_SPEC_UNAVAILABLE

    def test_the_message_says_neither_upstream_project_can_supply_it(self) -> None:
        # So the refusal explains the project's central constraint rather than
        # looking like a missing configuration entry.
        service, _, _ = _service()
        explanation = service.position_size("NOSUCH", _stop(), _risk()).explanation
        assert "opaque string" in explanation
        assert "clicking on it" in explanation

    def test_a_currency_mismatch_is_refused_before_any_division(self) -> None:
        service, _, _ = _service(specs={"XAUUSD": gold_spec(currency_profit="EUR")})
        size = service.position_size("XAUUSD", _stop(distance="3.00"), _risk())
        assert size.ok is False
        assert size.reason is RejectionReason.INVALID_RISK_PARAMETERS

    def test_a_missing_facts_refusal_says_so_differently_from_a_contradiction(self) -> None:
        # "The terminal did not answer" and "the two answers disagree" are
        # different problems with different remedies, and one reason code for both
        # would send an operator to the wrong place.
        service, _, _ = _service(specs={"XAUUSD": gold_spec(currency_profit="EUR")})
        assert (
            service.position_size("NOSUCH", _stop(), _risk()).reason
            is RejectionReason.SYMBOL_SPEC_UNAVAILABLE
        )
        assert (
            service.position_size("XAUUSD", _stop(), _risk()).reason
            is RejectionReason.INVALID_RISK_PARAMETERS
        )


class TestWhenTheSizeIsUntradeable:
    def test_a_budget_too_small_for_the_instrument_refuses_the_trade(self) -> None:
        # $0.20 over a 300-tick stop cannot buy a 0.01 lot, and the floor-up that
        # would "solve" it risks fifteen times the budget.
        service, _, _ = _service()
        size = service.position_size("EURUSD", _stop(), _risk(risk_percent=Decimal("0.002")))
        assert size.ok is False
        assert size.reason is RejectionReason.VOLUME_BELOW_BROKER_MINIMUM

    def test_the_refusal_reaches_the_caller_with_the_reasons_numbers(self) -> None:
        service, _, _ = _service()
        details = service.position_size(
            "EURUSD", _stop(), _risk(risk_percent=Decimal("0.002"))
        ).details
        assert details["raw_volume"]
        assert details["risk_amount"]
        assert details["ticks"] == "300"


class TestObservability:
    def test_both_reserved_events_are_emitted_on_success(self, _logs: StringIO) -> None:
        service, _, _ = _service()
        service.position_size("EURUSD", _stop(), _risk())
        assert _events(_logs, "RISK_CALCULATED")
        assert _events(_logs, "POSITION_SIZED")

    def test_the_risk_event_carries_the_numbers_a_reviewer_checks(self, _logs: StringIO) -> None:
        service, _, _ = _service()
        service.position_size("EURUSD", _stop(), _risk())
        event = _events(_logs, "RISK_CALCULATED")[0]
        assert Decimal(str(event["balance"])) == Decimal("10000")
        assert event["risk_percent"] == "0.5"
        assert event["currency"] == "USD"
        assert Decimal(str(event["risk_amount"])) == Decimal("50")

    def test_the_size_event_carries_the_full_arithmetic(self, _logs: StringIO) -> None:
        service, _, _ = _service()
        service.position_size("EURUSD", _stop(), _risk())
        event = _events(_logs, "POSITION_SIZED")[0]
        assert event["volume"] == "0.16"
        assert event["ticks"] == "300"
        assert event["risk_per_unit"] == "300.0"
        assert event["within_budget"] is True

    def test_a_refusal_is_also_logged(self, _logs: StringIO) -> None:
        # A size refused because the budget is too small for this instrument is
        # one of the most useful lines in the log: it says the configuration and
        # the market do not fit together, and it says it with the numbers. An
        # event emitted only on success would leave a silence indistinguishable
        # from a signal that never arrived.
        service, _, _ = _service()
        service.position_size("EURUSD", _stop(), _risk(risk_percent=Decimal("0.002")))
        sized = _events(_logs, "POSITION_SIZED")
        assert sized
        assert sized[0]["ok"] is False
        assert sized[0]["reason"] == "VOLUME_BELOW_BROKER_MINIMUM"
        assert sized[0]["raw_volume"]

    def test_an_unreachable_terminal_is_logged_with_its_reason(self, _logs: StringIO) -> None:
        service, account, _ = _service()
        account.fail_with(IntegrationError("terminal is not running"))
        service.position_size("EURUSD", _stop(), _risk())
        calculated = _events(_logs, "RISK_CALCULATED")
        assert calculated
        assert calculated[0]["reason"] == "ACCOUNT_BALANCE_UNAVAILABLE"
        assert calculated[0]["balance"] is None

    def test_the_logged_volume_is_the_returned_volume(self, _logs: StringIO) -> None:
        # A decision log that named a different size from the one returned would
        # be worse than no log at all.
        service, _, _ = _service()
        size = service.position_size("EURUSD", _stop(), _risk()).unwrap()
        assert _events(_logs, "POSITION_SIZED")[0]["volume"] == str(size.volume)

    def test_the_logged_risk_is_the_returned_risk(self, _logs: StringIO) -> None:
        service, _, _ = _service()
        size = service.position_size("EURUSD", _stop(), _risk()).unwrap()
        assert Decimal(str(_events(_logs, "RISK_CALCULATED")[0]["risk_amount"])) == size.risk_amount


class TestTheFakesAreRealAdapters:
    def test_the_fakes_satisfy_the_ports_they_stand_in_for(self) -> None:
        # The reason the fakes live in `adapters/` and not in a fixtures file: a
        # fixture returning a tuple would test a function that does not exist in
        # production. These are checked against the same Protocols the MT5 adapter
        # will satisfy, so the contract is pinned before the adapter is written.
        account, symbols = FakeAccountProvider(_account()), FakeSymbolSpecProvider()
        assert isinstance(account, AccountProvider)
        assert isinstance(symbols, SymbolSpecProvider)

    def test_an_unknown_symbol_raises_rather_than_returning_a_default(self) -> None:
        with pytest.raises(IntegrationError):
            FakeSymbolSpecProvider().spec("EURUSD")

    def test_the_error_names_what_is_registered(self) -> None:
        # So the failure says what to fix rather than only what went wrong.
        with pytest.raises(IntegrationError) as caught:
            FakeSymbolSpecProvider({"EURUSD": eurusd_spec()}).spec("XAUUSD")
        assert "EURUSD" in str(caught.value)

    def test_a_failing_provider_raises_what_it_was_given(self) -> None:
        provider = FakeAccountProvider(_account())
        provider.fail_with(IntegrationError("terminal is not running"))
        with pytest.raises(IntegrationError, match="not running"):
            provider.balance()

    def test_a_symbol_provider_that_recovered_stops_raising(self) -> None:
        # A terminal restarting mid-session must not end the loop, and that has to
        # hold for the symbol source as well as the account.
        provider = FakeSymbolSpecProvider({"EURUSD": eurusd_spec()})
        provider.fail_with(IntegrationError("down"))
        with pytest.raises(IntegrationError, match="down"):
            provider.spec("EURUSD")
        provider.recover()
        assert provider.spec("EURUSD").symbol_normalised == "EURUSD"

    def test_a_failing_symbol_provider_short_circuits_before_the_lookup(self) -> None:
        # The terminal's error takes precedence over "no such symbol", because it
        # is the reason there is no answer.
        provider = FakeSymbolSpecProvider()
        provider.fail_with(IntegrationError("down"))
        with pytest.raises(IntegrationError, match="down"):
            provider.spec("EURUSD")

    def test_a_provider_that_recovered_stops_raising(self) -> None:
        provider = FakeAccountProvider(_account())
        provider.fail_with(IntegrationError("down"))
        provider.recover()
        assert provider.balance().balance == Decimal("10000")

    def test_registration_is_keyed_by_the_normalised_symbol(self) -> None:
        provider = FakeSymbolSpecProvider()
        provider.register(gold_spec(symbol=" xauusd "))
        assert provider.spec("XAUUSD").symbol_normalised == "XAUUSD"

    def test_the_spec_builders_produce_the_shapes_the_sizer_is_correct_for(self) -> None:
        # Both reach $300 a lot over their natural stop, by different arithmetic.
        # If a builder's numbers drifted, every sizing test built on it would
        # still pass while testing the wrong instrument.
        forex = eurusd_spec()
        gold = gold_spec()
        assert forex.risk_per_unit(Decimal("0.00300")) == Decimal("300")
        assert gold.risk_per_unit(Decimal("3.00")) == Decimal("300")

    def test_an_unusual_spec_is_asymmetric_as_advertised(self) -> None:
        assert unusual_spec().conservative_tick_value == Decimal("2.0")
