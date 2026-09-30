"""Deterministic signal identity.

These tests pin the one property the downstream deduplication depends on: the same
reading must produce the same key, forever, on any machine. A key that changed
between calls would let the same signal through twice, and a key that was not
deterministic at all would defeat the mechanism while still passing a test that
only checked the format.
"""

from __future__ import annotations

from signal_to_trade_bridge.adapters.albrooks.identity import (
    SIGNAL_ID_PREFIX,
    compute_signal_id,
)
from signal_to_trade_bridge.domain.enums import Direction, SignalAction


def _key(**overrides: object) -> str:
    defaults: dict[str, object] = {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "bar_index": 299,
        "bar_time": 1727740800.0,
        "action": SignalAction.BUY,
        "direction": Direction.LONG,
        "setup_id": "pullback_h#0",
    }
    defaults.update(overrides)
    return compute_signal_id(**defaults)  # type: ignore[arg-type]


class TestDeterminism:
    def test_the_same_input_always_produces_the_same_key(self) -> None:
        # Not a formatting check. The downstream ledger is only useful if this
        # holds across a process restart, and nothing in the function touches a
        # clock or a random source precisely so that it can.
        first = _key()
        for _ in range(100):
            assert _key() == first

    def test_the_key_has_the_expected_shape(self) -> None:
        key = _key()
        assert key.startswith(f"{SIGNAL_ID_PREFIX}-")
        body = key.removeprefix(f"{SIGNAL_ID_PREFIX}-")
        assert len(body) == 32
        assert all(char in "0123456789abcdef" for char in body)

    def test_different_readings_produce_different_keys(self) -> None:
        assert _key() != _key(symbol="GBPUSD")
        assert _key() != _key(timeframe="M15")
        assert _key() != _key(setup_id="pullback_h#1")


class TestTheBarIsTheUnitOfIdentity:
    def test_a_new_bar_is_a_new_signal(self) -> None:
        # A reading recomputed after new bars close describes a different moment
        # in the market, and a new trade is a new decision. Without this, a signal
        # that stayed valid across a bar would be suppressed as a duplicate and
        # the setup would be missed entirely.
        assert _key(bar_index=299) != _key(bar_index=300)

    def test_a_new_bar_time_is_a_new_signal(self) -> None:
        assert _key(bar_time=1727740800.0) != _key(bar_time=1727744400.0)

    def test_a_re_delivery_of_the_same_bar_is_the_same_signal(self) -> None:
        # The case deduplication exists for. Same bar, same reading, arriving
        # twice, must produce the same key so the second one is recognised.
        assert _key(bar_index=299) == _key(bar_index=299)


class TestPricesAreExcluded:
    def test_the_prices_are_not_part_of_the_key(self) -> None:
        # A deliberate decision, and the most debatable one in the project. A
        # recomputation over the *same* closed bar that nudges a stop by one tick
        # is the same reading with a rounding difference. Including the prices
        # would let a flapping stop produce a fresh position on every cycle, which
        # is a worse failure than the one this rule accepts.
        #
        # The trade-off: if the engine genuinely revises a stop materially on the
        # same bar, the bridge treats the revision as the same signal and
        # suppresses it. Phase 9 revisits this against real engine output.
        base = _key()
        assert base == _key()

    def test_the_function_takes_no_price_arguments_at_all(self) -> None:
        # Structural rather than behavioural: if a price were ever added as a
        # parameter, this test's introspection would catch the change before it
        # silently altered every key in an existing ledger.
        import inspect

        parameters = set(inspect.signature(compute_signal_id).parameters)
        assert parameters == {
            "symbol",
            "timeframe",
            "bar_index",
            "bar_time",
            "action",
            "direction",
            "setup_id",
        }


class TestDirectionAndAction:
    def test_the_action_is_part_of_the_key(self) -> None:
        assert _key(action=SignalAction.BUY) != _key(action=SignalAction.SELL)

    def test_an_abstention_is_a_different_signal_from_a_trade(self) -> None:
        assert _key(action=SignalAction.WAIT) != _key(action=SignalAction.BUY)

    def test_the_setup_identifier_separates_two_detectors_on_one_bar(self) -> None:
        # Two setups can be valid on the same bar. They are different decisions
        # and must not collapse into one key.
        assert _key(setup_id="pullback_h#0") != _key(setup_id="double_bottom#0")


class TestEncoding:
    def test_the_symbol_is_normalised_before_hashing(self) -> None:
        # The same symbol in two casings is the same symbol, and an inconsistent
        # key here would mean a duplicate trade the moment a broker's spelling
        # changed.
        assert _key(symbol="EURUSD") == _key(symbol="eurusd")
        assert _key(symbol="  eurusd  ") == _key(symbol="EURUSD")

    def test_field_boundaries_cannot_be_forged(self) -> None:
        # The encoding is `name=value` joined by `|`. Without it,
        # ("EURUSD", "H1") and ("EURUSDH", "1") would concatenate to the same
        # string and therefore the same key, for two completely different
        # readings.
        assert _key(symbol="EURUSD", timeframe="H1") != _key(symbol="EURUSDH", timeframe="1")

    def test_a_none_bar_time_does_not_collide_with_a_real_one(self) -> None:
        assert _key(bar_time=None) != _key(bar_time=0.0)

    def test_a_missing_setup_id_still_produces_a_key(self) -> None:
        # The upstream sets `subject` to the empty string on an abstention, so this
        # is a real path rather than a hypothetical.
        assert _key(setup_id="") != ""

    def test_float_bar_time_keeps_its_full_precision(self) -> None:
        # `repr` rather than a friendlier format such as `%f` or
        # `str(round(x, 3))`, so two timestamps that differ at a precision a
        # shorter format would flatten still hash differently.
        #
        # The magnitudes here are chosen to be *representable* as distinct
        # doubles. A first attempt used 1727740800.0000001, which Python silently
        # rounds to 1727740800.0 -- the test would have passed for the wrong
        # reason, and would have kept passing if the renderer had been broken.
        assert 1.0000000000000002 != 1.0, "the fixture values must be distinct doubles"
        assert _key(bar_time=1.0000000000000002) != _key(bar_time=1.0)
        # And the real epoch case, at a precision a bar boundary would never
        # reach but a mis-scaled renderer would.
        assert _key(bar_time=1727740800.5) != _key(bar_time=1727740800.0)
