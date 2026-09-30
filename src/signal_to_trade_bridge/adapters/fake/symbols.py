"""A fake symbol specification source, for tests.

The companion to :mod:`signal_to_trade_bridge.adapters.fake.account`, and for the
same reason. A position size is ``risk_amount / (ticks x tick_value)``, and every
one of those three numbers comes from a source that only exists in production --
the terminal. Without a fake, the sizer's tests could not run without MetaTrader
5, and a test suite that needs a trading terminal is a test suite nobody runs
before changing risk code.

The specifications shipped here are the two the project cares about most, and
they are worth having as named builders rather than as inline literals:

* **EURUSD** -- a 5-digit pair. 0.00001 ticks at $1 per tick, so a 0.00300 stop
  is 300 ticks and risks $300 a lot.
* **XAUUSD** -- gold. 0.01 ticks at $1 per tick, so a $3.00 stop is *also* 300
  ticks and *also* risks $300 a lot.

The second is the case a forex-shaped sizer gets wrong by two orders of
magnitude, in the direction of oversizing. Having both available by name is what
makes that test readable rather than incidental.

Symbol lookup is case-insensitive and whitespace-tolerant, matching MT5 and
matching :attr:`SymbolSpec.symbol_normalised`. A fake that was stricter than the
real thing would let a case-handling bug pass here and fail against the terminal.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from signal_to_trade_bridge.domain.errors import IntegrationError
from signal_to_trade_bridge.domain.models import SymbolSpec

__all__ = [
    "FakeSymbolSpecProvider",
    "eurusd_spec",
    "gold_spec",
    "unusual_spec",
]


def _decimal_or_raw(defaults: Mapping[str, object]) -> dict[str, object]:
    """Turn the numeric fields of a specification builder into ``Decimal``.

    The builders below are written with string literals so the numbers read as the
    numbers a broker publishes -- ``"0.00001"`` rather than ``1e-05`` -- and this
    is where the numeric ones become the ``Decimal`` the model requires. Converting
    in the builder rather than at each call site means a test cannot accidentally
    pass a ``float`` and get ``float`` arithmetic inside the domain, which is the
    one thing the ``Decimal`` decision exists to prevent.

    The field names are listed rather than every string being converted, because
    the non-numeric fields are strings too -- a symbol name is not a number, and
    ``Decimal("EURUSD")`` is an error rather than a conversion.
    """
    return {
        key: Decimal(value) if key in _NUMERIC_FIELDS and isinstance(value, str) else value
        for key, value in defaults.items()
    }


#: The ``SymbolSpec`` fields that are numeric. Everything else is a string.
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


def eurusd_spec(**overrides: object) -> SymbolSpec:
    """A 5-digit EURUSD: 0.00001 ticks at $1, so $300 a lot over a 30-pip stop."""
    defaults: dict[str, object] = {
        "symbol": "EURUSD",
        "contract_size": "100000",
        "tick_size": "0.00001",
        "tick_value_profit": "1.0",
        "tick_value_loss": "1.0",
        "volume_min": "0.01",
        "volume_max": "100.0",
        "volume_step": "0.01",
        "digits": 5,
        "point": "0.00001",
        "currency": "EUR",
        "currency_profit": "USD",
        "currency_margin": "EUR",
    }
    defaults.update(overrides)
    return SymbolSpec(**_decimal_or_raw(defaults))  # type: ignore[arg-type]


def gold_spec(**overrides: object) -> SymbolSpec:
    """XAUUSD: 0.01 ticks at $1, so $300 a lot over a $3.00 stop.

    The same risk as EURUSD over a 30-pip stop, reached by completely different
    arithmetic. One lot is 100 ounces and a tick is a cent, where the forex case
    has a tick of 0.00001 and no contract size in the calculation at all.
    """
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
    return SymbolSpec(**_decimal_or_raw(defaults))  # type: ignore[arg-type]


def unusual_spec(**overrides: object) -> SymbolSpec:
    """A specification that is deliberately awkward, for the edges.

    ``tick_value_loss`` larger than ``tick_value_profit``, a 0.1 volume step and a
    maximum below the default. Every one of those is a real broker shape, and
    each one is a case where a sizer that assumes a 5-digit pair with $1 ticks
    gives a different -- and wrong -- answer.
    """
    defaults: dict[str, object] = {
        "symbol": "ODDPAIR",
        "contract_size": "1",
        "tick_size": "0.05",
        "tick_value_profit": "0.5",
        "tick_value_loss": "2.0",
        "volume_min": "0.1",
        "volume_max": "5.0",
        "volume_step": "0.1",
        "digits": 2,
        "point": "0.01",
        "currency": "USD",
        "currency_profit": "USD",
        "currency_margin": "USD",
    }
    defaults.update(overrides)
    return SymbolSpec(**_decimal_or_raw(defaults))  # type: ignore[arg-type]


class FakeSymbolSpecProvider:
    """A registry of specifications, standing in for the terminal's.

    Same contract as the MT5 adapter it will be replaced by: **an unknown symbol
    raises.** It does not return a default specification, because a default would
    be an invented contract -- and an invented contract is precisely what the
    Phase 0 audit found missing from both upstream projects and what this project
    refuses to supply. An unknown symbol is a refusal, and the port says so.
    """

    def __init__(
        self,
        specs: Mapping[str, SymbolSpec] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._specs: dict[str, SymbolSpec] = {}
        self._error = error
        self.calls = 0
        for spec in (specs or {}).values():
            self.register(spec)

    def register(self, spec: SymbolSpec) -> None:
        """Add or replace a specification, keyed by its normalised symbol."""
        self._specs[spec.symbol_normalised] = spec

    def spec(self, symbol: str) -> SymbolSpec:
        """The specification for ``symbol``.

        Raises rather than returning a default: an unknown or unloaded symbol is
        a refusal, not a trade at a standard size.
        """
        self.calls += 1
        if self._error is not None:
            raise self._error
        key = symbol.strip().upper()
        try:
            return self._specs[key]
        except KeyError:
            known = ", ".join(sorted(self._specs)) or "none"
            raise IntegrationError(
                f"no specification is registered for {key}; the terminal does not have that "
                f"symbol loaded. Known symbols: {known}"
            ) from None

    def fail_with(self, error: Exception) -> None:
        """Make every lookup fail, as a terminal that is not running would."""
        self._error = error

    def recover(self) -> None:
        """Stop failing, so a test can show the loop resumes on the next signal."""
        self._error = None
