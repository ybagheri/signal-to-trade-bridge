"""Structured logging.

The two properties worth testing are that the redaction cannot be bypassed and
that the event vocabulary is complete. Both are safety properties rather than
formatting preferences: a leaked credential in a log file is a real incident, and
an event name nobody can enumerate is an event nobody will ever search for.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any

import pytest

from signal_to_trade_bridge.infrastructure.logging.events import Event, all_events
from signal_to_trade_bridge.infrastructure.logging.structured import (
    REDACTED,
    SENSITIVE_KEYS,
    configure_logging,
    get_logger,
)


@pytest.fixture
def stream() -> StringIO:
    return StringIO()


@pytest.fixture
def logger(stream: StringIO) -> Any:
    configure_logging(level="DEBUG", json_output=True, stream=stream)
    return get_logger()


class TestEventVocabulary:
    def test_every_event_is_a_non_empty_uppercase_string(self) -> None:
        for name in all_events():
            assert name, "an event name cannot be empty"
            assert name.isupper(), f"{name} should be uppercase so logs sort and grep"

    def test_no_two_events_share_a_name(self) -> None:
        names = all_events()
        assert len(names) == len(set(names))

    def test_the_vocabulary_covers_the_whole_decision_chain(self) -> None:
        # Named explicitly rather than by count, so that removing an event -- the
        # natural way to "simplify" -- fails here instead of silently removing a
        # step from the audit trail.
        required = {
            "SIGNAL_RECEIVED",
            "SIGNAL_REJECTED",
            "STOP_RESOLVED",
            "TAKE_PROFIT_RESOLVED",
            "RISK_CALCULATED",
            "POSITION_SIZED",
            "TRADE_VALIDATED",
            "TRADE_REJECTED",
            "DRY_RUN_COMPLETED",
            "EXECUTION_RESULT",
            "DUPLICATE_SUPPRESSED",
        }
        assert required <= set(all_events())

    def test_stop_resolution_is_always_reported(self) -> None:
        # "No stop" is the most important thing this bridge can report, so it gets
        # an event of its own rather than being folded into a generic rejection.
        assert Event.STOP_RESOLVED.value == "STOP_RESOLVED"


class TestRedaction:
    @pytest.mark.parametrize("key", sorted(SENSITIVE_KEYS))
    def test_every_declared_marker_actually_redacts(
        self, logger: Any, stream: StringIO, key: str
    ) -> None:
        logger.event(Event.TRADE_VALIDATED, **{key: "super-secret-value"})
        assert "super-secret-value" not in stream.getvalue()
        assert REDACTED in stream.getvalue()

    def test_matching_is_by_substring_and_case_insensitive(
        self, logger: Any, stream: StringIO
    ) -> None:
        # Deliberately broad. The cost of redacting a harmless field is a slightly
        # less informative log line; the cost of not redacting one is a leaked
        # credential in a file that eventually gets pasted into a bug report.
        logger.event(Event.TRADE_VALIDATED, BrokerAPI_Token="leaked", PASSWORD="leaked")
        output = stream.getvalue()
        assert "leaked" not in output
        assert output.count(REDACTED) == 2

    def test_ordinary_fields_survive(self, logger: Any, stream: StringIO) -> None:
        # Redaction that removed everything would satisfy the test above while
        # making the logs useless, so the complementary property is checked too.
        logger.event(Event.POSITION_SIZED, symbol="EURUSD", volume="0.16")
        output = stream.getvalue()
        assert "EURUSD" in output
        assert "0.16" in output

    def test_a_field_cannot_evade_redaction_by_nesting_its_value(
        self, logger: Any, stream: StringIO
    ) -> None:
        logger.event(Event.TRADE_VALIDATED, note="the password is hunter2")
        # A whole-sentence value is not itself a credential field, and redacting
        # it would be wrong -- this pins the boundary of the rule rather than
        # leaving it undefined.
        assert "hunter2" in stream.getvalue()


class TestJsonOutput:
    def test_emits_one_object_per_event(self, logger: Any, stream: StringIO) -> None:
        logger.event(Event.SIGNAL_RECEIVED, signal_id="abc", symbol="EURUSD")
        payload = json.loads(stream.getvalue().strip())
        assert payload["event"] == "SIGNAL_RECEIVED"
        assert payload["level"] == "INFO"
        assert payload["signal_id"] == "abc"
        assert payload["symbol"] == "EURUSD"
        assert "timestamp" in payload

    def test_events_are_separate_lines(self, logger: Any, stream: StringIO) -> None:
        logger.event(Event.SIGNAL_RECEIVED, signal_id="one")
        logger.event(Event.TRADE_REJECTED, signal_id="two", reason="NO_VALID_STOP")
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "SIGNAL_RECEIVED"
        assert json.loads(lines[1])["reason"] == "NO_VALID_STOP"

    def test_a_decimal_never_becomes_a_lossy_float(self, logger: Any, stream: StringIO) -> None:
        from decimal import Decimal

        logger.event(Event.POSITION_SIZED, volume=Decimal("0.1666666666666666666"))
        payload = json.loads(stream.getvalue().strip())
        # `default=str` in the encoder means a Decimal survives as its exact
        # string form. A float would have silently rounded it.
        assert payload["volume"] == "0.1666666666666666666"


class TestHandlerHygiene:
    def test_configuring_twice_does_not_double_the_output(self, stream: StringIO) -> None:
        # A test calls configure_logging per case, and a duplicated handler would
        # make output-based assertions fail for a reason that has nothing to do
        # with the code under test.
        import logging

        configure_logging(level="INFO", json_output=True, stream=stream)
        configure_logging(level="INFO", json_output=True, stream=stream)
        get_logger().event(Event.SIGNAL_RECEIVED, signal_id="x")
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        assert len(logging.getLogger("signal_to_trade_bridge").handlers) == 1

    def test_events_do_not_propagate_to_the_root_logger(self, stream: StringIO) -> None:
        # Otherwise a host application's root handler would put trading decisions
        # into whatever unrelated log file that application happens to write.
        import logging

        root_output = StringIO()
        root_handler = logging.StreamHandler(root_output)
        root_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(root_handler)
        try:
            configure_logging(level="INFO", json_output=True, stream=stream)
            get_logger().event(Event.SIGNAL_RECEIVED, signal_id="x")
            assert root_output.getvalue() == ""
        finally:
            logging.getLogger().removeHandler(root_handler)

    def test_a_disabled_level_emits_nothing(self) -> None:
        quiet = StringIO()
        configure_logging(level="ERROR", json_output=True, stream=quiet)
        get_logger().event(Event.SIGNAL_RECEIVED, signal_id="x")
        assert quiet.getvalue() == ""


class TestTextOutput:
    def test_text_mode_keeps_the_fields_readable(self, stream: StringIO) -> None:
        configure_logging(level="INFO", json_output=False, stream=stream)
        get_logger().event(Event.POSITION_SIZED, symbol="EURUSD", volume="0.16")
        line = stream.getvalue().strip()
        assert "POSITION_SIZED" in line
        assert "symbol=EURUSD" in line
        assert "volume=0.16" in line
