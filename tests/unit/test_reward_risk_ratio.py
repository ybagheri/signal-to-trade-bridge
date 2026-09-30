"""The reward:risk policy: the floor, and the achieved ratio that is reported.

Phase 5's subject. The resolver itself was built in Phase 3, so the tests here are
about two things that were true of it but not *of* it:

1. **The configured ratio was never a minimum.** ``RR_FALLBACK`` has documented
   itself as "I want 1:1 as the floor", and nothing compared the signal's implied
   ratio against anything -- so a structurally sound target at 0.2:1 was accepted
   silently under the default policy. Phase 5 added an **opt-in** floor rather
   than changing what the default policy means, and these tests pin both halves:
   that it is off by default, and that when it is on it does what it says.
2. **The achieved ratio was reported on some paths and absent on others.**
   ``achieved_ratio`` existed on four of five success paths and was *missing
   entirely* under ``NONE``, so the first consumer to index it would have raised
   ``KeyError`` on a successful resolution. These tests pin that it is now present
   on every path and ``None`` only where there is genuinely no target.

There is also an AST test at the bottom, because the ratio used to be computed in
five places and a fifth copy is exactly how two of them drift apart.
"""

from __future__ import annotations

import ast
import inspect
import os
from decimal import Decimal

import pytest

from signal_to_trade_bridge.configuration.config import BRIDGE_ENV_PREFIX, config_from_env
from signal_to_trade_bridge.domain.enums import (
    Direction,
    RejectionReason,
    SignalAction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.errors import ConfigurationError
from signal_to_trade_bridge.domain.models import (
    RiskParameters,
    Signal,
    StopLoss,
    TakeProfit,
    achieved_ratio,
)
from signal_to_trade_bridge.domain.take_profit import (
    resolve_take_profit,
    signal_target_ratio,
)

#: The brief's worked example: entry 100, stop 99, one unit of risk. A target at
#: 100.2 is therefore 0.2:1 -- the case the default policy used to accept
#: silently.
ENTRY = Decimal("100")
STOP_DISTANCE = Decimal("1")


def _stop(direction: Direction = Direction.LONG) -> StopLoss:
    price = ENTRY - STOP_DISTANCE if direction is Direction.LONG else ENTRY + STOP_DISTANCE
    return StopLoss(price=price, distance=STOP_DISTANCE, source=StopSource.SIGNAL)


def _signal(
    *,
    direction: Direction = Direction.LONG,
    target: str | None = "101",
    basis: str = "MEASURED_MOVE",
) -> Signal:
    action = SignalAction.BUY if direction is Direction.LONG else SignalAction.SELL
    return Signal(
        signal_id="stb-test-rr",
        symbol="EURUSD",
        timeframe="H1",
        action=action,
        direction=direction,
        entry=ENTRY,
        stop_loss=_stop(direction).price,
        stop_basis="PULLBACK_EXTREME",
        take_profit=Decimal(target) if target is not None else None,
        take_profit_basis=basis,
    )


def _risk(
    source: TakeProfitSource = TakeProfitSource.RR_FALLBACK,
    **overrides: object,
) -> RiskParameters:
    defaults: dict[str, object] = {
        "risk_percent": Decimal("0.5"),
        "reward_risk_ratio": Decimal("1.0"),
        "take_profit_source": source,
    }
    defaults.update(overrides)
    return RiskParameters(**defaults)  # type: ignore[arg-type]


class TestTheFloorIsOffByDefault:
    def test_no_floor_is_configured_out_of_the_box(self) -> None:
        # The reason Phase 5 added the floor instead of wiring
        # `reward_risk_ratio` into the usability test. Turning the floor on starts
        # replacing engine-measured targets with the configured distance, which is
        # a trading decision, and no bridge should make it silently on upgrade.
        assert _risk().minimum_reward_risk_ratio is None

    def test_a_signal_target_well_below_one_to_one_is_still_accepted(self) -> None:
        # Stated as a test on purpose: this is the behaviour that is being
        # preserved, and it should be a decision somebody made rather than an
        # accident nobody noticed.
        resolution = resolve_take_profit(_signal(target="100.2"), _stop(), _risk())
        assert resolution.ok
        assert resolution.unwrap().price == Decimal("100.2")
        assert resolution.details["achieved_ratio"] == "0.2"


class TestTheFloorWhenEnabled:
    def _floor(self, floor: str = "1.0", **overrides: object) -> RiskParameters:
        return _risk(minimum_reward_risk_ratio=Decimal(floor), **overrides)

    def test_a_target_below_the_floor_is_not_used_and_the_ratio_is(self) -> None:
        resolution = resolve_take_profit(_signal(target="100.2"), _stop(), self._floor())
        assert resolution.ok
        assert resolution.unwrap().price == Decimal("101")
        assert resolution.unwrap().source is TakeProfitSource.RR_FALLBACK

    def test_the_fallback_records_why_and_the_ratio_it_found(self) -> None:
        # Never silent. A floor that quietly swapped the engine's target for a
        # computed one would be the worst version of this feature: the trade
        # would look identical in the log while its exit plan was not the one the
        # engine's analysis chose.
        details = resolve_take_profit(_signal(target="100.2"), _stop(), self._floor()).details
        assert "0.2" in str(details["fallback_because"])
        assert details["signal_target_ratio"] == "0.2"
        assert details["minimum_reward_risk_ratio"] == "1.0"

    def test_a_target_exactly_at_the_floor_is_kept(self) -> None:
        # The boundary. An off-by-one here either refuses the engine's own 1:1
        # target -- which the brief's whole example is built on -- or lets a
        # 0.99:1 through on the theory that it is close enough.
        resolution = resolve_take_profit(_signal(target="101"), _stop(), self._floor())
        assert resolution.unwrap().source is TakeProfitSource.SIGNAL

    def test_a_target_above_the_floor_is_kept(self) -> None:
        resolution = resolve_take_profit(_signal(target="103"), _stop(), self._floor())
        assert resolution.unwrap().source is TakeProfitSource.SIGNAL
        assert resolution.details["achieved_ratio"] == "3"

    def test_a_floor_below_the_configured_ratio_is_honoured(self) -> None:
        # A trader who is content with 0.5:1 as a floor and configures 1:1 targets
        # wants the engine's 0.6:1 kept, and gets it.
        resolution = resolve_take_profit(_signal(target="100.6"), _stop(), self._floor(floor="0.5"))
        assert resolution.unwrap().source is TakeProfitSource.SIGNAL

    def test_under_the_strict_policy_a_below_floor_target_refuses_the_trade(self) -> None:
        # Under `SIGNAL` there is no fallback to apply, so a target the trader has
        # declared unacceptable has to refuse rather than be silently replaced.
        # This is the asymmetry between the two policies and it is deliberate.
        resolution = resolve_take_profit(
            _signal(target="100.2"),
            _stop(),
            self._floor(source=TakeProfitSource.SIGNAL),
        )
        assert resolution.ok is False
        assert resolution.reason is RejectionReason.INVALID_TAKE_PROFIT
        assert "0.2" in str(resolution.details["unusable_because"])

    def test_under_rr_derived_the_floor_has_nothing_to_filter(self) -> None:
        # `RR_DERIVED` ignores signal targets entirely, which is the entire point
        # of the policy, so there is no candidate for the floor to reject. Pinned
        # so the interaction is deliberate rather than accidental: a reader who
        # assumed the floor applied everywhere should be corrected here.
        resolution = resolve_take_profit(
            _signal(target="100.2"),
            _stop(),
            self._floor(source=TakeProfitSource.RR_DERIVED),
        )
        assert resolution.unwrap().price == Decimal("101")
        assert resolution.details["achieved_ratio"] == "1"

    def test_the_floor_is_checked_after_the_structural_judgements(self) -> None:
        # A target that is wrong on two grounds should report the one with the
        # upstream remedy. The basis is the engine's own defect; the ratio is this
        # bridge's opinion about it.
        resolution = resolve_take_profit(
            _signal(target="100.2", basis="ATR_FALLBACK"),
            _stop(),
            self._floor(),
        )
        assert "volatility multiple" in str(resolution.details["fallback_because"])

    def test_the_floor_does_not_apply_to_an_absent_target(self) -> None:
        # Nothing to judge. The refusal is about the missing target, not about a
        # ratio, and reporting a ratio of zero would be a different claim.
        details = resolve_take_profit(_signal(target=None), _stop(), self._floor()).details
        assert "no take profit" in str(details["fallback_because"])


class TestFloorValidation:
    @pytest.mark.parametrize("bad", ["0", "0.0", "-1"])
    def test_a_floor_of_zero_or_less_is_refused(self, bad: str) -> None:
        # Zero would refuse every target, which is what *omitting* the setting
        # means to say. A configuration file that was meant to set a floor and set
        # it to zero should say so loudly rather than trade nothing for a day.
        with pytest.raises(ValueError, match="minimum_reward_risk_ratio"):
            _risk(minimum_reward_risk_ratio=Decimal(bad))

    def test_the_error_says_how_to_disable_the_check_instead(self) -> None:
        with pytest.raises(ValueError, match="omit it entirely"):
            _risk(minimum_reward_risk_ratio=Decimal("0"))

    def test_the_floor_appears_in_the_serialised_parameters(self) -> None:
        assert (
            _risk(minimum_reward_risk_ratio=Decimal("2")).to_dict()["minimum_reward_risk_ratio"]
            == "2"
        )

    def test_no_floor_serialises_as_none_not_as_an_empty_string(self) -> None:
        # So a decision record can tell "not set" from "set to nothing".
        assert _risk().to_dict()["minimum_reward_risk_ratio"] is None


class TestTheAchievedRatioIsAlwaysReported:
    def test_signal_policy_reports_the_signals_own_ratio(self) -> None:
        resolution = resolve_take_profit(
            _signal(target="102.5"), _stop(), _risk(TakeProfitSource.SIGNAL)
        )
        assert resolution.details["achieved_ratio"] == "2.5"

    def test_rr_derived_reports_the_configured_ratio(self) -> None:
        resolution = resolve_take_profit(
            _signal(target="101"), _stop(), _risk(TakeProfitSource.RR_DERIVED)
        )
        assert resolution.details["achieved_ratio"] == "1"

    def test_rr_derived_reports_a_configured_ratio_other_than_one(self) -> None:
        resolution = resolve_take_profit(
            _signal(target="101"),
            _stop(),
            _risk(TakeProfitSource.RR_DERIVED, reward_risk_ratio=Decimal("2.5")),
        )
        assert resolution.details["achieved_ratio"] == "2.5"

    def test_the_fallback_branch_reports_the_ratio_it_applied(self) -> None:
        resolution = resolve_take_profit(
            _signal(target=None), _stop(), _risk(reward_risk_ratio=Decimal("3"))
        )
        assert resolution.details["achieved_ratio"] == "3"

    def test_the_disabled_policy_reports_none_rather_than_raising_a_key_error(self) -> None:
        # The bug this pins: `achieved_ratio` did not exist on this path at all, so
        # `details["achieved_ratio"]` raised `KeyError` on a resolution that had
        # already succeeded. A missing key is the worst failure mode here, because
        # the code around it has decided the trade is fine.
        resolution = resolve_take_profit(_signal(), _stop(), _risk(TakeProfitSource.NONE))
        assert resolution.ok
        assert resolution.details["achieved_ratio"] is None
        assert "achieved_ratio" in resolution.details

    def test_every_successful_path_reports_the_key(self) -> None:
        # The blanket version of the above: whatever the policy and whatever the
        # signal, a resolution that succeeded carries the key. Cheap, and it is
        # the property a log parser will come to depend on.
        cases = [
            _signal(target="101"),
            _signal(target="100.2"),
            _signal(target=None),
            _signal(target="99"),
            _signal(basis="ATR_FALLBACK"),
        ]
        for policy in TakeProfitSource:
            for signal in cases:
                resolution = resolve_take_profit(signal, _stop(), _risk(policy))
                if resolution.ok:
                    assert "achieved_ratio" in resolution.details, (policy, signal.take_profit)

    def test_the_reported_ratio_is_the_distances_actually_used(self) -> None:
        # Not the configured number and not the signal's own claim: the ratio the
        # trade has. A target at 102.5 against a stop distance of 1 is 2.5:1 even
        # though the configuration says 1:1, and reporting the configuration would
        # be reporting an intention as a fact.
        resolution = resolve_take_profit(_signal(target="102.5"), _stop(), _risk())
        assert resolution.details["achieved_ratio"] == "2.5"
        assert resolution.details["reward_risk_ratio"] == "1.0"


class TestTheSharedRatioHelper:
    def test_it_computes_reward_distance_over_risk_distance(self) -> None:
        take_profit = TakeProfit(price=ENTRY + Decimal("2.5"), distance=Decimal("2.5"))
        assert achieved_ratio(_stop(), take_profit) == Decimal("2.5")

    def test_it_is_none_when_there_is_no_target(self) -> None:
        assert achieved_ratio(_stop(), None) is None

    def test_it_is_none_for_a_zero_risk_distance(self) -> None:
        # `StopLoss` refuses this at construction, so the only way to reach it is
        # an object that skipped validation -- and the answer must be `None` rather
        # than a `ZeroDivisionError` escaping into a trading loop.
        broken = StopLoss(price=Decimal("99"), distance=Decimal("1"))
        object.__setattr__(broken, "distance", Decimal("0"))
        take_profit = TakeProfit(price=Decimal("101"), distance=Decimal("1"))
        assert achieved_ratio(broken, take_profit) is None

    def test_the_signal_helper_reads_a_target_the_resolver_has_not_accepted(self) -> None:
        assert signal_target_ratio(_stop(), _signal(target="100.2")) == Decimal("0.2")

    def test_the_signal_helper_is_none_without_a_target(self) -> None:
        # Distinct from a target at 0:1. "No target to judge" and "a target that
        # scores badly" are different inputs and a floor has to tell them apart.
        assert signal_target_ratio(_stop(), _signal(target=None)) is None

    def test_the_signal_helper_measures_from_the_entry_not_the_stop_price(self) -> None:
        # A stop price of 99 with an entry of 100 is a risk distance of 1, and a
        # target of 100.2 is 0.2 away. Measuring from the wrong reference would
        # make every ratio wrong by exactly the risk distance.
        assert signal_target_ratio(_stop(), _signal(target="100.2")) == Decimal("0.2")

    def test_the_signal_helper_is_none_without_an_entry(self) -> None:
        # An abstention can carry a target it never acted on. There is nothing to
        # measure it against, and guessing a reference would produce a ratio for a
        # trade that does not exist.
        abstention = Signal(
            signal_id="stb-test-rr",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.WAIT,
            direction=Direction.FLAT,
            take_profit=Decimal("101"),
            take_profit_basis="MEASURED_MOVE",
        )
        assert signal_target_ratio(_stop(), abstention) is None

    def test_the_signal_helper_is_none_for_a_zero_risk_distance(self) -> None:
        broken = _stop()
        object.__setattr__(broken, "distance", Decimal("0"))
        assert signal_target_ratio(broken, _signal(target="100.2")) is None


class TestTheResolverDelegatesItsArithmetic:
    def test_resolve_take_profit_never_divides(self) -> None:
        """One implementation of the ratio, not five.

        The resolver used to compute ``distance / stop.distance`` inline on four
        separate paths, while ``TradeIntent.reward_to_risk`` computed the same
        quantity a fifth way. Five copies is five places for one to be edited and
        the rest to be quietly wrong -- and a policy that checks one ratio while a
        log reports another is exactly the disagreement this project refuses to
        have between two of its own layers.

        So the resolver delegates to three named helpers, each with one meaning:
        ``signal_target_ratio`` for a target it has not yet accepted,
        :func:`achieved_ratio` for one it has, and ``target_from_ratio`` for
        deriving a price from a configured ratio. Asserted structurally, because
        the failure mode -- one extra inline division that nobody notices -- is
        invisible in review.
        """
        source = inspect.getsource(resolve_take_profit)
        tree = ast.parse(source.lstrip())
        divisions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
        ]
        assert not divisions, (
            f"resolve_take_profit performs its own division at line "
            f"{divisions[0].lineno}. Compute the ratio with signal_target_ratio(), "
            f"achieved_ratio() or target_from_ratio() so there is one implementation."
        )

    def test_the_three_helpers_are_all_reachable_from_the_resolver(self) -> None:
        # The companion to the test above: forbidding the division is only a
        # policy if the named alternatives are actually used, otherwise the rule
        # just makes the function refuse to do its job.
        source = inspect.getsource(resolve_take_profit)
        for helper in ("signal_target_ratio", "target_from_ratio", "_ratio"):
            assert helper in source, f"{helper} is not used by resolve_take_profit"


