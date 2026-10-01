"""The position snapshot reader.

The contract these tests pin was **observed**, not designed: from a live terminal
on this machine, and from the indicator's own source in the `auto-trade` checkout.
That distinction matters more than usual here, because the whole point of the
module is that a reader nobody reasoned about is a reader that picks up an
unexpected file.

Every test runs against a temporary directory. None opens MetaTrader 5, and the
one marked `live` reads the terminal's real snapshot **read-only** — no terminal
is launched, nothing is clicked, and no order exists anywhere near it. That test
skips unless `BRIDGE_ALLOW_MT5_TESTS=1`, which is Phase 11's opt-in marker.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from signal_to_trade_bridge.adapters.mt5.bindings import MT5Unavailable
from signal_to_trade_bridge.adapters.mt5.positions import (
    DEFAULT_MAX_AGE,
    MT5PositionReader,
    parse_snapshot,
)
from signal_to_trade_bridge.domain.enums import Direction

NOW = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)

#: The envelope exactly as a live terminal wrote it, including the two facts this
#: reader had no way to guess: the double buffer is ``sequence % 2`` with the
#: other file *deleted*, and the side is spelled ``BUY``/``SELL``.
_LIVE_ENVELOPE = {
    "schema": 1,
    "sequence": 410,
    "complete": True,
    "written_at": "2026-10-01T05:30:16Z",
    "account": 53184454,
    "server": "Alpari-MT5-Demo",
    "terminal_build": 6230,
    "positions": [],
}

#: A captured real entry, from the indicator's writer and a documented snapshot.
#: The four fields here are the four `auto-trade`'s verifier matches on.
_LIVE_ENTRY = {
    "ticket": 382363348,
    "symbol": "BITCOIN",
    "type": "BUY",
    "volume": 0.01,
    "price_open": 84484.0,
    "sl": 0.0,
    "tp": 0.0,
    "profit": 1.64,
    "magic": 0,
    "opened_at": "2026-09-27T08:52:15Z",
}


def _envelope(**overrides: object) -> dict[str, object]:
    base = dict(_LIVE_ENVELOPE)
    # Fresh by default, so a test that is not *about* staleness is not refused by the
    # staleness guard. The frozen live timestamp is kept on _LIVE_ENVELOPE for the
    # test that asserts the observed format verbatim.
    base["written_at"] = (NOW - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base.update(overrides)
    return base


def _write(root: Path, name: str, document: dict[str, object]) -> Path:
    files = root / "MQL5" / "Files"
    files.mkdir(parents=True, exist_ok=True)
    path = files / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def data_path(tmp_path: Path) -> Path:
    return tmp_path


def _reader(data_path: Path, **kwargs: object) -> MT5PositionReader:
    return MT5PositionReader(data_path, now=lambda: NOW, **kwargs)


class TestTheEnvelope:
    def test_a_live_envelope_parses(self) -> None:
        snapshot = parse_snapshot(json.dumps(_LIVE_ENVELOPE))
        assert snapshot.sequence == 410
        assert snapshot.account == 53184454
        assert snapshot.server == "Alpari-MT5-Demo"
        assert snapshot.terminal_build == 6230
        assert snapshot.count == 0

    def test_the_written_at_is_utc(self) -> None:
        # The trailing `Z` is what the indicator writes, via `UtcStamp`. If it
        # were dropped and the value read as local time, every snapshot would
        # appear hours stale and the gate would refuse for the wrong reason.
        snapshot = parse_snapshot(json.dumps(_LIVE_ENVELOPE))
        assert snapshot.written_at == datetime(2026, 10, 1, 5, 30, 16, tzinfo=UTC)
        assert snapshot.written_at.tzinfo is not None

    def test_an_unknown_schema_is_refused_rather_than_interpreted(self) -> None:
        # A different schema number is a different shape. Interpreting it anyway is
        # a guess, and a count guessed from an unknown shape is worse than no count.
        with pytest.raises(MT5Unavailable) as caught:
            parse_snapshot(json.dumps(_envelope(schema=2)))
        assert "schema" in str(caught.value)

    def test_a_missing_sequence_is_refused(self) -> None:
        # It is the only thing that orders the two files, so without it neither
        # can be preferred and the double buffer cannot be used at all.
        document = _envelope()
        del document["sequence"]
        with pytest.raises(MT5Unavailable, match="sequence"):
            parse_snapshot(json.dumps(document))

    def test_an_incomplete_snapshot_is_refused(self) -> None:
        # Defensive: this indicator writes the literal `true` and never `false`.
        # The check is kept because reading a file that says it is incomplete as
        # though it were complete is worse than refusing, and auto-trade's own
        # reader keeps the same check.
        with pytest.raises(MT5Unavailable, match="incomplete"):
            parse_snapshot(json.dumps(_envelope(complete=False)))

    def test_a_missing_written_at_is_refused(self) -> None:
        # It is the only freshness signal available, and a count with no way to
        # know how old it is is a count nothing should act on.
        document = _envelope()
        del document["written_at"]
        with pytest.raises(MT5Unavailable, match="written_at"):
            parse_snapshot(json.dumps(document))

    def test_a_truncated_file_is_refused_rather_than_half_read(self) -> None:
        # The designed state of the double buffer: the target is truncated while
        # the previous complete copy still exists. This is what the reader's
        # collect-then-choose strategy is for.
        with pytest.raises(MT5Unavailable, match="not valid JSON"):
            parse_snapshot('{"schema": 1, "sequence": 41')

    def test_malformed_json_names_the_other_file_as_the_recovery(self) -> None:
        with pytest.raises(MT5Unavailable, match="other one is kept"):
            parse_snapshot("{oops", source=Path("a.json"))


class TestAPositionEntry:
    def test_the_four_fields_the_verifier_matches_on_are_parsed(self) -> None:
        snapshot = parse_snapshot(json.dumps(_envelope(positions=[_LIVE_ENTRY])))
        assert snapshot.count == 1
        position = snapshot.positions[0]
        assert position.ticket == 382363348
        assert position.symbol == "BITCOIN"
        assert position.direction is Direction.LONG
        assert position.volume == Decimal("0.01")

    def test_the_indicator_buy_becomes_the_bridges_long(self) -> None:
        # A translation, not a rename: a broker's BUY is not always the bridge's
        # LONG, and reading a snapshot as the wrong direction would make a long
        # look like a short.
        snapshot = parse_snapshot(
            json.dumps(_envelope(positions=[{**_LIVE_ENTRY, "type": "SELL"}]))
        )
        assert snapshot.positions[0].direction is Direction.SHORT

    def test_volume_is_parsed_as_a_decimal_not_through_a_float(self) -> None:
        # The verifier matches on exact Decimal volume equality, so a float
        # round-trip would fail the verification of a trade that succeeded. The
        # numbers below are not exactly representable as binary doubles.
        snapshot = parse_snapshot(
            json.dumps(_envelope(positions=[{**_LIVE_ENTRY, "volume": 0.07}]))
        )
        assert str(snapshot.positions[0].volume) == "0.07"
        assert snapshot.positions[0].volume == Decimal("0.07")

    def test_the_unused_fields_are_available_but_not_modelled(self) -> None:
        # `price_open`, `sl`, `tp`, `profit`, `magic`, `opened_at` are in the file
        # and deliberately absent from the model, so no code can come to depend on
        # a field nobody has thought about. `raw` keeps them for when one does.
        snapshot = parse_snapshot(json.dumps(_envelope(positions=[_LIVE_ENTRY])))
        assert snapshot.raw[0]["sl"] == 0.0
        assert snapshot.raw[0]["opened_at"] == "2026-09-27T08:52:15Z"
        assert not hasattr(snapshot.positions[0], "sl")

    @pytest.mark.parametrize("field", ["ticket", "symbol", "type", "volume"])
    def test_a_missing_required_field_refuses_the_whole_snapshot(self, field: str) -> None:
        # Not skipped. A skipped entry under-counts, and an under-count is a
        # concurrency gate that admits a trade it should have refused.
        entry = {k: v for k, v in _LIVE_ENTRY.items() if k != field}
        with pytest.raises(MT5Unavailable):
            parse_snapshot(json.dumps(_envelope(positions=[entry])))

    def test_an_unexpected_side_names_the_value(self) -> None:
        with pytest.raises(MT5Unavailable, match="'LONG'"):
            parse_snapshot(json.dumps(_envelope(positions=[{**_LIVE_ENTRY, "type": "LONG"}])))

    def test_a_zero_volume_refuses_rather_than_counting_it(self) -> None:
        with pytest.raises(MT5Unavailable, match="non-positive"):
            parse_snapshot(json.dumps(_envelope(positions=[{**_LIVE_ENTRY, "volume": 0}])))

    def test_positions_can_be_filtered_by_symbol(self) -> None:
        snapshot = parse_snapshot(
            json.dumps(
                _envelope(
                    positions=[
                        _LIVE_ENTRY,
                        {**_LIVE_ENTRY, "ticket": 2, "symbol": "EURUSD"},
                    ]
                )
            )
        )
        assert snapshot.count == 2
        assert len(snapshot.for_symbol("eurusd")) == 1
        assert snapshot.for_symbol("EURUSD")[0].ticket == 2
        assert snapshot.for_symbol("XAUUSD") == ()


class TestTheDoubleBuffer:
    def test_one_file_is_the_normal_state(self, data_path: Path) -> None:
        # The writer picks a or b by `sequence % 2` and *deletes the other* after
        # a successful write, so in normal operation exactly one file exists. A
        # reader that demanded both would fail exactly when one is deleted.
        _write(data_path, "auto_trade_positions_a.json", _envelope(sequence=411))
        assert _reader(data_path).read().sequence == 411

    def test_the_higher_sequence_wins_when_both_exist(self, data_path: Path) -> None:
        # The recovery state: a crash left two files behind.
        _write(data_path, "auto_trade_positions_a.json", _envelope(sequence=410))
        _write(data_path, "auto_trade_positions_b.json", _envelope(sequence=411))
        assert _reader(data_path).read().sequence == 411

    def test_a_truncated_file_falls_back_to_the_good_one(self, data_path: Path) -> None:
        # The designed window: the target is being written, the previous complete
        # snapshot is still there. Refusing here would refuse a good read.
        files = data_path / "MQL5" / "Files"
        files.mkdir(parents=True, exist_ok=True)
        (files / "auto_trade_positions_a.json").write_text('{"schema": 1, "seq', "utf-8")
        _write(data_path, "auto_trade_positions_b.json", _envelope(sequence=410))
        assert _reader(data_path).read().sequence == 410

    def test_two_files_claiming_one_sequence_refuses(self, data_path: Path) -> None:
        # The writer stopped mid-rotation, or two terminals write into one folder.
        # Picking either would be a coin toss presented as a fact.
        _write(data_path, "auto_trade_positions_a.json", _envelope(sequence=410))
        _write(data_path, "auto_trade_positions_b.json", _envelope(sequence=410))
        with pytest.raises(MT5Unavailable, match="neither can be preferred"):
            _reader(data_path).read()

    def test_no_file_at_all_refuses_and_names_the_indicator(self, data_path: Path) -> None:
        # Not a default. "The count is not known" and "there are no positions" are
        # different answers, and only one of them is a reason to admit a trade.
        with pytest.raises(MT5Unavailable, match=r"AutoTradePositionReader|not attached"):
            _reader(data_path).read()

    def test_the_file_names_are_the_observed_ones(self, data_path: Path) -> None:
        # Written out rather than globbed: a glob would silently start reading a
        # third file if the upstream project ever adds one.
        _write(data_path, "auto_trade_positions_a.json", _envelope())
        reader = _reader(data_path)
        names = {p.name for p in reader._paths()}
        assert names == {"auto_trade_positions_a.json", "auto_trade_positions_b.json"}


class TestStaleness:
    def test_a_fresh_snapshot_is_believed(self, data_path: Path) -> None:
        _write(
            data_path,
            "auto_trade_positions_a.json",
            _envelope(written_at="2026-10-01T08:59:40Z"),
        )
        assert _reader(data_path).read().count == 0

    def test_a_snapshot_at_the_limit_is_still_believed(self, data_path: Path) -> None:
        # The boundary. Thirty seconds is thirty missed writes at the indicator's
        # one-per-second cadence, and an off-by-one either refuses a live terminal
        # or believes a dead one.
        exactly = (NOW - DEFAULT_MAX_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write(data_path, "auto_trade_positions_a.json", _envelope(written_at=exactly))
        assert _reader(data_path).read().count == 0

    def test_one_second_past_the_limit_refuses(self, data_path: Path) -> None:
        past = (NOW - DEFAULT_MAX_AGE - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write(data_path, "auto_trade_positions_a.json", _envelope(written_at=past))
        with pytest.raises(MT5Unavailable, match=r"stale|it has stopped"):
            _reader(data_path).read()

    def test_the_message_explains_what_staleness_means(self, data_path: Path) -> None:
        old = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write(data_path, "auto_trade_positions_a.json", _envelope(written_at=old))
        with pytest.raises(MT5Unavailable) as caught:
            _reader(data_path).read()
        assert "concurrency gate" in str(caught.value)

    def test_a_future_timestamp_counts_as_stale(self, data_path: Path) -> None:
        # A clock that moved backwards, or a file written by a terminal on another
        # machine. Both produce a timestamp nothing should act on, and clamping it
        # would be inventing a reading.
        ahead = (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write(data_path, "auto_trade_positions_a.json", _envelope(written_at=ahead))
        with pytest.raises(MT5Unavailable):
            _reader(data_path).read()

    def test_the_limit_is_auto_trades_own_default(self) -> None:
        # Not a taste. `MT5FilePositionSnapshotProvider` in the upstream project
        # uses the same 30 seconds, derived the same way, and the two must agree
        # or the same terminal looks alive to one reader and dead to the other.
        assert timedelta(seconds=30) == DEFAULT_MAX_AGE


class TestTheTolerances:
    """The places this reader is deliberately lenient, and why.

    Every tolerance below is on a field that is recorded for traceability and
    drives nothing. The fields that *do* drive behaviour -- ``sequence``,
    ``written_at``, ``schema`` and the four per-position keys -- are strict, and
    the two groups are kept apart deliberately: leniency on the first and
    strictness on the second is a line worth drawing once and writing down.
    """

    def test_a_json_array_rather_than_an_object_is_refused(self) -> None:
        # The envelope is an object because a bare list has nowhere to put the
        # sequence and the timestamp.
        with pytest.raises(MT5Unavailable, match="not a JSON object"):
            parse_snapshot("[]")

    def test_a_timestamp_without_a_timezone_is_read_as_utc(self) -> None:
        # The indicator writes `Z`, so this path is not exercised in production.
        # It is pinned because assuming *local* time instead would make every
        # snapshot appear hours stale -- and this project has already decided
        # that assuming a timezone in upstream data is how one field ends up
        # meaning two things.
        snapshot = parse_snapshot(json.dumps(_envelope(written_at="2026-10-01T08:59:59")))
        assert snapshot.written_at == datetime(2026, 10, 1, 8, 59, 59, tzinfo=UTC)

    def test_an_unreadable_timestamp_is_refused_not_guessed(self) -> None:
        with pytest.raises(MT5Unavailable, match="unreadable written_at"):
            parse_snapshot(json.dumps(_envelope(written_at="last tuesday")))

    def test_traceability_fields_accept_text_and_report_absent(self) -> None:
        # MQL5 is not a typed target, so a number can arrive as a string. These
        # three are recorded and drive nothing, so tolerating that is safe -- and
        # a value that cannot be read at all becomes `None` rather than a guess.
        snapshot = parse_snapshot(
            json.dumps(
                _envelope(
                    account="53184454",
                    server="  ",
                    terminal_build="not a number",
                )
            )
        )
        assert snapshot.account == 53184454
        assert snapshot.server is None
        assert snapshot.terminal_build is None

    def test_an_observed_position_serialises(self) -> None:
        # The snapshot's own record of what it read, for a decision log that has to
        # say which positions were open at the time.
        position = parse_snapshot(json.dumps(_envelope(positions=[_LIVE_ENTRY]))).positions[0]
        assert position.to_dict() == {
            "ticket": 382363348,
            "symbol": "BITCOIN",
            "type": "LONG",
            "volume": "0.01",
        }

    def test_the_count_shortcut_agrees_with_the_snapshot(self, data_path: Path) -> None:
        # What the account provider calls. Kept as a separate test because a
        # shortcut that disagreed with the method it wraps would be a count that
        # varies with which one the caller happened to use.
        _write(data_path, "auto_trade_positions_a.json", _envelope(sequence=411))
        reader = _reader(data_path)
        assert reader.count() == reader.read().count

    def test_a_non_object_entry_is_refused(self) -> None:
        # Not skipped: a skipped entry under-counts, and an under-count admits a
        # trade the concurrency gate should have refused.
        with pytest.raises(MT5Unavailable, match="not a JSON object"):
            parse_snapshot(json.dumps(_envelope(positions=["EURUSD buy"])))

    def test_a_missing_positions_list_is_refused(self) -> None:
        # The one number this module exists to produce. A snapshot without the
        # list cannot produce it, and defaulting to an empty list would report
        # "flat" for a document that says nothing at all.
        document = _envelope()
        del document["positions"]
        with pytest.raises(MT5Unavailable, match="no positions list"):
            parse_snapshot(json.dumps(document))

    def test_a_traceability_number_arriving_as_a_json_float_is_read(self) -> None:
        # `parse_float=Decimal` means every number in the document is a `Decimal`,
        # including the ones nothing depends on. The branch is pinned so that a
        # future change to the parse hooks cannot quietly break the int fields.
        snapshot = parse_snapshot(json.dumps(_envelope(terminal_build=6230, account=53184454)))
        assert snapshot.terminal_build == 6230
        assert snapshot.account == 53184454

    def test_a_boolean_is_not_mistaken_for_an_integer(self) -> None:
        # `isinstance(True, int)` is true in Python, and `True` as a ticket or a
        # terminal build would be a value nobody published. The envelope's
        # `sequence` check makes the same distinction for the same reason.
        assert parse_snapshot(json.dumps(_envelope(account=True))).account is None

    def test_a_number_written_with_a_decimal_point_is_still_read(self) -> None:
        # MQL5's `IntegerToString` writes `6230`, but nothing forbids a future
        # writer emitting `6230.0`, and with `parse_float=Decimal` that arrives as
        # a `Decimal` rather than an `int`. Recorded, not refused: the field drives
        # nothing.
        assert parse_snapshot(json.dumps(_envelope(terminal_build=6230.0))).terminal_build == 6230

    def test_a_field_of_an_unexpected_type_becomes_absent_rather_than_a_guess(self) -> None:
        # A traceability field arriving as a list is not a number and not nothing.
        # Reporting `None` is honest; coercing it would be inventing a reading.
        assert parse_snapshot(json.dumps(_envelope(account=[1, 2]))).account is None


class TestConfiguration:
    def test_a_data_path_is_required(self) -> None:
        # Constructed eagerly rather than defaulting to a machine's path. A default
        # here would be exactly the `AUTO_TRADE_DATA_PATH` mistake the Phase 0
        # audit recorded: a committed path to somebody else's terminal.
        with pytest.raises(MT5Unavailable, match="BRIDGE_MT5_DATA_PATH"):
            MT5PositionReader()

    def test_the_default_limit_is_constructible_without_a_terminal(self) -> None:
        assert MT5PositionReader(Path("C:/nowhere"))._max_age == DEFAULT_MAX_AGE


@pytest.mark.mt5
class TestAgainstTheLiveTerminal:
    """The one test that reads the real indicator.

    Read-only, and opt-in behind `BRIDGE_ALLOW_MT5_TESTS=1`. It launches no
    terminal, clicks nothing, and is nowhere near an order — it reads a JSON file
    the terminal has already written. **A skip is not a pass**, so this is the one
    test whose green-ness is worth checking by hand before trusting Phase 11.
    """

    def _reader(self) -> MT5PositionReader:
        import os

        data_path = os.environ.get("BRIDGE_MT5_DATA_PATH")
        if not data_path:
            pytest.skip("BRIDGE_MT5_DATA_PATH is not set")
        return MT5PositionReader(Path(data_path))

    def test_a_live_snapshot_is_readable(self) -> None:
        snapshot = self._reader().read()
        assert snapshot.terminal_build == 6230, (
            "the terminal build is a load-bearing fact and auto-trade's control ids were "
            "measured on 6184; if this changes, re-check before trusting the UI adapter"
        )
        assert snapshot.server == "Alpari-MT5-Demo", "expected the demo account, not a live one"

    def test_the_sequence_advances_between_reads(self) -> None:
        # The one-second cadence is what makes the 30-second staleness limit a
        # count of missed writes rather than a guess.
        import time

        reader = self._reader()
        first = reader.read()
        time.sleep(2.5)
        second = reader.read()
        assert second.sequence > first.sequence

    def test_it_is_a_demo_account_and_not_a_live_one(self) -> None:
        # The single most important thing to confirm about any terminal the bridge
        # is pointed at. `demo_only` in the upstream project is decided by the
        # window title; this is the account side of the same question.
        assert "demo" in (self._reader().read().server or "").lower()
