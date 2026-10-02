"""One Click Trading, and why it makes an order's stop loss decorative.

**The finding.** Every order this project placed came back wrong in the same way:
0.01 lots where 0.03 was asked for, and no stop loss and no take profit at all, while
the order dialog read back exactly what had been requested and MT5 had applied all of
it.

That combination rules out a write race and a focus problem. The fields were written,
settled and verified. The order was simply **not taken from the dialog**.

On an Alpari build, Algo Trading off means the terminal falls back to **One Click
Trading** mode, and in that mode `Buy by Market` sends the values from the Toolbox
Trade panel -- a symbol, a volume spinner, and no stop loss at all. That is precisely
the shape of what came back.

The terminal reports it: ``account_info().trade_mode == 0`` and
``terminal_info().trade_allowed is False``. Both are read here, with no terminal change
and no click.

### What this module does about it

It refuses. An order carrying a stop loss cannot be sent in a mode that cannot carry
one, and the only safe direction is to decline rather than to send an unprotected
position and notice afterwards.

**Verifying what you wrote is not verifying what will be sent.** That is the lesson,
and it is why the check lives here rather than in the dialog verifier, which was
reading the right values off the wrong object.
"""

from __future__ import annotations

from typing import Any

__all__ = ["is_one_click_trading", "refuse_one_click_order"]


def is_one_click_trading(bindings: Any) -> bool:
    """Whether the terminal is in One Click Trading mode, and safe to assume it is not.

    Read from MT5 rather than configured, because it is a terminal state the operator
    changes with a toolbar click, not a setting this project owns. Nothing is written
    and nothing is clicked.

    **An unreadable terminal reads as one-click, not as safe.** The mode cannot be
    read when the bindings are unavailable or the terminal is not answering, and "I
    could not tell" must not collapse into "everything is fine" -- that is the
    direction in which an unprotected position gets opened. Failing towards the
    refusal costs a working order on a machine whose terminal is briefly
    unresponsive; failing the other way costs money.
    """
    try:
        account = bindings.account_info()
    except Exception:
        return True
    if account is None:
        return True

    trade_mode = getattr(account, "trade_mode", None)
    if isinstance(trade_mode, int) and trade_mode != 0:
        return False

    # `trade_mode == 0` is MT5's DISABLED, which is what this build reports when Algo
    # Trading is off. Confirm it against the terminal's own view where that view is
    # available: `trade_allowed` is the flag that actually decides whether the
    # terminal may trade programmatically.
    try:
        terminal = bindings.terminal_info()
    except Exception:
        terminal = None
    if terminal is not None:
        allowed = getattr(terminal, "trade_allowed", None)
        if allowed is True:
            return False

    return True


def refuse_one_click_order(request: Any, *, one_click: bool) -> str:
    """Why this order cannot be sent in this mode, or ``""`` if it can.

    Only orders that carry a stop loss or a take profit are refused. An order with
    neither has nothing the mode would lose, and refusing those too would be a
    different bug -- a bridge that cannot trade at all on a machine with Algo Trading
    off.

    The message names the setting, because the operator's next action is a toolbar
    click and "enable Algo Trading" is the whole of it.
    """
    if not one_click:
        return ""
    carries_a_level = getattr(request, "stop_loss", None) is not None or (
        getattr(request, "take_profit", None) is not None
    )
    if not carries_a_level:
        return ""
    return (
        "the terminal is in One Click Trading mode, where Buy/Sell sends the Toolbox "
        "Trade panel's volume and has no stop loss or take profit at all -- so this "
        "order's levels cannot be carried, and the position would open unprotected. "
        "This was found the hard way: every order this bridge placed came back at the "
        "panel's default volume with no stop, while the order dialog read back "
        "exactly what had been requested. Enable Algo Trading in the MT5 toolbar, or "
        "send an order with no stop and no target."
    )
