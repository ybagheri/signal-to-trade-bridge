"""A fake account, for tests.

Position sizing needs an account balance, and neither upstream project can supply
one -- so every test of the sizer would otherwise need a running MetaTrader
terminal. That is not a convenience this project can accept: the domain's tests
have to run on a laptop with neither upstream repository cloned, which is the
whole point of the layering and the thing ``test_domain_isolation`` enforces.

So the fake is a first-class adapter rather than a fixture buried in a test file.
Two reasons, and the second is the important one:

* it implements the same
  :class:`~signal_to_trade_bridge.ports.AccountProvider` port as the real MT5
  adapter will, so a test written against this exercises the same code path the
  terminal will drive;
* it is the **specification** for that adapter. When Phase 7 writes the real one,
  the contract is already pinned by tests that pass against this, and the
  difference between them is a terminal rather than an argument.

The failure modes matter as much as the happy path. A provider that could only
return a balance would never exercise the branch that matters most in production,
which is the one where the terminal is not answering and the trade has to be
refused rather than sized against a remembered figure. So this fake can be told
to fail, and does so by **raising**, which is what
:class:`~signal_to_trade_bridge.ports.AccountProvider` specifies: "the account
has no money" and "we could not ask" are different answers and must not be
collapsed into one.
"""

from __future__ import annotations

from signal_to_trade_bridge.domain.errors import IntegrationError
from signal_to_trade_bridge.domain.models import AccountBalance

__all__ = ["FakeAccountProvider"]


class FakeAccountProvider:
    """An account that reports whatever it was told to report.

    Mutable on purpose, because a balance changes and a test needs to show that
    the size follows it. ``calls`` counts the reads, which is what lets a test
    assert that a refused trade never asked for the balance twice, or that a
    refused trade did not ask at all.
    """

    def __init__(
        self,
        balance: AccountBalance | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._balance = balance
        self._error = error
        self.calls = 0

    def balance(self) -> AccountBalance:
        """The configured balance, or a fault.

        Raises when it has been told to, and when it holds no balance at all.
        Returning ``None`` instead would be the more convenient shape and the
        wrong one: the port's contract is that an unreachable account raises, and
        a fake that quietly broke that contract would let the real adapter's
        error handling go untested.
        """
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._balance is None:
            raise IntegrationError(
                "the fake account provider has no balance configured, which is what a "
                "terminal that is not running looks like"
            )
        return self._balance

    def set_balance(self, balance: AccountBalance) -> None:
        """Change the reported balance, as a closed position would."""
        self._balance = balance

    def fail_with(self, error: Exception) -> None:
        """Make every read fail, as an unreachable terminal would."""
        self._error = error

    def recover(self) -> None:
        """Stop failing, so a test can show the loop resumes on the next signal."""
        self._error = None
