"""The position snapshot the MQL5 indicator writes.

`MT5AccountProvider` reports ``open_positions`` as zero because
``account_info()`` does not carry a position count. This module closes that gap
from the other end: the terminal already publishes the answer, as JSON, on disk.

### Observed, not inferred

Everything below was read from a live terminal and from the indicator's source,
`AutoTradePositionReader.mq5` in the `auto-trade` checkout. It is recorded here
because the shape is a contract and a contract written down is worth more than
one rediscovered.

```
<data path>\\MQL5\\Files\\auto_trade_positions_a.json
<data path>\\MQL5\\Files\\auto_trade_positions_b.json
```

```json
{
  "schema": 1, "sequence": 410, "complete": true,
  "written_at": "2026-10-01T05:30:16Z", "account": 53184454,
  "server": "Alpari-MT5-Demo", "terminal_build": 6230,
  "positions": [
    {"ticket": 382363348, "symbol": "BITCOIN", "type": "BUY", "volume": 0.01,
     "price_open": 84484.0, "sl": 0.0, "tp": 0.0, "profit": 1.64,
     "magic": 0, "opened_at": "2026-09-27T08:52:15Z"}
  ]
}
```

### The double buffer, correctly understood

The writer's `WriteAlternating` picks `a` or `b` by `sequence % 2` and then
**deletes the other file** after a successful write. So in normal operation
**exactly one file exists**, not two — the second one only appears in the window
between truncating the target and deleting its predecessor.

That window is the whole reason for the design. `FileOpen` for writing truncates
the target, so a reader that opens it at that moment sees a **partial document**,
not a wrong one. The guard is therefore *parse failure*, and the recovery is *the
other file*, which is the previous complete snapshot. Reading a half-written file
is not a hazard this protocol creates; it is the hazard it contains.

Which is also why the reader below collects every file it can parse and takes the
highest sequence, rather than insisting on a particular name. A reader that
demanded both files would fail exactly when one has just been deleted.

``complete`` is written as the literal ``true`` by this indicator and is never
``false``, so the check for it is defensive against a future writer or a
hand-edited file rather than a branch that fires today. It is kept because
reading a file that says it is incomplete as though it were complete is worse
than refusing, and because auto-trade's own reader keeps the same check.

**The staleness limit is 30 seconds**, which is also auto-trade's
``MT5FilePositionSnapshotProvider`` default and is derived the same way: the
indicator writes once per second, so thirty seconds is thirty consecutive missed
writes. It is a constructor default rather than a configuration key in both
projects, and it is set explicitly here for the same reason — a gate that passes
on a count the terminal has stopped maintaining is not a gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from signal_to_trade_bridge.adapters.mt5.bindings import MT5Unavailable
from signal_to_trade_bridge.domain.enums import Direction

__all__ = [
    "DEFAULT_MAX_AGE",
    "SUPPORTED_SCHEMA",
    "MT5PositionReader",
    "ObservedPosition",
    "PositionSnapshot",
    "parse_snapshot",
]

#: The snapshot format this reader understands. A different number means a
#: different shape, and this reader refuses it rather than interpreting it.
SUPPORTED_SCHEMA = 1

#: How old a snapshot may be and still be believed. Thirty missed writes, at the
#: indicator's one-per-second cadence.
DEFAULT_MAX_AGE = timedelta(seconds=30)

#: The indicator's side vocabulary. MT5 says ``BUY``/``SELL``; the bridge says
#: ``LONG``/``SHORT``. The translation is here, once, rather than at each call
#: site -- and it is a translation rather than a rename because the two words
#: mean genuinely different things: a broker's ``BUY`` is not always the bridge's
#: ``LONG``, and a snapshot read as the wrong direction would make a long look
#: like a short.
_SIDES: dict[str, Direction] = {
    "BUY": Direction.LONG,
    "SELL": Direction.SHORT,
}


@dataclass(frozen=True, slots=True)
class ObservedPosition:
    """One position, as the terminal reported it.

    Only the four fields the bridge actually needs are modelled, and those four
    are the ones ``auto-trade``'s own verifier matches on: ``ticket``, ``symbol``,
    ``type`` and ``volume``. The snapshot also carries ``price_open``, ``sl``,
    ``tp``, ``profit``, ``magic`` and ``opened_at``; they are deliberately absent
    rather than merely unread, so that no code can come to depend on a field this
    class has not thought about.

    Dropping the rest is not a loss yet. ``auto-trade``'s ``PositionSnapshot`` has
    the same four attributes and discards the rest the same way, and the bridge's
    own use is a count plus a later Phase 7b identity match.
    """

    ticket: int
    symbol: str
    direction: Direction
    volume: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "type": self.direction.name,
            "volume": str(self.volume),
        }


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One reading of the indicator's file.

    ``raw`` keeps the untouched entries alongside the parsed ones, so a field that
    turns out to matter can be added without going back to the terminal.
    """

    sequence: int
    written_at: datetime
    account: int | None
    server: str | None
    terminal_build: int | None
    positions: tuple[ObservedPosition, ...] = field(default_factory=tuple)
    raw: tuple[Any, ...] = field(default_factory=tuple)
    source: Path | None = None

    @property
    def count(self) -> int:
        """How many positions the terminal reported.

        ``len``, and deliberately not a count of entries that parsed cleanly. A
        malformed entry is still an open position, and dropping it would
        under-count -- which is the direction that admits a trade the concurrency
        gate should have refused.

        So an unparseable entry is a **refusal of the whole snapshot**, raised
        while parsing, rather than a silently shorter list.
        """
        return len(self.positions)

    def is_stale(
        self, *, now: datetime | None = None, max_age: timedelta = DEFAULT_MAX_AGE
    ) -> bool:
        """Whether the snapshot is too old to believe.

        A snapshot in the *future* counts as stale rather than being clamped. A
        clock that has moved backwards, or a file written by a terminal on another
        machine, both produce a timestamp nothing should act on, and "in the
        future" is the honest reading of both.
        """
        reference = now or datetime.now(UTC)
        age = reference - self.written_at
        return age < timedelta(0) or age > max_age

    def for_symbol(self, symbol: str) -> tuple[ObservedPosition, ...]:
        """The positions on one symbol, case-insensitively as MT5 reports them."""
        key = symbol.strip().upper()
        return tuple(p for p in self.positions if p.symbol == key)


