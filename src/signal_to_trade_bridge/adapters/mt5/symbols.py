"""``SymbolSpecProvider`` over the MetaTrader 5 bindings.

The second fact neither upstream project has, and the one that makes position
sizing possible at all. The Phase 0 audit verified that `trade_contract_size`,
`volume_step` and `trade_tick_value` appear **nowhere** in `auto-trade`'s `src/`,
`docs/` or `mql5/`, and that `albrooks` treats a symbol as an opaque `str`.

**A missing specification raises.** It does not return a default. A default
specification is an invented contract, and an invented contract is the single
thing this project refuses to supply — the fake provider in `adapters/fake/` says
the same thing for the same reason, and a test asserts both agree. An unknown or
unloaded symbol is a refusal, which is what the port specifies.

**Floats become exact decimals here.** ``Decimal(str(value))``, not
``Decimal(value)``: the second copies the binary double's exact representation, so
a ``tick_size`` of ``0.00001`` would arrive as
``0.0000100000000000000002092251`` and every tick count computed from it would be
slightly wrong in a way that looks right. The domain's ``Decimal`` decision
depends on this conversion being done properly rather than approximately.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from pathlib import Path

from signal_to_trade_bridge.adapters.mt5.bindings import (
    MT5Bindings,
    MT5SymbolInfo,
    MT5Unavailable,
    load_bindings,
)
from signal_to_trade_bridge.domain.models import SymbolSpec

__all__ = ["MT5SymbolSpecProvider"]


class MT5SymbolSpecProvider:
    """A symbol's trading contract, read from a running terminal.

    Cache-free. A specification does not change within a session, but a cache here
    would be a cache that could go stale across a broker changing a symbol's
    contract mid-session, and the cost of a re-reading is a local IPC call. When
    caching is wanted, it belongs at the composition root with a stated lifetime.
    """

    def __init__(
        self,
        bindings: MT5Bindings | None = None,
        *,
        terminal_path: Path | None = None,
    ) -> None:
        self._bindings = bindings if bindings is not None else load_bindings(terminal_path)

    def spec(self, symbol: str) -> SymbolSpec:
        """The specification for ``symbol``.

        Raises :class:`MT5Unavailable` for a symbol the terminal does not know.
        That covers three distinct situations — the name is wrong, the symbol is
        not loaded in this terminal, and the terminal is not running at all — and
        the message names all three rather than guessing which it is. Guessing
        would be worse: an operator who is told "unknown symbol" when the real
        cause is a dead terminal goes looking in Market Watch instead of at the
        process.
        """
        key = (symbol or "").strip().upper()
        if not key:
            raise MT5Unavailable("a symbol is required to read a specification")

        info = self._lookup(key)
        return self._to_spec(key, info)

    def _lookup(self, key: str) -> MT5SymbolInfo:
        try:
            info = self._bindings.symbol_info(key)
        except Exception as exc:
            raise MT5Unavailable(
                f"the terminal did not answer a specification query for {key}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if info is None:
            raise MT5Unavailable(
                f"the terminal has no specification for {key}. Either the name is wrong, or "
                f"the symbol is not loaded in this terminal, or the terminal is not running "
                f"— all three look identical from here, and none of them is answered with a "
                f"guessed contract."
            )
        return info

    @staticmethod
    def _to_spec(key: str, info: MT5SymbolInfo) -> SymbolSpec:
        """Map the bindings' fields onto the domain model, one for one.

        The field names are MT5's own, which is why the model is named after
        them. The currency fields are included even though the earlier Phase 4 model
        predated them: ``check_currency_compatibility`` refuses an unstated profit
        currency, and it can only be answered if the adapter read one.
        """
        return SymbolSpec(
            symbol=key,
            contract_size=_decimal(info.trade_contract_size, "trade_contract_size", key),
            tick_size=_decimal(info.trade_tick_size, "trade_tick_size", key),
            tick_value_profit=_decimal(
                info.trade_tick_value_profit, "trade_tick_value_profit", key
            ),
            tick_value_loss=_decimal(info.trade_tick_value_loss, "trade_tick_value_loss", key),
            volume_min=_decimal(info.volume_min, "volume_min", key),
            volume_max=_decimal(info.volume_max, "volume_max", key),
            volume_step=_decimal(info.volume_step, "volume_step", key),
            digits=int(info.digits),
            point=_decimal(info.point, "point", key),
            currency=_text(info.currency_base, "currency_base", key),
            currency_profit=_text(info.currency_profit, "currency_profit", key),
            currency_margin=_text(info.currency_margin, "currency_margin", key),
        )

    def shutdown(self) -> None:
        """Release the terminal connection. Never raises; see the account adapter."""
        with contextlib.suppress(Exception):
            self._bindings.shutdown()


def _decimal(value: float, name: str, symbol: str) -> Decimal:
    """A binding's ``float`` as the exact decimal the terminal meant.

    ``Decimal(str(value))`` rather than ``Decimal(value)``, which is the whole
    reason this function exists. The second form copies the binary double's exact
    value, so a ``tick_size`` of ``0.00001`` arrives as
    ``0.0000100000000000000002092251...`` and every tick count derived from it is
    slightly wrong in a way that looks entirely right. ``str`` gives the shortest
    decimal that round-trips, which is what the terminal published.

    ``None`` raises rather than becoming zero. A zero tick size would be caught
    downstream by the sizer, but it would be caught as a *sizing* refusal, which
    points an operator at the arithmetic instead of at the terminal. And a missing
    tick value becoming zero would be caught only as a refusal to divide by
    nothing — two different faults, one of which is real.
    """
    if value is None:
        raise MT5Unavailable(f"the terminal reported no {name} for {symbol}")
    return Decimal(str(value))


def _text(value: str, name: str, symbol: str) -> str:
    """A currency code, upper-cased, or an empty string.

    Empty rather than raising, and the asymmetry is deliberate. An empty
    ``currency_profit`` is a *fact the domain already handles*: the currency check
    refuses an unstated profit currency, because a check that passes on missing
    data is not a check. An empty ``symbol``, by contrast, is not something the
    domain models.

    So this normalises and stops. The refusal belongs to the policy that knows what
    a missing currency means, not to the adapter that merely reports one.
    """
    return (value or "").strip().upper()