class TestConfigurationWiring:
    def test_the_floor_is_read_from_the_environment(self, clean_environment: None) -> None:
        os.environ[f"{BRIDGE_ENV_PREFIX}MINIMUM_REWARD_RISK_RATIO"] = "1.5"
        config = config_from_env(apply=False)
        assert config.risk.minimum_reward_risk_ratio == Decimal("1.5")

    def test_no_floor_by_default(self, clean_environment: None) -> None:
        assert config_from_env(apply=False).risk.minimum_reward_risk_ratio is None

    def test_an_unparsable_floor_refuses_to_start(self, clean_environment: None) -> None:
        os.environ[f"{BRIDGE_ENV_PREFIX}MINIMUM_REWARD_RISK_RATIO"] = "half"
        with pytest.raises(ConfigurationError):
            config_from_env(apply=False)

    def test_a_configured_zero_floor_refuses_to_start(self, clean_environment: None) -> None:
        # Not silently ignored as "unset". An operator who typed zero has said
        # something, and the answer to it is an error rather than a day of
        # mysterious refusals.
        os.environ[f"{BRIDGE_ENV_PREFIX}MINIMUM_REWARD_RISK_RATIO"] = "0"
        with pytest.raises(ConfigurationError, match="minimum_reward_risk_ratio"):
            config_from_env(apply=False)

    def test_a_configured_zero_reward_ratio_refuses_to_start(self, clean_environment: None) -> None:
        """`Decimal("0")` is falsy, so `or default` used to swallow this.

        The bridge would have come up at 1:1 after the operator had asked for no
        ratio at all. Phase 1 fixed exactly this idiom for `BAR_COUNT` and left it
        in the two fields where it risks money.
        """
        os.environ[f"{BRIDGE_ENV_PREFIX}REWARD_RISK_RATIO"] = "0"
        with pytest.raises(ConfigurationError, match="reward_risk_ratio"):
            config_from_env(apply=False)

    def test_a_configured_zero_risk_percent_refuses_to_start(self, clean_environment: None) -> None:
        # The worse of the two: a silently-defaulted risk percentage leaves the
        # bridge risking 0.5% per trade after somebody asked for none.
        os.environ[f"{BRIDGE_ENV_PREFIX}RISK_PERCENT"] = "0"
        with pytest.raises(ConfigurationError, match="risk_percent"):
            config_from_env(apply=False)

    @pytest.mark.parametrize(
        "name", ["RISK_PERCENT", "REWARD_RISK_RATIO", "MINIMUM_REWARD_RISK_RATIO"]
    )
    def test_a_blank_value_still_means_unset(self, clean_environment: None, name: str) -> None:
        # `KEY=` in a `.env` file has always meant "not set" here, and changing
        # that would be a separate decision. The distinction that matters is
        # between "the operator said nothing" and "the operator said zero".
        os.environ[f"{BRIDGE_ENV_PREFIX}{name}"] = "   "
        config_from_env(apply=False)

    def test_the_floor_reaches_the_policy_it_gates(self, clean_environment: None) -> None:
        # The one test that crosses the configuration boundary: env → config →
        # model → policy. Nothing else in the suite does, and without it the
        # wiring between the setting and the behaviour that claims to honour it
        # could break with every test still green.
        os.environ[f"{BRIDGE_ENV_PREFIX}MINIMUM_REWARD_RISK_RATIO"] = "1.0"
        risk = config_from_env(apply=False).risk
        resolution = resolve_take_profit(_signal(target="100.2"), _stop(), risk)
        assert resolution.unwrap().price == Decimal("101")
        assert resolution.details["minimum_reward_risk_ratio"] == "1.0"
