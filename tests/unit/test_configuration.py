"""Configuration loading.

The behaviour under test is mostly about what happens when something is wrong.
Configuration for a trading system has one overriding requirement: a mistake must
produce a refusal to start, not a silent fallback to a default. A risk
percentage that quietly reverted to 0.5 because the configured value had a typo
would leave a system trading at a level nobody chose.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from signal_to_trade_bridge.configuration.config import (
    BRIDGE_ENV_PREFIX,
    BridgeConfig,
    config_from_env,
    load_dotenv,
)
from signal_to_trade_bridge.domain.enums import TakeProfitSource
from signal_to_trade_bridge.domain.errors import ConfigurationError


class TestDefaults:
    """What the *code* defaults to, with nothing around it.

    Every test here needs the working directory moved as well as the environment
    cleared, because `config_from_env()` finds `.env` by path. Clearing
    `os.environ` alone is not enough, and the difference showed up on the day this
    machine armed itself: these tests started failing not because the defaults
    changed but because a file in the repository root said otherwise. An assertion
    about the shipped default that fails when a developer configures their machine
    is testing the developer, not the default.
    """

    def test_a_fresh_checkout_cannot_execute(
        self, clean_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The single most important default in the project. A misconfigured or
        # absent environment must produce a bridge that cannot trade.
        monkeypatch.chdir(tmp_path)
        config = config_from_env(apply=False)
        assert config.execution_enabled is False
        assert config.dry_run is True
        assert not config.can_execute

    def test_risk_defaults_to_half_a_percent_at_one_to_one(
        self, clean_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config = config_from_env(apply=False)
        assert config.risk_percent == Decimal("0.5")
        assert config.reward_risk_ratio == Decimal("1.0")

    def test_no_machine_specific_path_is_baked_in(self, clean_environment: None) -> None:
        # The current development machine happens to use E:\, and the execution
        # project defaults to another person's C:\Users\BazikadeStore. A default
        # that names any of those is a default that is wrong on every other
        # laptop.
        #
        # The two variables are cleared **here**, and not left to `clean_environment`,
        # because that fixture snapshots and restores but never clears -- a deliberate
        # choice so it cannot delete a developer's real settings mid-session. The
        # consequence is that a test asserting something about the *defaults* runs
        # against the ambient environment. This one passed on a machine with nothing
        # exported and failed on the machine that actually configures a terminal,
        # which is the worst possible pairing: the test for "no path is hard-coded"
        # broke on the one machine where a path legitimately is.
        import os

        for name in ("MT5_TERMINAL_PATH", "MT5_DATA_PATH"):
            os.environ.pop(f"{BRIDGE_ENV_PREFIX}{name}", None)

        config = config_from_env(apply=False)
        assert config.mt5_terminal_path is None
        assert config.mt5_data_path is None
        assert config.log_directory == Path("logs")


class TestOverrides:
    def test_risk_percent_is_configurable(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}RISK_PERCENT"] = "0.25"
        assert config_from_env(apply=False).risk_percent == Decimal("0.25")

    def test_reward_ratio_is_configurable(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}REWARD_RISK_RATIO"] = "2.5"
        assert config_from_env(apply=False).reward_risk_ratio == Decimal("2.5")

    def test_take_profit_source_is_parsed(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}TAKE_PROFIT_SOURCE"] = "SIGNAL"
        assert config_from_env(apply=False).take_profit_source is TakeProfitSource.SIGNAL

    def test_direction_restrictions_are_configurable(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}ALLOW_SELL"] = "false"
        config = config_from_env(apply=False)
        assert config.risk.allow_buy is True
        assert config.risk.allow_sell is False

    def test_symbol_allowlist_is_upper_cased_and_split(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}ALLOWED_SYMBOLS"] = "eurusd, XAUUSD ,"
        assert config_from_env(apply=False).risk.allowed_symbols == frozenset({"EURUSD", "XAUUSD"})

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_flag_values(self, clean_environment: None, raw: str) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}DRY_RUN"] = raw
        assert config_from_env(apply=False).dry_run is True

    @pytest.mark.parametrize("raw", ["0", "false", "NO", "off"])
    def test_falsy_flag_values(self, clean_environment: None, raw: str) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}DRY_RUN"] = raw
        assert config_from_env(apply=False).dry_run is False


class TestFailLoudly:
    def test_a_non_numeric_risk_percent_raises(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}RISK_PERCENT"] = "half"
        with pytest.raises(ConfigurationError, match="is not a number"):
            config_from_env(apply=False)

    def test_a_non_boolean_flag_raises(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}DRY_RUN"] = "maybe"
        with pytest.raises(ConfigurationError, match="is not a boolean"):
            config_from_env(apply=False)

    def test_an_unknown_take_profit_source_lists_the_valid_ones(
        self, clean_environment: None
    ) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}TAKE_PROFIT_SOURCE"] = "MADE_UP"
        with pytest.raises(ConfigurationError, match="is not one of"):
            config_from_env(apply=False)

    def test_a_negative_risk_percent_raises_a_configuration_error(
        self, clean_environment: None
    ) -> None:
        # One exception type for every configuration problem, rather than two
        # depending on which layer happened to notice.
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}RISK_PERCENT"] = "-1"
        with pytest.raises(ConfigurationError, match="risk_percent must be positive"):
            config_from_env(apply=False)

    def test_an_unknown_log_level_raises(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}LOG_LEVEL"] = "CHATTY"
        with pytest.raises(ConfigurationError, match="is not one of"):
            config_from_env(apply=False)

    def test_a_non_positive_bar_count_raises(self, clean_environment: None) -> None:
        import os

        os.environ[f"{BRIDGE_ENV_PREFIX}BAR_COUNT"] = "0"
        with pytest.raises(ConfigurationError, match="bar_count must be positive"):
            config_from_env(apply=False)


class TestCanExecute:
    def test_dry_run_overrides_the_enable_flag(self) -> None:
        # Ordering, not luck: a configuration with execution enabled and dry run
        # on is a dry run, and that has to be true regardless of which value a
        # caller changed last.
        config = BridgeConfig(execution_enabled=True, dry_run=True)
        assert not config.can_execute

    def test_execution_needs_both_switches(self) -> None:
        assert BridgeConfig(execution_enabled=True, dry_run=False).can_execute
        assert not BridgeConfig(execution_enabled=False, dry_run=False).can_execute


class TestSecrets:
    def test_repr_does_not_dump_every_field(self) -> None:
        # The default dataclass repr would print every field, and this is the
        # object where a broker credential would live if anyone put one here. A
        # stray print(config) in a diagnostic is exactly the kind of thing that
        # ends up pasted into a bug report.
        text = repr(BridgeConfig(mt5_terminal_path=Path("C:/secret/terminal64.exe")))
        assert "mt5_terminal_path=set" in text
        assert "C:/secret" not in text
        assert "terminal64.exe" not in text
        # The trading-relevant settings are still there, because a repr that hid
        # everything would satisfy this test while making debugging impossible.
        assert "risk_percent=0.5" in text
        assert "execution_enabled=False" in text


class TestDotenv:
    def test_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        # Absent configuration must produce safe defaults, not a crash. The two
        # requirements are different: a missing file is normal, a malformed value
        # is not.
        assert load_dotenv(tmp_path / "absent.env") == {}

    def test_parses_assignments_comments_and_quotes(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "# a comment",
                    "",
                    "PLAIN=value",
                    'QUOTED="with spaces"',
                    "SINGLE='literal'",
                    "export EXPORTED=exported_value",
                    "  SPACED  =  trimmed  ",
                ]
            ),
            encoding="utf-8",
        )
        assert load_dotenv(env_file) == {
            "PLAIN": "value",
            "QUOTED": "with spaces",
            "SINGLE": "literal",
            "EXPORTED": "exported_value",
            "SPACED": "trimmed",
        }

    def test_a_malformed_line_raises_rather_than_being_skipped(self, tmp_path: Path) -> None:
        # A typo in a risk percentage that is silently ignored is worse than a
        # startup failure, because the process would then run with a default
        # nobody chose.
        env_file = tmp_path / ".env"
        env_file.write_text("BRIDGE_RISK_PERCENT=0.5\nthis line is broken\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="not a KEY=VALUE assignment"):
            load_dotenv(env_file)

    def test_the_error_names_the_line_number(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("A=1\nB=2\nbroken\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match=r"\.env:3"):
            load_dotenv(env_file)

    def test_an_empty_key_raises(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("=orphan\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="empty key"):
            load_dotenv(env_file)

    def test_a_real_environment_variable_beats_the_file(
        self, tmp_path: Path, clean_environment: None
    ) -> None:
        # Someone who exported a variable in their shell meant it, and a file on
        # disk silently beating that would make the shell unusable for overrides.
        import os

        env_file = tmp_path / ".env"
        env_file.write_text("BRIDGE_RISK_PERCENT=0.25\n", encoding="utf-8")
        os.environ["BRIDGE_RISK_PERCENT"] = "1.0"
        assert config_from_env(env_file=env_file).risk_percent == Decimal("1.0")

    def test_the_file_is_used_when_nothing_else_is_set(
        self, tmp_path: Path, clean_environment: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Run from a directory with no `.env` of its own, so the value below can only
        # have come from the file that was passed in. Without this it also read the
        # repository's, and a machine that had armed itself made this test fail for
        # a reason that had nothing to do with what it is checking.
        monkeypatch.chdir(tmp_path)
        env_file = tmp_path / "elsewhere.env"
        env_file.write_text("BRIDGE_RISK_PERCENT=0.75\n", encoding="utf-8")
        assert config_from_env(env_file=env_file).risk_percent == Decimal("0.75")
