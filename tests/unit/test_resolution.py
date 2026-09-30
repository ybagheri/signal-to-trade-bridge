"""The ``Resolution`` type.

Small and load-bearing. It carries success and failure, and a bug in it would
silently change the meaning of every refusal in the project -- a resolution that
reported failure for a successful step would fill logs with refusals, and one
that reported success for a failure would let a missing stop through to a
position sizer.

The tests here concentrate on the case that actually broke: a step that
**succeeds with no value**. `TakeProfitSource.NONE` resolves to "there is no take
profit", and inferring success from `value is not None` made that
indistinguishable from a refusal.
"""

from __future__ import annotations

from signal_to_trade_bridge.domain.enums import RejectionReason
from signal_to_trade_bridge.domain.resolution import refused, resolved


class TestSuccess:
    def test_a_resolved_value_reports_success(self) -> None:
        assert resolved(42).ok

    def test_a_resolved_value_is_truthy(self) -> None:
        # So `if resolution:` reads correctly at a call site.
        assert bool(resolved(42))

    def test_a_successful_resolution_has_no_reason(self) -> None:
        assert resolved(42).reason is None

    def test_a_successful_resolution_reports_OK(self) -> None:
        # So a log line always has something in the field, and a query for
        # refusals never has to tell "no reason" from "success".
        assert resolved(42).reason_code == "OK"

    def test_details_are_carried_onto_a_success(self) -> None:
        assert resolved(42, symbol="EURUSD").details["symbol"] == "EURUSD"


class TestFailure:
    def test_a_refusal_is_not_ok(self) -> None:
        assert not refused(RejectionReason.NO_VALID_STOP, "no stop").ok

    def test_a_refusal_is_falsy(self) -> None:
        assert not refused(RejectionReason.NO_VALID_STOP, "no stop")

    def test_a_refusal_carries_its_reason_code(self) -> None:
        resolution = refused(RejectionReason.STOP_ON_WRONG_SIDE, "wrong side")
        assert resolution.reason_code == "STOP_ON_WRONG_SIDE"

    def test_a_refusal_keeps_the_arithmetic_it_did_on_the_way(self) -> None:
        # What makes a refusal debuggable rather than merely negative.
        resolution = refused(
            RejectionReason.STOP_ON_WRONG_SIDE,
            "wrong side",
            entry="1.10000",
            stop_distance="0.00300",
        )
        assert resolution.details["entry"] == "1.10000"
        assert resolution.details["stop_distance"] == "0.00300"


class TestSuccessWithNoValue:
    """A step can legitimately succeed with the answer "nothing".

    This is the case that was broken, and it is the reason success is an explicit
    flag rather than something inferred from the value.
    """

    def test_success_does_not_depend_on_the_value_being_present(self) -> None:
        assert resolved(None).ok

    def test_a_none_value_is_still_truthy(self) -> None:
        assert bool(resolved(None))

    def test_a_none_value_reports_OK(self) -> None:
        assert resolved(None).reason_code == "OK"

    def test_a_none_value_and_a_refusal_are_distinguishable(self) -> None:
        # The property the bug destroyed. Both have `value is None`; only one is
        # a failure, and a pipeline that told them apart by inspecting the value
        # would report every disabled take profit as a refusal.
        assert resolved(None).ok
        assert not refused(RejectionReason.INVALID_TAKE_PROFIT, "no target").ok

    def test_unwrapping_a_none_value_raises_a_clear_error(self) -> None:
        # A distinct error from a failed resolution, because the two need
        # different responses: one means "this step succeeded, read `.value`",
        # the other means "this step failed".
        import pytest

        # Raw, because the backtick-quoted `.value` in the message is a regex
        # metacharacter and an unescaped one would match any character.
        with pytest.raises(TypeError, match=r"read `\.value` directly"):
            resolved(None).unwrap()


class TestUnwrap:
    def test_unwrapping_a_success_returns_the_value(self) -> None:
        assert resolved(42).unwrap() == 42

    def test_unwrapping_a_failure_raises_with_the_reason(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="STOP_ON_WRONG_SIDE"):
            refused(RejectionReason.STOP_ON_WRONG_SIDE, "wrong side").unwrap()

    def test_the_error_message_includes_the_explanation(self) -> None:
        # An operator seeing this in a traceback should not have to go looking
        # for the reason elsewhere.
        import pytest

        with pytest.raises(ValueError, match="the broker would refuse it"):
            refused(RejectionReason.STOP_ON_WRONG_SIDE, "the broker would refuse it").unwrap()


class TestSerialisation:
    def test_a_success_serialises(self) -> None:
        payload = resolved(42, symbol="EURUSD").to_dict()
        assert payload == {
            "ok": True,
            "reason": "OK",
            "explanation": "",
            "details": {"symbol": "EURUSD"},
        }

    def test_a_failure_serialises(self) -> None:
        payload = refused(RejectionReason.NO_VALID_STOP, "no stop").to_dict()
        assert payload["ok"] is False
        assert payload["reason"] == "NO_VALID_STOP"
        assert payload["explanation"] == "no stop"

    def test_the_serialised_form_is_json_ready(self) -> None:
        # A decision record is written to a JSONL log, so this has to survive
        # `json.dumps` without a custom encoder.
        import json

        json.dumps(resolved(42, detail={"a": 1}).to_dict())
        json.dumps(refused(RejectionReason.NO_VALID_STOP, "x").to_dict())


class TestImmutability:
    def test_a_resolution_is_frozen(self) -> None:
        import pytest

        resolution = resolved(42)
        with pytest.raises(AttributeError):
            resolution.value = 7  # type: ignore[misc]

    def test_the_details_mapping_cannot_be_mutated_through_the_object(self) -> None:
        # The `details` dict is shared by reference, so a caller could otherwise
        # rewrite a decision's record after the fact. Read-only here, deliberately:
        # a decision that can be edited after it was made is not a record.
        resolution = resolved(42, symbol="EURUSD")
        try:
            resolution.details["symbol"] = "GBPUSD"  # type: ignore[index]
        except TypeError:
            return  # A read-only mapping, which is the intended behaviour.
        raise AssertionError(
            "Resolution.details is mutable, so a decision record could be rewritten after "
            "it was made. Store details in a MappingProxyType."
        )
