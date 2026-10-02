"""The command line. Phase 12.

Everything runs through :func:`main`, which returns an exit code rather than
calling ``sys.exit`` — so a test can call it and read the code, and a caller that
wants to embed it does not have to catch ``SystemExit``.

The exit codes are the part worth reading before the commands, because they are the
contract a shell script depends on:

```
0  the command did what it was asked
1  a refusal -- the bridge worked and declined to trade
2  a fault -- unreachable terminal, unreadable ledger, refused configuration
3  the answer was UNKNOWN, and must not be retried automatically
```

**No command here can place an order.** Not one. The live path requires control
identifiers measured for this terminal's build, and until it has them
:func:`~signal_to_trade_bridge.composition.build_bridge` refuses — so there is
nothing for a CLI to expose. If a later phase adds an order command it needs its own
opt-in flag and its own tests, and :mod:`signal_to_trade_bridge.cli.main` is where
that reasoning belongs.
"""

from signal_to_trade_bridge.cli.main import (
    EXIT_FAULT,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNKNOWN,
    main,
)

__all__ = ["EXIT_FAULT", "EXIT_OK", "EXIT_REFUSED", "EXIT_UNKNOWN", "main"]
