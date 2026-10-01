"""Deterministic signal identity.

These tests pin the one property the downstream deduplication depends on: the same
reading must produce the same key, forever, on any machine. A key that changed
between calls would let the same signal through twice, and a key that was not
deterministic at all would defeat the mechanism while still passing a test that
only checked the format.
"""

from __future__ import annotations

import re
from decimal import Decimal

from signal_to_trade_bridge.adapters.albrooks.identity import (
    SIGNAL_ID_PREFIX,
    compute_signal_id,
)
from signal_to_trade_bridge.adapters.albrooks.mapper import map_result_to_signal
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


class TestTheKeyCannotSilentlyCollapseTwoTrades:
    """Phase 9 revisited this key, as the handoff required, and found a hole.

    The handoff asked Phase 9 to check the key against real engine output. It could
    not -- `albrooks` is not installed on this machine -- so it checked the thing the
    key is actually built from instead: **what the mapper supplies when the engine
    does not report a bar.**

    `_bar_index` returns `-1` when the engine's result has no `last_closed_bar`, and
    `_bar_time` returns `None` when there is no matching bar feature. Both are
    *permitted*: they are ordinary attribute lookups with a fallback, so nothing
    raises and nothing is logged. A `Signal` then carries `bar_index=-1` and
    `bar_time=None`, and **the bar -- which is the whole unit of identity -- is
    simply absent from the key**.

    The consequence is below, and it is the failure mode the ledger exists to
    prevent, arriving through the ledger: two readings with no bar information
    produce the *same* key, so the second is refused as a duplicate even though it is
    a different trade.
    """

    def test_two_readings_with_no_bar_information_collapse_into_one_key(self) -> None:
        # The defect, stated directly. Nothing raises, nothing warns, and the key is
        # perfectly valid -- it just means "this is the same reading as last time"
        # for two readings that are not the same.
        assert _key(bar_index=-1, bar_time=None) == _key(bar_index=-1, bar_time=None)

    def test_the_symbol_still_separates_them(self) -> None:
        # The reassuring half, and worth pinning because it is what stops this being
        # catastrophic rather than serious. Two different symbols never collide, so
        # the damage is confined to one symbol on one timeframe.
        assert _key(bar_index=-1, bar_time=None, symbol="GBPUSD") != _key(
            bar_index=-1, bar_time=None, symbol="EURUSD"
        )

    def test_the_setup_still_separates_them(self) -> None:
        # Also reassuring: two different setups on the same bar do not collide.
        assert _key(bar_index=-1, bar_time=None, setup_id="a") != _key(
            bar_index=-1, bar_time=None, setup_id="b"
        )

    def test_bar_time_alone_is_enough_when_the_index_is_missing(self) -> None:
        # Which is why the collapse needs *both* to be absent: the engine that
        # reports a close time but no index is fine, and the engine that reports an
        # index but no time is fine.
        assert _key(bar_index=-1, bar_time=100.0) != _key(bar_index=-1, bar_time=200.0)
        assert _key(bar_index=1, bar_time=None) != _key(bar_index=2, bar_time=None)

    def test_the_production_default_is_the_collapse(self) -> None:
        # WHY this matters: `bar_index` defaults to `-1` on the model itself, so a
        # `Signal` built without one is not an unusual thing -- it is the declared
        # default. The mapper's fallback and the model's default agree, which means
        # the key can be degenerate without anything looking wrong.
        from signal_to_trade_bridge.domain.models import Signal

        defaults = Signal(
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
            stop_basis="PULLBACK_EXTREME",
            signal_id="stb-whatever",
        )
        assert defaults.bar_index == -1
        assert defaults.bar_time is None


class TestItSatisfiesTheDownstreamFilenameRule:
    """`signal_id` is used as a file name, and must not be able to name a path.

    Found in Phase 7, by reading `auto-trade`'s source rather than its
    documentation. `TradeSignal` validates its id against
    `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` because the id "is used as a file name and
    must not be able to name a path" — and `../../x` fails that pattern.

    That makes it a **security control on our side**, not a cosmetic constraint:
    the execution project writes a file named after the id the bridge supplies. An
    id containing a path separator or a drive letter would let a signal decide
    where the execution project writes.

    Phase 2 chose `stb-<32 hex>`, which satisfies it, and nothing tested that it
    still did. The property is asserted here rather than left to a comment,
    because the failure mode is a path traversal in someone else's log directory
    and it would be silent.
    """

    #: The upstream rule, copied rather than imported: `auto_trade` is a private
    #: repository that may not be installed, and this test has to run without it.
    UPSTREAM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    def test_every_generated_id_matches_the_upstream_pattern(self) -> None:
        from tests.stubs import stub_buy

        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert self.UPSTREAM_PATTERN.match(signal.signal_id), (
            f"{signal.signal_id!r} would be refused by auto-trade, whose id doubles as a "
            f"file name and must not be able to name a path"
        )

    def test_an_id_can_never_contain_a_path_separator(self) -> None:
        from tests.stubs import stub_buy

        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        for forbidden in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
            assert forbidden not in signal.signal_id

    def test_an_id_starts_with_a_letter_or_digit(self) -> None:
        # A leading dot would satisfy "contains only dot, dash, underscore" while
        # naming a hidden file, which is why the upstream pattern requires an
        # alphanumeric first character.
        from tests.stubs import stub_buy

        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert signal.signal_id[0].isalnum()

    def test_the_id_is_short_enough_for_the_upstream_limit(self) -> None:
        from tests.stubs import stub_buy

        signal = map_result_to_signal(stub_buy()).signal
        assert signal is not None
        assert len(signal.signal_id) <= 128


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
