"""The build gate, pinned to the evidence rather than to a number.

The constant `MEASURED_ON_BUILD` moved from 6184 to 6230 in Phase 13, and it moved
only after `auto-trade terminal-check` reported every control present, in place, on
6230. That report is kept at `logs/terminal_check.json`.

**A constant that can be edited without producing evidence is not a measurement.**
So the test that matters here is the last one in this file: it requires the recorded
probe to exist, to name this build, and to report zero drifted and zero missing. If
somebody changes the number without re-measuring, that test fails -- which is the
only thing that makes the number worth anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signal_to_trade_bridge.live import (
    MEASURED_ON_BUILD,
    ControlIdCheck,
    check_control_ids,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: The durable, committed record of the measurement.
#:
#: **Not `logs/terminal_check.json`.** That is where the probe's raw report lands,
#: and `logs/` is gitignored because it holds the ledger and the audit trail --
#: runtime state. Reading it from a test would mean the guard only exists on the
#: machine that happened to run the probe, which is the same mistake as the one this
#: file exists to prevent. `scripts/record_control_ids.py` turns the raw report into
#: this file, which is committed.
RECORD = PROJECT_ROOT / "docs" / "measurements" / "control_ids.json"

TERMINAL = Path(r"C:\Program Files\Alpari MT5_4\terminal64.exe")
DATA = Path(
    r"C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal"
    r"\1D9617E1A6A4352DBDC25D08FEC12BD2"
)


class TestTheGateStillRefuses:
    """The half that must not have been softened by the reconciliation."""

    def test_a_different_build_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A build nobody measured, on purpose.
        monkeypatch.setattr("signal_to_trade_bridge.live.MEASURED_ON_BUILD", 12345)
        check = check_control_ids(TERMINAL, DATA)
        assert check.matches is False
        assert "12345" in check.refusal()

    def test_the_expected_build_is_read_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `expected` used to be a default argument, which freezes the value when the
        # module is imported. So the constant and the check that guards it could
        # disagree with no way to notice except editing the constant -- which is
        # exactly what happened when the re-measurement moved it, and why the check
        # kept comparing against 6184 after the module said 6230.
        monkeypatch.setattr("signal_to_trade_bridge.live.MEASURED_ON_BUILD", 999)
        assert check_control_ids(TERMINAL, DATA).expected == 999
        monkeypatch.setattr("signal_to_trade_bridge.live.MEASURED_ON_BUILD", 998)
        assert check_control_ids(TERMINAL, DATA).expected == 998

    def test_an_explicit_expected_still_wins(self) -> None:
        check = check_control_ids(TERMINAL, DATA, expected=4242)
        assert check.expected == 4242
        assert check.matches is False


class TestTheGatePassesOnAMeasuredBuild:
    def test_the_build_agrees_and_there_is_no_refusal_text(self) -> None:
        # **Both halves.** `matches` going True is the reconciliation; `refusal()`
        # being empty is the bug fix. It used to return the full "a gap of 0 builds"
        # paragraph, because it branched on whether the sources were readable and
        # never asked whether the builds agreed -- and `doctor` reads `matches` for
        # its verdict and `refusal()` for the text underneath, so it would have
        # printed **ok** directly above a refusal.
        check = check_control_ids(TERMINAL, DATA)
        assert check.build == MEASURED_ON_BUILD
        assert check.matches is True
        assert check.refusal() == ""

    def test_a_matching_check_with_unreadable_sources_is_still_refused(self) -> None:
        # `matches` requires agreement, so "one source said so" is not a pass even
        # when the one source that spoke happens to be right.
        check = check_control_ids(TERMINAL, Path("no-such-folder"))
        assert check.agreed is False
        assert check.matches is False
        assert check.refusal() != ""


class TestTheMeasurementIsBackedByEvidence:
    """The tests that make the number mean something."""

    def test_the_recorded_probe_exists(self) -> None:
        assert RECORD.is_file(), (
            f"{RECORD} is missing. It is the report that justifies "
            f"MEASURED_ON_BUILD={MEASURED_ON_BUILD}; without it the constant is an "
            f"assertion. Produce it with `auto-trade terminal-check`."
        )

    def test_the_recorded_probe_names_this_build(self) -> None:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        assert record["measured_on_build"] == MEASURED_ON_BUILD, (
            f"the committed record reports build {record['measured_on_build']} but "
            f"MEASURED_ON_BUILD is {MEASURED_ON_BUILD}. Re-measure with "
            f"`auto-trade terminal-check` and regenerate the record with "
            f"`scripts/record_control_ids.py` before moving the constant."
        )

    def test_the_latest_probe_found_nothing_drifted_and_nothing_missing(self) -> None:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        assert record["drifted"] == [], (
            f"the probe reported drifted controls: {record['drifted']}. A control whose "
            f"identifier changed is a control whose behaviour is not established, so "
            f"MEASURED_ON_BUILD must not be moved to match this build."
        )
        assert record["missing"] == [], f"missing controls: {record['missing']}"
        assert record["verdict"] == "OK"

    def test_every_identifier_this_project_depends_on_was_observed(self) -> None:
        # The specific controls, by the identifiers `live.py` will compare. A probe
        # that reported OK for the build but not for the order dialog's fields would
        # satisfy `verdict` and still be useless, so the fields are named here.
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        seen = record["controls"]
        for control in (
            "symbol",
            "volume",
            "stop_loss",
            "take_profit",
            "buy",
            "sell",
        ):
            assert control in seen, f"the record does not cover {control}"
            assert seen[control]["status"] == "OK", f"{control}: {seen[control]['status']}"

    def test_the_recorded_identifiers_are_the_ones_upstream_uses(self) -> None:
        # **The record is checked against upstream's source, not trusted.** A
        # committed JSON file that drifts from the code it documents is worse than
        # none, and the identifiers it records are the ones an order would be typed
        # into. Upstream keeps them in `control_probe.EXPECTED_FIELDS` and
        # `EXPECTED_FINAL_CONTROLS`, which is where the order path reads them.
        from auto_trade.infrastructure.automation.control_probe import (
            EXPECTED_FIELDS,
            EXPECTED_FINAL_CONTROLS,
        )

        record = json.loads(RECORD.read_text(encoding="utf-8"))["controls"]
        # Compared as integers. The record stores the identifier as a string and
        # upstream holds an int, and the first version of this test compared them
        # directly -- so it reported "the record says 10325, upstream uses 10325. One
        # of the two is stale" about two identical values. A guard that cries wolf
        # once gets ignored the second time, which is the failure mode this whole
        # file is about.
        for label, value in EXPECTED_FIELDS:
            assert int(record[label]["identifier"]) == int(value), (
                f"{label}: the record says {record[label]['identifier']}, upstream uses "
                f"{value}. One of the two is stale."
            )
        for label, _name, value in EXPECTED_FINAL_CONTROLS:
            assert int(record[label]["identifier"]) == int(value), (
                f"{label}: the record says {record[label]['identifier']}, upstream uses "
                f"{value}. One of the two is stale."
            )

    def test_the_record_says_how_to_reproduce_itself(self) -> None:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        assert record["command"] == "auto-trade terminal-check"
        assert record["account_type"] == "DEMO", (
            "the measurement was taken against a demo account; a record from a live "
            "account is not a record of the machine this project is allowed to trade"
        )


class TestWhatStopsAnOrderNow:
    """Gate 1 used to stop everything. These are the rungs that do now."""

    @pytest.mark.parametrize(
        ("flags", "fragment"),
        [
            ({"execution_enabled": False, "dry_run": False}, "execution is not enabled"),
            ({"execution_enabled": True, "dry_run": True}, "dry-run mode is on"),
        ],
    )
    def test_the_default_configuration_is_still_refused(
        self, tmp_path: Path, flags: dict[str, bool], fragment: str
    ) -> None:
        from signal_to_trade_bridge.composition import CompositionRefusal
        from signal_to_trade_bridge.configuration.config import BridgeConfig
        from signal_to_trade_bridge.domain.models import RiskParameters
        from signal_to_trade_bridge.live import build_live

        config = BridgeConfig(
            risk=RiskParameters(allowed_symbols={"EURUSD"}),
            log_directory=tmp_path,
            mt5_terminal_path=TERMINAL,
            mt5_data_path=DATA,
            **flags,  # type: ignore[arg-type]
        )
        with pytest.raises(CompositionRefusal) as raised:
            build_live(config, terminal=TERMINAL, data_path=DATA)
        assert fragment in str(raised.value)

    def test_the_default_composition_root_cannot_execute(self) -> None:
        # The rung that is a *code path* rather than a setting, and therefore the one
        # no environment variable can reach. `build_bridge` is what the CLI and every
        # library consumer get.
        from signal_to_trade_bridge.composition import build_bridge
        from signal_to_trade_bridge.configuration.config import BridgeConfig

        bridge = build_bridge(BridgeConfig(mt5_terminal_path=TERMINAL, mt5_data_path=DATA))
        assert getattr(bridge, "can_execute", None) is False


class TestTheCheckItself:
    def test_a_check_built_by_hand_with_a_match_says_nothing(self) -> None:
        # The unit-level version of the `refusal()` fix, so the behaviour does not
        # depend on a terminal being present.
        agreeing = ControlIdCheck(
            build=6230,
            sources={"a": 6230, "b": 6230},
            expected=6230,
            agreed=True,
        )
        assert agreeing.matches is True
        assert agreeing.refusal() == ""

    def test_a_check_built_by_hand_with_a_gap_still_explains_itself(self) -> None:
        gapped = ControlIdCheck(
            build=6230,
            sources={"a": 6230, "b": 6230},
            expected=6184,
            agreed=True,
        )
        assert gapped.matches is False
        message = gapped.refusal()
        assert "6184" in message and "6230" in message
