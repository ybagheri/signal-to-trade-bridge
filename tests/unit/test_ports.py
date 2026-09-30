"""The ports: shape, narrowness, and substitutability.

A ``Protocol`` has no executable body, so line coverage of ``ports/`` is
permanently zero and says nothing. What can be checked is the *contract*: that
each port declares the methods its name promises, that they are narrow, and that
a plain class implementing them structurally satisfies the type.

That last one matters more than it looks. The whole dependency-inversion argument
rests on a concrete adapter being usable as its port without inheriting from
anything. If a port were an abstract base class rather than a ``Protocol``, the
adapters would have to import it -- and ``domain`` would transitively depend on
``ports``, which is the layering this project refuses.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from signal_to_trade_bridge import ports


class TestEveryPortIsAProtocol:
    @pytest.mark.parametrize(
        "name",
        [
            "SignalSource",
            "MarketDataProvider",
            "AccountProvider",
            "SymbolSpecProvider",
            "TradeExecutor",
            "IdempotencyStore",
            "KillSwitch",
        ],
    )
    def test_the_port_exists_and_is_a_protocol(self, name: str) -> None:
        port = getattr(ports, name)
        assert inspect.isclass(port)
        # `runtime_checkable` is what makes `isinstance` work, which is what a
        # composition root needs when it is handed a concrete adapter.
        assert getattr(port, "_is_runtime_protocol", False), (
            f"{name} is not runtime-checkable, so a composition root cannot verify that "
            f"the adapter it was given actually satisfies it"
        )

    def test_no_port_is_an_abstract_base_class(self) -> None:
        # An ABC would force adapters to inherit from it, which would make the
        # domain transitively depend on `ports` and break the layering. Checked by
        # looking for `abstractmethod`, which is what actually makes a class
        # abstract -- `inspect.isabstract` alone would also be true for a class
        # that inherited from one.
        for name in ports.__all__:
            port = getattr(ports, name)
            members = vars(port).values()
            assert not any(getattr(member, "__isabstractmethod__", False) for member in members), (
                f"{name} declares an abstract method, so it is an ABC rather than a Protocol"
            )

    def test_every_port_is_exported(self) -> None:
        for name in ("SignalSource", "TradeExecutor", "AccountProvider"):
            assert name in ports.__all__


class TestPortsAreNarrow:
    """A fat port has to be faked in full by every test that touches it."""

    def test_the_provider_ports_expose_exactly_one_method(self) -> None:
        # Each of these answers exactly one question. Merging them would mean a
        # test that only wants a balance figure also had to implement positions,
        # quotes and symbol metadata.
        for name in ("SignalSource", "MarketDataProvider", "AccountProvider", "SymbolSpecProvider"):
            port = getattr(ports, name)
            own = [
                member
                for member, value in vars(port).items()
                if not member.startswith("_") and inspect.isfunction(value)
            ]
            assert len(own) == 1, f"{name} declares {own}, expected exactly one method"

    def test_trade_executor_exposes_exactly_one_method(self) -> None:
        port = ports.TradeExecutor
        own = [
            member
            for member, value in vars(port).items()
            if not member.startswith("_") and inspect.isfunction(value)
        ]
        assert own == ["submit"]

    def test_the_kill_switch_is_a_property_not_a_method(self) -> None:
        # A property, so a caller cannot call it and a stub cannot forget to
        # implement it as a method returning a bool by mistake.
        assert isinstance(ports.KillSwitch.active, property)


class TestPortsAreStructurallySatisfied:
    """The core claim: a plain class is usable as a port, with no inheritance."""

    def test_a_plain_account_provider_satisfies_the_port(self) -> None:
        from decimal import Decimal

        from signal_to_trade_bridge.domain.models import AccountBalance

        class FakeAccount:
            def balance(self) -> AccountBalance:
                return AccountBalance(balance=Decimal("1000"), currency="USD")

        assert isinstance(FakeAccount(), ports.AccountProvider)

    def test_a_plain_trade_executor_satisfies_the_port(self) -> None:

        from signal_to_trade_bridge.domain.models import ExecutionRequest, ExecutionResult

        class FakeExecutor:
            def submit(self, request: ExecutionRequest) -> ExecutionResult:
                return ExecutionResult(
                    signal_id=request.signal_id, status=ExecutionResult.STATUS_ACCEPTED
                )

        assert isinstance(FakeExecutor(), ports.TradeExecutor)

    def test_a_class_missing_a_method_does_not_satisfy_the_port(self) -> None:
        # The other half of the claim. `runtime_checkable` checks method presence,
        # so an adapter that forgot `submit` is caught at composition time rather
        # than as an `AttributeError` on the first live signal.
        class Incomplete:
            pass

        assert not isinstance(Incomplete(), ports.TradeExecutor)

    def test_a_class_with_a_wrong_signature_still_satisfies_naming(self) -> None:
        # A documented limitation of `runtime_checkable`, recorded so nobody
        # mistakes `isinstance` for more than it is. The name check passes; only a
        # real type checker catches the wrong signature. mypy runs over the whole
        # source tree, which is where that check lives.
        class WrongSignature:
            def submit(self) -> None: ...

        assert isinstance(WrongSignature(), ports.TradeExecutor)


class TestPortsAreTypeAnnotated:
    @pytest.mark.parametrize(
        "name", ["SignalSource", "AccountProvider", "SymbolSpecProvider", "TradeExecutor"]
    )
    def test_every_port_method_has_a_return_annotation(self, name: str) -> None:
        # An unannotated method on a Protocol is `Any` to a type checker, which
        # silently disables checking for every implementation of it.
        port = getattr(ports, name)
        for member, value in vars(port).items():
            if member.startswith("_") or not inspect.isfunction(value):
                continue
            assert value.__annotations__.get("return") is not None, (
                f"{name}.{member} has no return annotation"
            )
            signature = inspect.signature(value)
            for parameter, definition in signature.parameters.items():
                if parameter == "self":
                    # Correctly unannotated. `self` is not a value a caller
                    # supplies and a type checker infers it, so demanding an
                    # annotation here would be demanding a lie.
                    continue
                assert definition.annotation is not inspect.Parameter.empty, (
                    f"{name}.{member}({parameter}) has no annotation"
                )

    def test_the_idempotency_key_is_a_string_not_a_uuid(self) -> None:
        hints = get_type_hints(ports.IdempotencyStore.contains)
        assert hints["return"] is bool
        assert hints["key"] is str

    def test_the_domain_types_are_referenced_lazily(self) -> None:
        # The ports import their domain types under `TYPE_CHECKING`, so importing
        # `ports` does not drag in the domain. That is what keeps a test that only
        # needs a fake executor from importing the whole model layer.
        source = inspect.getsource(ports)
        assert "if TYPE_CHECKING:" in source
