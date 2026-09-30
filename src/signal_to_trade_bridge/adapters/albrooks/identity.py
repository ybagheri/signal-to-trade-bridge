"""Deterministic signal identity.

This module exists because of one hard constraint discovered in the architecture
audit: the downstream execution project deduplicates on ``signal_id``, and its
ledger is only useful if the same key can be produced again after a process
restart. A fresh UUID per call would satisfy every unit test and defeat the
mechanism entirely in production, because the key would never match.

So the identity is derived from the content of the reading, deterministically.
Two calls over the same market data produce the same key; the execution ledger
then refuses the second one.

That leaves one genuine design tension, and this module resolves it explicitly
rather than leaving it to a future reader's guess:

    * A reading that arrives **twice** must be the same signal. Re-delivery is
      the case dedup exists for.
    * A reading that is **recomputed** after new bars close must be a different
      signal, because the market moved and a new trade is a new decision.

The line is the bar. ``bar_index`` and ``bar_time`` are the newest bar the
analysis was allowed to read, and including them is what makes "the same reading
on the same bar" the unit of identity. Excluding the prices from the key for the
same reason: a recomputation over the *same* closed bar that nudges a stop by one
tick is the same reading with a rounding difference, and treating it as a new
signal would let a flapping stop produce a fresh position every cycle.

Excluding the prices is therefore a deliberate decision, and a test pins it.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from signal_to_trade_bridge.domain.enums import Direction, SignalAction

__all__ = ["SIGNAL_ID_PREFIX", "compute_signal_id"]

#: Prefixed so a key is recognisable in a log or a ledger at a glance, and so it
#: cannot be confused with a broker ticket or a position id.
SIGNAL_ID_PREFIX = "stb"

#: How much of the digest to keep. 32 hex characters is 128 bits, which makes an
#: accidental collision between two different readings effectively impossible for
#: any realistic number of signals, while keeping the key short enough to read in
#: a log line. The full 64 characters would be longer and no safer here: this is
#: an identity key, not a security token, and it is not used to resist guessing.
_DIGEST_CHARS = 32

#: Field order is part of the contract. Changing it changes every key, which
#: would make every previously-recorded signal look new and re-enable a duplicate
#: trade against a live ledger. A change here needs a migration note in
#: HANDOFF.md, not just a test update.
_FIELDS = (
    "symbol",
    "timeframe",
    "bar_index",
    "bar_time",
    "action",
    "direction",
    "setup_id",
)


def _render(value: object) -> str:
    """Render one component in a form that cannot collide with another.

    The ``|``-separated encoding is length-unambiguous because every component is
    written as ``name=value`` and the values themselves never contain the
    separator in a form that could be confused. The alternative -- concatenating
    values with a bare separator -- would let ``("EURUSD", "H1")`` and
    ``("EURUSDH", "1")`` produce the same string, and therefore the same key, for
    two completely different readings.
    """
    if isinstance(value, Decimal):
        # `str` on a Decimal is exact and never uses exponent notation for the
        # magnitudes a price reaches, whereas `float` would round. Two prices
        # that differ below float precision must not collapse into one key.
        return format(value.normalize(), "f")
    if isinstance(value, float):
        # A float reaches here only for `bar_time`, which upstream stores as a
        # float epoch. Rendered with repr so the full precision survives rather
        # than being truncated to a friendlier, lossy form.
        return repr(value)
    if value is None:
        return ""
    return str(value)


def compute_signal_id(
    *,
    symbol: str,
    timeframe: str,
    bar_index: int,
    bar_time: float | None,
    action: SignalAction,
    direction: Direction,
    setup_id: str,
) -> str:
    """Derive a stable identifier for one reading.

    Deterministic by construction: the same arguments always produce the same
    string, on any machine, in any process, forever. That is the whole point, and
    it is why no timestamp, no random source and no counter appears here.
    """
    parts = {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip(),
        "bar_index": str(bar_index),
        "bar_time": _render(bar_time),
        "action": action.value,
        "direction": direction.value,
        "setup_id": setup_id.strip(),
    }
    payload = "|".join(f"{name}={parts[name]}" for name in _FIELDS)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    return f"{SIGNAL_ID_PREFIX}-{digest}"
