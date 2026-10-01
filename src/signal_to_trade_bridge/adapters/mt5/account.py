"""``AccountProvider`` over the MetaTrader 5 bindings.

The first adapter to supply the fact neither upstream project has. The Phase 0
audit found `balance`, `equity` and `margin` **nowhere** in `auto-trade`'s
source, documentation or MQL5, and no account layer at all in `albrooks` — which
is why this class exists rather than a wiring exercise.

Two things it deliberately does not do:

* **It does not launch a terminal.** ``initialize`` is called; ``launch`` never
  is. ``BridgeConfig.require_running_terminal`` defaults to true for the same
  reason — a process that starts a trading terminal is a process that can start it
  by accident, and one that starts it *without* a login is worse.
* **It does not fall back.** An unreachable terminal raises
  :class:`~signal_to_trade_bridge.adapters.mt5.bindings.MT5Unavailable`. The
  alternative — returning the last balance seen, or a configured default — is the
  one thing this project refuses to do, and the refusal is enforced twice: here by
  raising, and in the domain by refusing a trade with no balance.

The balances arrive as ``float`` from the bindings and leave as ``Decimal``.
That conversion is the whole reason this adapter exists, and it happens here rather
than downstream: ``float`` cannot represent every decimal exactly, and a balance
that has been rounded to the nearest representable double before being multiplied
by a percentage is a balance nobody chose.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from pathlib import Path

from signal_to_trade_bridge.adapters.mt5.bindings import (
    MT5AccountInfo,
    MT5Bindings,
    MT5Unavailable,
    load_bindings,
)
from signal_to_trade_bridge.domain.models import AccountBalance

__all__ = ["MT5AccountProvider"]


class MT5AccountProvider:
    """The account facts sizing needs, read from a running terminal.

    Stateless apart from the bindings it holds, so a caller may construct one per
    use or share one for a session.
    """

    def __init__(
        self,
        bindings: MT5Bindings | None = None,
        *,
        terminal_path: Path | None = None,
    ) -> None:
        # Injectable, following `albrooks.adapters.mt5.MT5Feed`: the module is a
        # parameter so every branch below is testable without a terminal, and so
        # that `MetaTrader5` need not appear in any test file. Constructed with no
        # argument it connects for real.
        self._bindings = bindings if bindings is not None else load_bindings(terminal_path)

    def balance(self) -> AccountBalance:
        """The current account state.

        Raises :class:`MT5Unavailable` rather than returning a sentinel. The
        distinction between "the account has no money" and "we could not ask" is
        the difference between refusing a trade and reporting a broken connection,
        and the two must not be collapsed into one value.
        """
        info = self._read()
        return AccountBalance(
            balance=_decimal(info.balance, "balance"),
            currency=_currency(info.currency),
            equity=_decimal(info.equity, "equity"),
            open_positions=_open_positions(),
            account_login=int(info.login),
            server=info.name or None,
        )

    def _read(self) -> MT5AccountInfo:
        try:
            info = self._bindings.account_info()
        except Exception as exc:
            raise MT5Unavailable(
                f"the terminal did not answer an account query: {type(exc).__name__}: {exc}"
            ) from exc

        if info is None:
            # The documented "no account" answer. A terminal that is running but
            # not logged in returns this, and it is the single most likely cause
            # on a machine where the process is up and the trade is not.
            raise MT5Unavailable(
                "the terminal returned no account. It is usually running but not logged in, "
                "or logged into an account the bridge was not told about."
            )
        return info

    def shutdown(self) -> None:
        """Release the terminal connection.

        Never raises. A failure to disconnect is not a trading fact, and letting
        it escape from a cleanup path would mask whatever the caller was actually
        doing when it called.
        """
        with contextlib.suppress(Exception):
            self._bindings.shutdown()


def _decimal(value: float, name: str) -> Decimal:
    """A binding's ``float`` as an exact ``Decimal``.

    ``Decimal(str(value))`` rather than ``Decimal(value)``: the second form copies
    the binary double's exact value, so ``0.1`` becomes
    ``0.1000000000000000055511151231257827`` and every downstream percentage of it
    inherits that error. The first reads the shortest decimal that round-trips,
    which is the number the terminal meant.

    A negative balance is passed through rather than clamped. ``AccountBalance``
    refuses it, and a refusal from the model says "that is not an account balance"
    — which is more useful than a silent zero, and more honest than a positive
    number the terminal never reported.
    """
    if value is None:
        raise MT5Unavailable(f"the terminal reported no {name}")
    return Decimal(str(value))


def _currency(value: str) -> str:
    """The account currency, upper-cased.

    Upper-cased because every currency comparison in the domain is
    case-insensitive, and normalising once at the boundary is better than relying
    on every comparison to remember. An empty currency raises rather than
    defaulting: a balance with no currency cannot be divided by a tick value, and
    the domain's currency check would refuse the trade anyway — better to say so
    here, where the cause is obvious.
    """
    text = (value or "").strip().upper()
    if not text:
        raise MT5Unavailable("the terminal reported no account currency")
    return text


def _open_positions() -> int:
    """Always zero, and documented as a known gap.

    MT5's ``account_info()`` does not report a position count — that is
    ``positions_get()``, a different call over a different shape. Reading it would
    mean adding a fifth call to a four-call Protocol for a fact the bridge's own
    concurrency gate is the only consumer of.

    So the count is reported as zero, which is **wrong when positions are open**,
    and the consequence is recorded rather than hidden:
    `validate_policy`'s ``max_open_positions`` gate sees zero and admits a trade.
    A gate that admits a trade it should have refused is the wrong direction.

    Three things follow, and none of them is "leave it as zero":

    * this must be implemented before any configuration sets
      ``BRIDGE_MAX_OPEN_POSITIONS``;
    * ``positions_get()`` belongs in this class, and the Protocol grows a method
      when it does;
    * Phase 0 recorded that ``auto-trade``'s own equivalent gate is **inert** for
      the same reason — ``AccountSnapshot.open_positions`` is never populated. So
      there is no second source to fall back on either.

    Until then, an unset ``BRIDGE_MAX_OPEN_POSITIONS`` is not merely the safe
    default but the only correct one.
    """
    return 0
