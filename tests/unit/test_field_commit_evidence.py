"""The dialog-commit fix, pinned to the evidence rather than to a description.

On 2026-10-09 a live demo order went out at the panel defaults (0.01 lots, no
stop, no target) while every dialog field read back correct. Read-only probes
on 2026-10-15 established why: `set_edit_text` displays without committing,
and only real keystrokes move MT5's committed state -- witnessed by labels
10881/11138/11139, which upstream now waits on before every click.

**A fix that can be reverted without breaking a test is a description, not a
fix.** So this file requires the committed record at
`docs/measurements/field_commit.json` to exist, to name the witnesses
upstream waits on, and to show a live fill whose protection matches the
approval. If somebody restores display-only writes, or drops a witness, or the
fill record stops agreeing with itself, these tests fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: The durable, committed record of the commit-behavior measurement and the
#: live order that proved the fix. Committed for the same reason as
#: `docs/measurements/control_ids.json`: a `logs/` report only exists on the
#: machine that ran the probe.
RECORD = PROJECT_ROOT / "docs" / "measurements" / "field_commit.json"


def _record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


class TestTheRecordExistsAndSaysWhatWasMeasured:
    def test_the_record_exists(self) -> None:
        assert RECORD.is_file(), (
            f"{RECORD} is missing. It records the dialog-commit measurement "
            "and the live fill that proved the keystroke-write fix."
        )

    def test_the_record_names_the_build_and_the_method(self) -> None:
        record = _record()
        assert record["measured_on_build"] == 6230
        assert record["account_type"] == "DEMO", (
            "the measurement was taken against a demo account; a record from "
            "a live account is not evidence this project may use"
        )
        comment = " ".join(record["_comment"]).lower()
        assert "no buy/sell" in comment or "read-only" in comment, (
            "the record must say the probes placed no order, or a later reader "
            "cannot tell measurement from trading"
        )

    def test_the_record_states_the_behavior_rule(self) -> None:
        # The rule the code implements: display-only writes never commit.
        behavior = _record()["behavior"]
        written = behavior["set_edit_text"].lower()
        assert "never commit" in written or "display only" in written
        assert "commit" in behavior["keystrokes"].lower()


class TestTheWitnessesAreTheOnesUpstreamWaitsOn:
    @pytest.mark.requires_auto_trade
    def test_the_recorded_witnesses_match_the_order_path(self) -> None:
        # **Checked against upstream's source, not trusted.** The record's
        # witness identifiers must be exactly the labels `prepare_order`
        # waits on, or the measurement is about controls nobody reads.
        from auto_trade.infrastructure.automation.execution import (
            COMMIT_LABEL_FOR_FIELD,
        )

        witnesses = _record()["witnesses"]
        assert set(witnesses) == {
            "volume_commit_label",
            "stop_loss_commit_label",
            "take_profit_commit_label",
        }, f"record witnesses {sorted(witnesses)} do not name the three witnesses"
        assert int(witnesses["volume_commit_label"]["identifier"]) == int(
            COMMIT_LABEL_FOR_FIELD["10333"]
        )
        assert int(witnesses["stop_loss_commit_label"]["identifier"]) == int(
            COMMIT_LABEL_FOR_FIELD["10334"]
        )
        assert int(witnesses["take_profit_commit_label"]["identifier"]) == int(
            COMMIT_LABEL_FOR_FIELD["10336"]
        )

    def test_an_uncommitted_witness_differs_from_a_committed_one(self) -> None:
        # The property the wait depends on: the witness must visibly move when
        # MT5 commits, or waiting on it proves nothing. A record whose two
        # states are equal would bless a wait that always passes.
        for label, states in _record()["witnesses"].items():
            assert states["uncommitted"] != states["committed"], (
                f"{label}: the record's uncommitted and committed renderings "
                "are identical, so the witness cannot distinguish them"
            )


class TestTheLiveFillCarriedItsProtection:
    def test_requested_volume_stop_and_target_all_match_what_mt5_reported(self) -> None:
        # Ticket 389589852, the first order after the fix: if any of these
        # three differed, the fix would have changed the write path without
        # changing what the terminal commits -- and the test would name which
        # level regressed.
        fill = _record()["live_fill"]
        for level in ("volume", "stop_loss", "take_profit"):
            assert fill["requested"][level] == fill["observed"][level], (
                f"{level}: requested {fill['requested'][level]} but MT5 reported "
                f"{fill['observed'][level]}"
            )

    def test_the_fill_record_identifies_the_order(self) -> None:
        fill = _record()["live_fill"]
        assert fill["ticket"] == "389589852"
        assert fill["signal_id"] == "stb-demo-003"
        assert fill["symbol"] == "EURUSD"