def parse_snapshot(text: str, *, source: Path | None = None) -> PositionSnapshot:
    """One file's contents into a snapshot, or :class:`MT5Unavailable`.

    Strict about the envelope, because every field in it is load-bearing:

    * ``schema`` must be :data:`SUPPORTED_SCHEMA`. An unknown version is a
      different shape, and interpreting it anyway is a guess.
    * ``sequence`` must be an integer. It is the only thing that orders the two
      files, so without it neither can be preferred.
    * ``complete`` must be true. Defensive against a future writer; see the module
      docstring.
    * ``written_at`` must parse, because it is the only freshness signal.

    JSON floats are parsed **straight to ``Decimal``** via ``parse_float``. Going
    through ``float`` first would round-trip ``volume`` through a binary double
    and hand the size comparison a value the terminal never wrote -- and the
    execution verifier matches on exact ``Decimal`` volume equality, so a
    rounding drift there is a failed verification of a trade that succeeded.
    """
    try:
        payload = json.loads(text, parse_float=Decimal)
    except ValueError as exc:
        raise MT5Unavailable(
            f"the position snapshot at {source} is not valid JSON: {exc}. This is the "
            f"expected state of a file the indicator is part-way through writing, which "
            f"is why the other one is kept until the new one is complete."
        ) from exc

    if not isinstance(payload, dict):
        raise MT5Unavailable(f"the position snapshot at {source} is not a JSON object")

    schema = payload.get("schema")
    if schema != SUPPORTED_SCHEMA:
        raise MT5Unavailable(
            f"the position snapshot at {source} declares schema {schema!r}, and this reader "
            f"understands {SUPPORTED_SCHEMA}. Refusing rather than interpreting a format it "
            f"has never seen: a count guessed from an unknown shape is worse than no count."
        )

    if payload.get("complete") is not True:
        raise MT5Unavailable(
            f"the position snapshot at {source} is marked incomplete. This indicator writes "
            f"the literal true, so this means a different writer or a hand-edited file."
        )

    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool):
        raise MT5Unavailable(
            f"the position snapshot at {source} has no usable sequence number "
            f"({sequence!r}). It is the only thing that orders the two files."
        )

    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise MT5Unavailable(
            f"the position snapshot at {source} has no positions list ({raw_positions!r}), "
            f"so the number of open positions cannot be read from it."
        )

    positions = tuple(_position(entry, source, index) for index, entry in enumerate(raw_positions))

    return PositionSnapshot(
        sequence=sequence,
        written_at=_timestamp(payload.get("written_at"), source),
        account=_int_or_none(payload.get("account")),
        server=_str_or_none(payload.get("server")),
        terminal_build=_int_or_none(payload.get("terminal_build")),
        positions=positions,
        raw=tuple(raw_positions),
        source=source,
    )


