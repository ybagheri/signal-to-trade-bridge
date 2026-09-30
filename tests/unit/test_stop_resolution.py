"""Stop-loss resolution.

The tests are organised around the project's central rule rather than around the
code's structure, because that rule is what a later change must not break:

    **There is no code path that invents a stop.**

So there is a test that asserts no such path exists, a test that every refusal
path returns a distinct reason, and a test that a stop on the wrong side is
caught here rather than handed to the broker.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from signal_to_trade_bridge.domain.enums import (
    Direction,
    RejectionReason,
    SignalAction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.models import RiskParameters, Signal
from signal_to_trade_bridge.domain.stops import (
    STRUCTURAL_STOP_BASES,
    is_protective,
    is_structural_basis,
    resolve_stop,
)


def _signal(
    *,
    direction: Direction = Direction.LONG,
    entry: str = "1.10000",
    stop: str | None = "1.09700",
    basis: str = "PULLBACK_EXTREME",
) -> Signal:
    """A tradable signal with an optional stop, for the stop resolver to judge."""
    action = SignalAction.BUY if direction is Direction.LONG else SignalAction.SELL
    return Signal(
        signal_id="stb-test-stop",
        symbol="EURUSD",
        timeframe="H1",
        action=action,
        direction=direction,
        entry=Decimal(entry),
        stop_loss=Decimal(stop) if stop is not None else None,
        stop_basis=basis,
    )


def _risk(**overrides: object) -> RiskParameters:
    defaults: dict[str, object] = {
        "risk_percent": Decimal("0.5"),
        "reward_risk_ratio": Decimal("1.0"),
        "take_profit_source": TakeProfitSource.RR_FALLBACK,
    }
    defaults.update(overrides)
    return RiskParameters(**defaults)  # type: ignore[arg-type]


class TestStructuralBases:
    @pytest.mark.parametrize(
        "basis", ["PULLBACK_EXTREME", "BREAKOUT_REFERENCE", "PATTERN_EXTREME", "SWING"]
    )
    def test_a_level_the_market_produced_is_structural(self, basis: str) -> None:
        assert is_structural_basis(basis)

    @pytest.mark.parametrize("basis", ["ATR_FALLBACK", "NONE", ""])
    def test_a_volatility_multiple_is_not_structural(self, basis: str) -> None:
        assert not is_structural_basis(basis)

    def test_an_unknown_basis_is_treated_as_non_structural(self) -> None:
        # The safe reading of an unrecognised provenance. Treating it as
        # structural because it is not on the refusal list would mean a new
        # upstream stop type gets traded automatically the first time anyone
        # produces one.
        assert not is_structural_basis("SOME_FUTURE_BASIS")

    def test_the_bases_mirror_the_upstream_predicate(self) -> None:
        # The engine's own `has_structural_stop` is exactly
        # `stop_basis not in ("ATR_FALLBACK", "NONE")`. Pinned so a change on
        # either side is visible here.
        assert {
            "PULLBACK_EXTREME",
            "BREAKOUT_REFERENCE",
            "PATTERN_EXTREME",
            "SWING",
        } == STRUCTURAL_STOP_BASES
        assert not (STRUCTURAL_STOP_BASES & {"ATR_FALLBACK", "NONE"})

    def test_basis_matching_is_case_and_whitespace_insensitive(self) -> None:
        assert is_structural_basis("  swing  ")
        assert is_structural_basis("PULLBACK_EXTREME")


class TestProtectiveSide:
    def test_a_long_is_protected_by_a_stop_below_entry(self) -> None:
        assert is_protective(Direction.LONG, Decimal("100"), Decimal("99"))

    def test_a_short_is_protected_by_a_stop_above_entry(self) -> None:
        assert is_protective(Direction.SHORT, Decimal("100"), Decimal("101"))

    def test_a_long_with_a_stop_above_entry_is_not_protected(self) -> None:
        # The check the execution layer does not make. Without it this reaches
        # the broker, which refuses it after the round trip.
        assert not is_protective(Direction.LONG, Decimal("100"), Decimal("101"))

    def test_a_short_with_a_stop_below_entry_is_not_protected(self) -> None:
        assert not is_protective(Direction.SHORT, Decimal("100"), Decimal("99"))

    def test_a_stop_at_entry_is_not_protective(self) -> None:
        assert not is_protective(Direction.LONG, Decimal("100"), Decimal("100"))
        assert not is_protective(Direction.SHORT, Decimal("100"), Decimal("100"))

    def test_a_flat_direction_is_never_protective(self) -> None:
        assert not is_protective(Direction.FLAT, Decimal("100"), Decimal("99"))


class TestResolution:
    def test_a_structural_stop_resolves(self) -> None:
        resolution = resolve_stop(_signal(), _risk())
        assert resolution.ok
        assert resolution.reason is None
        stop = resolution.unwrap()
        assert stop.price == Decimal("1.09700")
        assert stop.distance == Decimal("0.00300")
        assert stop.source is StopSource.SIGNAL
        assert stop.basis == "PULLBACK_EXTREME"

    def test_the_distance_is_the_magnitude_never_a_signed_value(self) -> None:
        # Every consumer needs the distance and none should re-derive it, because
        # a re-derived one can carry a sign error and a negative stop distance
        # divides into a negative volume.
        long_stop = resolve_stop(_signal(), _risk()).unwrap()
        short_stop = resolve_stop(
            _signal(direction=Direction.SHORT, entry="1.10000", stop="1.10300"), _risk()
        ).unwrap()
        assert long_stop.distance == short_stop.distance == Decimal("0.00300")
        assert long_stop.distance > 0
        assert short_stop.distance > 0

    def test_a_short_resolves_with_its_stop_above(self) -> None:
        stop = resolve_stop(_signal(direction=Direction.SHORT, stop="1.10300"), _risk()).unwrap()
        assert stop.price == Decimal("1.10300")
        assert stop.distance == Decimal("0.00300")

    def test_a_swing_basis_resolves(self) -> None:
        stop = resolve_stop(_signal(basis="SWING"), _risk()).unwrap()
        assert stop.basis == "SWING"
        assert stop.source is StopSource.SIGNAL

    def test_resolution_records_that_the_stop_was_structural(self) -> None:
        assert resolve_stop(_signal(), _risk()).details["structural"] is True

    def test_the_signal_id_is_carried_into_the_details(self) -> None:
        # Traceability: a refusal that does not name the signal is a refusal
        # somebody has to search for.
        assert resolve_stop(_signal(), _risk()).details["signal_id"] == "stb-test-stop"


class TestNoStopMeansNoTrade:
    def test_a_missing_stop_is_refused(self) -> None:
        resolution = resolve_stop(_signal(stop=None), _risk())
        assert not resolution.ok
        assert resolution.reason is RejectionReason.NO_VALID_STOP

    def test_the_refusal_says_the_bridge_does_not_invent_one(self) -> None:
        # The message is part of the contract. An operator reading it should
        # understand that the answer will not change by waiting.
        assert "does not invent one" in resolve_stop(_signal(stop=None), _risk()).explanation

    def test_the_refusal_explains_the_upstream_zero_convention(self) -> None:
        # The engine reports an undefined level as 0.0 and the adapter preserves
        # that as absent. Without this sentence the refusal looks like a bug.
        explanation = resolve_stop(_signal(stop=None), _risk()).explanation
        assert "0.0" in explanation

    def test_a_zero_stop_is_absent_not_a_stop_at_zero(self) -> None:
        # A stop at zero would be wrong by the entire size of the instrument and
        # would produce an enormous position rather than an error.
        assert resolve_stop(_signal(stop="0"), _risk()).reason is RejectionReason.NO_VALID_STOP

    def test_a_negative_stop_is_absent(self) -> None:
        assert resolve_stop(_signal(stop="-1.0"), _risk()).reason is RejectionReason.NO_VALID_STOP

    @pytest.mark.parametrize("price", ["0", "-1.0", "-0.00001"])
    def test_an_unusable_price_refuses_rather_than_raising(self, price: str) -> None:
        """A refusal must arrive as a value, never as an exception.

        This is the check that caught a real bug: `StopLoss.__post_init__` raises
        `ValueError` for a non-positive price, so passing a zero straight through
        made the most common outcome in the system -- a signal with no stop --
        escape as an exception instead of a reason code. A trading loop that
        caught `ValueError` around this call would be handling an expected
        condition as a fault, and one that did not would crash on a quiet market.
        """
        resolution = resolve_stop(_signal(stop=price), _risk())
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.NO_VALID_STOP
        assert resolution.value is None

    def test_the_message_explains_the_upstream_zero_convention(self) -> None:
        explanation = resolve_stop(_signal(stop="0"), _risk()).explanation
        assert "0.0" in explanation
        assert "no stop" in explanation

    def test_there_is_no_fallback_that_produces_a_stop(self) -> None:
        """No code path invents a stop.

        The single most important test in this file, and the reason the rule is
        enforced rather than merely documented. It walks the module's own AST and
        asserts that no constant anywhere in it could serve as a default stop
        distance, and that no function synthesises a price.

        The architectural test covers imports; this one covers the *absence of
        behaviour*, which is the thing a reviewer has to be told about and the
        thing no import rule would catch.
        """
        import ast
        import inspect

        from signal_to_trade_bridge.domain import stops as module

        tree = ast.parse(inspect.getsource(module))

        # No numeric literal that could be a price, a distance, or a percentage
        # of anything. The only permitted numbers are the zero comparisons and
        # the epsilon in `_MIN_MEANINGFUL_DISTANCE`, both of which are compared
        # rather than returned.
        allowed_numbers = {0, 1, 8}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                assert node.value in allowed_numbers, (
                    f"stops.py contains the literal {node.value!r} at line {node.lineno}. "
                    f"Every number in this module is expected to be a comparison bound, not a "
                    f"level: a literal here is how a synthesised stop gets introduced."
                )

        # And no function builds a StopLoss from anything but the signal's own
        # price. Checked structurally: every StopLoss construction must take its
        # price from a name that came out of the signal.
        constructions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "StopLoss"
        ]
        assert constructions, "expected the resolver to construct StopLoss values"
        for call in constructions:
            price_keyword = next((kw for kw in call.keywords if kw.arg == "price"), None)
            assert price_keyword is not None, (
                "a StopLoss was constructed without a price keyword argument"
            )
            source = ast.unparse(price_keyword.value)
            assert source == "price", (
                f"a StopLoss price came from {source!r} at line {price_keyword.lineno}, not "
                f"from the signal's own stop. Only a price the signal supplied may be used."
            )


class TestWrongSide:
    def test_a_long_with_a_stop_above_entry_is_refused(self) -> None:
        resolution = resolve_stop(_signal(stop="1.10300"), _risk())
        assert resolution.reason is RejectionReason.STOP_ON_WRONG_SIDE

    def test_a_short_with_a_stop_below_entry_is_refused(self) -> None:
        resolution = resolve_stop(_signal(direction=Direction.SHORT, stop="1.09700"), _risk())
        assert resolution.reason is RejectionReason.STOP_ON_WRONG_SIDE

    def test_the_message_names_the_expected_side(self) -> None:
        assert "below" in resolve_stop(_signal(stop="1.10300"), _risk()).explanation

    def test_the_message_says_the_broker_would_have_refused_it(self) -> None:
        # The reason this check exists here rather than downstream.
        assert "broker" in resolve_stop(_signal(stop="1.10300"), _risk()).explanation

    def test_the_wrong_side_is_checked_before_the_basis(self) -> None:
        # A stop on the wrong side is a worse problem than an empty one, and the
        # message should name the actual fault rather than the later one.
        resolution = resolve_stop(_signal(stop="1.10300", basis="ATR_FALLBACK"), _risk())
        assert resolution.reason is RejectionReason.STOP_ON_WRONG_SIDE

    def test_the_refusal_records_both_prices(self) -> None:
        details = resolve_stop(_signal(stop="1.10300"), _risk()).details
        assert details["entry"] == "1.10000"
        assert details["stop"] == "1.10300"


class TestStopDistance:
    def test_a_stop_at_the_entry_is_refused_as_a_distance_problem(self) -> None:
        # A stop at the entry does not limit loss; it only guarantees the spread
        # is paid. Reported as a distance problem rather than a side problem,
        # because the stop is not on the wrong side -- it is on no side at all,
        # and an operator reading "wrong side" would go looking for a sign error
        # that is not there.
        resolution = resolve_stop(_signal(stop="1.10000"), _risk())
        assert resolution.reason is RejectionReason.INVALID_STOP_DISTANCE

    def test_a_sub_tick_distance_is_refused(self) -> None:
        resolution = resolve_stop(_signal(stop="1.09999999999"), _risk())
        assert resolution.reason is RejectionReason.INVALID_STOP_DISTANCE

    def test_a_tiny_but_real_distance_resolves(self) -> None:
        # One tick on a 5-digit pair. Refusing this would be refusing a trade the
        # broker would accept, so the threshold has to be a rounding artefact and
        # not a practical one.
        stop = resolve_stop(_signal(stop="1.09999"), _risk()).unwrap()
        assert stop.distance == Decimal("0.00001")

    def test_the_distance_is_recorded_even_on_a_refusal(self) -> None:
        # A refused trade still had arithmetic performed on it, and recording it
        # is what makes the refusal debuggable rather than merely negative.
        #
        # Compared as `Decimal` rather than as a string, because `str(Decimal)`
        # switches to exponent notation below 1e-6 -- `1E-11`, not
        # `0.00000000001`. Both round-trip exactly, so this is a test-formatting
        # choice, not a defect, and the numeric comparison is the honest one.
        from decimal import Decimal as D

        details = resolve_stop(_signal(stop="1.09999999999"), _risk()).details
        assert D(str(details["stop_distance"])) == D("0.00000000001")
        # And for a wrong-side stop, which is a refusal that had computed a
        # perfectly ordinary distance before being rejected.
        details = resolve_stop(_signal(stop="1.10300"), _risk()).details
        assert details["stop_distance"] == "0.00300"


class TestVolatilityFallback:
    def test_a_volatility_fallback_is_refused_by_default(self) -> None:
        # The brief says not to invent a stop merely to make the system trade,
        # and the engine itself calls such a plan "structurally empty".
        resolution = resolve_stop(_signal(basis="ATR_FALLBACK"), _risk())
        assert resolution.reason is RejectionReason.STRUCTURAL_STOP_REQUIRED

    def test_the_message_quotes_the_engine_on_structural_emptiness(self) -> None:
        assert (
            "structurally empty" in resolve_stop(_signal(basis="ATR_FALLBACK"), _risk()).explanation
        )

    def test_the_message_names_the_configuration_that_permits_it(self) -> None:
        # An operator should be able to act on the refusal without reading the
        # source.
        explanation = resolve_stop(_signal(basis="ATR_FALLBACK"), _risk()).explanation
        assert "BRIDGE_ALLOW_VOLATILITY_FALLBACK_STOP" in explanation

    def test_it_resolves_when_explicitly_permitted(self) -> None:
        risk = _risk(allow_volatility_fallback_stop=True)
        stop = resolve_stop(_signal(basis="ATR_FALLBACK"), risk).unwrap()
        assert stop.source is StopSource.SIGNAL_VOLATILITY_FALLBACK

    def test_a_permitted_fallback_is_recorded_as_non_structural(self) -> None:
        # The log has to say the stop was not a level the market produced, or a
        # reader six months later would assume it was.
        risk = _risk(allow_volatility_fallback_stop=True)
        resolution = resolve_stop(_signal(basis="ATR_FALLBACK"), risk)
        assert resolution.details["structural"] is False
        assert resolution.details["source"] == StopSource.SIGNAL_VOLATILITY_FALLBACK.value

    def test_a_none_basis_with_a_price_is_refused(self) -> None:
        # A price with no stated origin is not a stop this bridge can justify.
        resolution = resolve_stop(_signal(basis="NONE"), _risk())
        assert resolution.reason is RejectionReason.NO_VALID_STOP

    def test_an_unstated_basis_with_a_price_is_refused(self) -> None:
        resolution = resolve_stop(_signal(basis=""), _risk())
        assert resolution.reason is RejectionReason.NO_VALID_STOP

    def test_an_unknown_basis_is_refused_even_when_fallbacks_are_allowed(self) -> None:
        # Permitting volatility fallbacks is not a licence to trade anything
        # unfamiliar. The flag names one specific alternative.
        risk = _risk(allow_volatility_fallback_stop=True)
        resolution = resolve_stop(_signal(basis="SOME_FUTURE_BASIS"), risk)
        assert not resolution.ok
        assert resolution.reason is RejectionReason.STRUCTURAL_STOP_REQUIRED

    def test_the_message_distinguishes_unknown_from_documented(self) -> None:
        # An operator who hits an unrecognised basis must not be left thinking
        # they tripped the documented ATR case, because the two have different
        # remedies: one has a flag, the other needs the upstream engine examined.
        risk = _risk(allow_volatility_fallback_stop=True)
        explanation = resolve_stop(_signal(basis="SOME_FUTURE_BASIS"), risk).explanation
        assert "does not recognise" in explanation
        assert "does not extend to it" in explanation

    def test_the_unknown_basis_is_named_in_the_refusal(self) -> None:
        risk = _risk(allow_volatility_fallback_stop=True)
        resolution = resolve_stop(_signal(basis="SOME_FUTURE_BASIS"), risk)
        assert resolution.details["unknown_basis"] == "SOME_FUTURE_BASIS"


class TestReasonCodes:
    def test_each_failure_path_yields_a_distinct_reason(self) -> None:
        # Distinct reasons are what make a refusal countable and alertable. Two
        # unrelated failures sharing a code would make a log useless for
        # answering "why does this happen on this symbol".
        #
        # Four, not six: a missing stop and a zero stop are deliberately the same
        # refusal, because they are the same fault. The engine uses `0.0` to mean
        # "no stop", so a signal reporting either is reporting the same thing, and
        # splitting the code would make an alert fire twice for one condition.
        reasons = {
            resolve_stop(_signal(stop=None), _risk()).reason,
            resolve_stop(_signal(stop="1.10300"), _risk()).reason,
            resolve_stop(_signal(stop="1.10000"), _risk()).reason,
            resolve_stop(_signal(basis="ATR_FALLBACK"), _risk()).reason,
        }
        assert reasons == {
            RejectionReason.NO_VALID_STOP,
            RejectionReason.STOP_ON_WRONG_SIDE,
            RejectionReason.INVALID_STOP_DISTANCE,
            RejectionReason.STRUCTURAL_STOP_REQUIRED,
        }

    def test_a_missing_and_a_zero_stop_are_the_same_refusal(self) -> None:
        # One fault, one code. The upstream engine reports an absent level as
        # 0.0, so a signal carrying either is reporting the same condition, and
        # two codes for it would mean an alert on "no stop" fires twice.
        assert (
            resolve_stop(_signal(stop=None), _risk()).reason
            is resolve_stop(_signal(stop="0"), _risk()).reason
        )
        assert (
            resolve_stop(_signal(stop=None), _risk()).reason
            is resolve_stop(_signal(basis="NONE"), _risk()).reason
        )

    def test_every_refusal_carries_a_reason(self) -> None:
        # Never an empty reason. A refusal with no code is indistinguishable from
        # a bug, which is the specific failure this design exists to prevent.
        for resolution in (
            resolve_stop(_signal(stop=None), _risk()),
            resolve_stop(_signal(stop="1.10300"), _risk()),
            resolve_stop(_signal(basis="ATR_FALLBACK"), _risk()),
        ):
            assert resolution.reason is not None
            assert resolution.reason_code != "OK"
            assert resolution.explanation

    def test_a_success_reports_OK_as_its_reason_code(self) -> None:
        # So a log line always has something in the field, and a query for
        # refusals never has to tell "no reason" from "success".
        assert resolve_stop(_signal(), _risk()).reason_code == "OK"