def _position(entry: object, source: Path | None, index: int) -> ObservedPosition:
    """One ``positions`` element, or a refusal naming what was wrong with it.

    Raises on a malformed entry rather than skipping it, for the reason
    :attr:`PositionSnapshot.count` gives: skipping under-counts, and an
    under-count is a concurrency gate that admits a trade.

    The four fields read are exactly the four ``auto-trade``'s verifier matches
    on -- ``ticket``, ``symbol``, ``type``, ``volume`` -- and the tolerance for a
    missing ``volume`` is zero, because ``Decimal(str(None))`` would raise anyway
    and a clear message here beats an ``InvalidOperation`` further down.
    """
    where = f"position {index} in {source}"

    if not isinstance(entry, dict):
        raise MT5Unavailable(f"{where} is not a JSON object ({type(entry).__name__})")

    ticket = _int_or_none(entry.get("ticket"))
    if ticket is None:
        raise MT5Unavailable(
            f"{where} has no usable ticket ({entry.get('ticket')!r}). The ticket is the "
            f"position's identity, and a position without one cannot be told from a new one."
        )

    symbol = _str_or_none(entry.get("symbol"))
    if symbol is None:
        raise MT5Unavailable(f"{where} has no symbol")

    raw_side = entry.get("type")
    side = _SIDES.get(raw_side.strip().upper()) if isinstance(raw_side, str) else None
    if side is None:
        # Reported with the value, because "unexpected side" without it is the
        # message that sends an operator to the indicator's source rather than to
        # the answer.
        raise MT5Unavailable(
            f"{where} reports type {raw_side!r}. This indicator writes BUY or SELL, so "
            f"anything else means a different writer."
        )

    raw_volume = entry.get("volume")
    try:
        volume = Decimal(str(raw_volume))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MT5Unavailable(f"{where} has no usable volume ({raw_volume!r})") from exc
    if volume <= 0:
        # A zero or negative volume is not a position. It is a bug in the
        # indicator, and treating it as an open one would be a false positive in
        # the safest possible direction -- but silently counting it would also be
        # wrong, so it is named instead.
        raise MT5Unavailable(f"{where} has a non-positive volume ({volume})")

    return ObservedPosition(ticket=ticket, symbol=symbol, direction=side, volume=volume)


def _timestamp(value: object, source: Path | None) -> datetime:
    """The snapshot's own clock, as an aware UTC datetime.

    A missing timezone is assumed UTC rather than local. The observed format ends
    in ``Z``, so the assumption is correct in practice; it is stated so that it is
    correct on purpose. This project has already decided that assuming a timezone
    in upstream data is how one field ends up meaning two things.
    """
    if not isinstance(value, str) or not value.strip():
        raise MT5Unavailable(f"the position snapshot at {source} has no written_at timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MT5Unavailable(
            f"the position snapshot at {source} has an unreadable written_at of {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _int_or_none(value: object) -> int | None:
    """An optional integer, with a decimal string accepted.

    MQL5 is not a typed target and a number can arrive as text. These fields are
    recorded for traceability and none of them drives the count, so a tolerance
    here is safe; the fields that *do* drive behaviour are checked in
    :func:`_position` with no tolerance.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, str):
        try:
            return int(Decimal(value))
        except ArithmeticError:
            return None
    return None


def _str_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


class MT5PositionReader:
    """Reads the newest complete, fresh position snapshot from the terminal.

    Injected into
    :class:`~signal_to_trade_bridge.adapters.mt5.account.MT5AccountProvider`
    rather than constructed inside it, for two reasons: a caller that only wants a
    balance should not have to know the file layout, and a test can drive the
    reader against a temporary directory with no terminal in sight.
    """

    def __init__(
        self,
        data_path: Path | None = None,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
        now: Any | None = None,
    ) -> None:
        if data_path is None:
            raise MT5Unavailable(
                "BRIDGE_MT5_DATA_PATH is required to read the position snapshot. It is the "
                "terminal's data directory; the indicator writes to <data path>\\MQL5\\Files."
            )
        self._files = data_path / "MQL5" / "Files"
        self._max_age = max_age
        # Injectable for the same reason as everywhere else in this project: a
        # test that pins "now" can exercise staleness without sleeping.
        self._now = now or (lambda: datetime.now(UTC))

    def read(self) -> PositionSnapshot:
        """The newest readable, fresh snapshot.

        Raises rather than returning a default. A missing, malformed, incomplete,
        stale or unknown-schema snapshot all mean the same thing to a caller --
        *the number of open positions is not known* -- and a caller that treats
        that as zero has a concurrency gate that has stopped working.
        """
        candidates: list[PositionSnapshot] = []
        problems: list[str] = []

        for path in self._paths():
            if not path.is_file():
                problems.append(f"{path.name} does not exist")
                continue
            try:
                candidates.append(parse_snapshot(path.read_text(encoding="utf-8"), source=path))
            except MT5Unavailable as exc:
                # Collected, not raised. A truncated `a` while `b` is still the
                # previous complete snapshot is the *designed* state of this
                # double buffer, and treating it as a failure would refuse a
                # perfectly good read.
                problems.append(f"{path.name}: {exc}")

        if not candidates:
            raise MT5Unavailable(
                f"no readable position snapshot in {self._files}. The indicator is what "
                f"writes it, so this means it is not attached, not running, or not permitted "
                f"to write. Details: {'; '.join(problems)}"
            )

        if len(candidates) == 2 and candidates[0].sequence == candidates[1].sequence:
            # Two files claiming one sequence means the writer stopped mid-rotation
            # or two terminals are writing into one folder. Either way the buffer is
            # not doing its job, and picking either would be a coin toss presented
            # as a fact.
            raise MT5Unavailable(
                f"both position snapshots claim sequence {candidates[0].sequence}, so neither "
                f"can be preferred. Details: {'; '.join(problems)}"
            )

        newest = max(candidates, key=lambda snapshot: snapshot.sequence)
        if newest.is_stale(now=self._now(), max_age=self._max_age):
            age = self._now() - newest.written_at
            raise MT5Unavailable(
                f"the newest position snapshot was written at {newest.written_at.isoformat()}, "
                f"which is {age} old against a limit of {self._max_age}. The indicator writes "
                f"about once a second, so this means it has stopped — and a concurrency gate "
                f"built on a count the terminal is no longer maintaining is not a gate."
            )
        return newest

    def count(self) -> int:
        """Just the number of open positions, for the account provider."""
        return self.read().count

    def _paths(self) -> tuple[Path, Path]:
        # The two observed names, written out rather than globbed. A glob would
        # silently start reading a third file if the upstream project ever adds
        # one, and a reader that picks up an unexpected file is a reader nobody
        # reasoned about.
        return (
            self._files / "auto_trade_positions_a.json",
            self._files / "auto_trade_positions_b.json",
        )
