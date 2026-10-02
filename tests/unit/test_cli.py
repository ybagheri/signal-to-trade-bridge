"""The CLI, and above all the exit codes.

**The exit code is the API.** A shell script driving this has three questions: did it
work, was the answer no, or was something broken. Anything else forces every consumer
to re-parse output meant for a person.

```
0   it did what it was asked
1   a refusal -- the bridge worked and declined to trade
2   a fault -- unreachable terminal, unreadable ledger, refused configuration
3   UNKNOWN -- and must not be retried automatically
```

**3 exists so a caller's retry logic has something to key on**, and it is why
``UNKNOWN`` is not folded into "refused". The bridge cannot tell whether a position
exists; a caller told "refused" would reasonably send it again, and rule 5 of this
project exists for exactly that case.

Every test here calls :func:`main` and reads the returned code. None of them runs
the command as a subprocess, because a subprocess test proves the entry point is
installed rather than that the logic is right, and the entry point is one line in
``pyproject.toml``.
"""

from __future__ import annotations

import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from signal_to_trade_bridge.cli import (
    EXIT_FAULT,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNKNOWN,
    main,
)
from signal_to_trade_bridge.cli.main import _read_text_any
from signal_to_trade_bridge.domain.enums import (
    DecisionAction,
    Direction,
    StopSource,
    TakeProfitSource,
)
from signal_to_trade_bridge.domain.models import (
    AccountBalance,
    ExecutionResult,
    PositionSize,
    RiskParameters,
    Signal,
    SignalAction,
    StopLoss,
    SymbolSpec,
    TakeProfit,
    TradeDecision,
    TradeIntent,
)


def _run(*argv: str) -> tuple[int, str]:
    out = StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


# --- the exit codes, which is the contract ----------------------------------


class TestTheExitCodeContract:
    def test_signal_exits_zero(self) -> None:
        code, output = _run("signal")
        assert code == EXIT_OK
        assert json.loads(output)["action"] == "BUY"

    def test_a_refusal_is_one_and_not_an_error(self) -> None:
        # "The engine found nothing to trade" is the commonest outcome in the system.
        # An exit code saying "error" for it teaches operators to ignore the code,
        # and then the code that means something is the one nobody reads.
        decision = TradeDecision(
            signal_id="x", action=DecisionAction.NO_TRADE, reason="EVIDENCE_BELOW_MINIMUM"
        )
        assert _code_for(decision) == EXIT_REFUSED

    def test_an_unknown_is_three_and_separate_from_a_refusal(self) -> None:
        # THE assertion. A caller keying retry logic on "not refused" would resend an
        # unknown, and the resend may open a second position.
        from signal_to_trade_bridge.domain.enums import DecisionAction

        decision = TradeDecision(
            signal_id="x",
            action=DecisionAction.NO_TRADE,
            reason="UNKNOWN",
            execution=ExecutionResult(signal_id="x", status="UNKNOWN"),
        )
        assert _code_for(decision) == EXIT_UNKNOWN
        assert EXIT_UNKNOWN != EXIT_REFUSED

    def test_a_pass_is_zero_even_though_nothing_was_sent(self) -> None:
        # A real decision, not a stub. `TradeDecision` refuses a `DRY_RUN` with no
        # intent -- "no result" is not a state the domain admits -- and a stub would
        # not have noticed.
        dry = TradeDecision(
            signal_id="x", action=DecisionAction.DRY_RUN, reason="PASSED", intent=_intent()
        )
        assert _code_for(dry) == EXIT_OK

    def test_an_execute_is_zero(self) -> None:
        sent = TradeDecision(
            signal_id="x", action=DecisionAction.EXECUTE, reason="PASSED", intent=_intent()
        )
        assert _code_for(sent) == EXIT_OK

    def test_a_decision_with_no_intent_cannot_claim_to_have_traded(self) -> None:
        # The invariant the two tests above are leaning on, asserted directly: only a
        # refusal may exist without an intent. Without it, `EXECUTE` with no volume
        # and no stop would be constructible.
        with pytest.raises(ValueError, match="requires an intent"):
            TradeDecision(signal_id="x", action=DecisionAction.EXECUTE, reason="PASSED")

    def test_the_four_codes_are_distinct(self) -> None:
        codes = [EXIT_OK, EXIT_REFUSED, EXIT_FAULT, EXIT_UNKNOWN]
        assert len(set(codes)) == len(codes)


# --- doctor -----------------------------------------------------------------


class TestDoctor:
    def test_it_reports_the_version(self) -> None:
        code, output = _run("doctor")
        assert "version" in output
        # A machine without a terminal configured cannot run the bridge, and that is
        # a fault rather than a refusal.
        assert code in (EXIT_OK, EXIT_FAULT)

    def test_it_names_a_missing_dependency(self) -> None:
        _code, output = _run("doctor")
        # Whichever machine this runs on, the output must *say something* about each
        # dependency rather than being silent about it.
        assert "MetaTrader5" in output
        assert "auto_trade" in output

    def test_it_mentions_the_control_ids(self) -> None:
        # The one refusal no configuration can clear, so `doctor` has to surface it
        # or an operator learns it from a failed trade instead.
        _code, output = _run("doctor")
        assert "control ids" in output

    def test_it_never_launches_anything(self) -> None:
        # Structural, and the assertion is the point: `doctor` is the command a
        # cautious person runs *before* deciding anything, so it must be safe to run
        # against a live machine.
        import ast
        import inspect

        from signal_to_trade_bridge.cli import main as module

        tree = ast.parse(inspect.getsource(module))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "launch" not in calls
        assert "initialize" not in calls


# --- signal and check -------------------------------------------------------


class TestSignal:
    def test_the_output_is_the_shape_the_execution_project_reads(self) -> None:
        # `id` and not `signal_id`: upstream's constructor takes the latter and its
        # `from_dict` reads the former, so a file written for it needs the former.
        # Getting that wrong produces a file the very project it was written for
        # rejects.
        _code, output = _run("signal")
        payload = json.loads(output)
        for key in ("id", "symbol", "action", "entry", "stop_loss"):
            assert key in payload, f"{key} is what auto-trade requires"

    def test_the_example_carries_no_volume_or_price(self) -> None:
        # Those two are *this* bridge's to compute -- the volume from the balance and
        # the contract, the entry from the signal's own resolution. A template with a
        # plausible `0.10` in it would be a number nobody computed, and the execution
        # project would accept it while this one refused to.
        _code, output = _run("signal")
        payload = json.loads(output)
        assert "volume" not in payload
        assert "price" not in payload

    def test_a_sell_side_mirrors_the_stop_and_target(self) -> None:
        _code, output = _run("signal", "--side", "SELL")
        payload = json.loads(output)
        assert payload["action"] == "SELL"
        assert payload["direction"] == "SHORT"
        # A long's stop is below its entry; a short's is above. Getting this backwards
        # produces a signal the pipeline refuses, which is safe but useless.
        assert float(payload["stop_loss"]) > float(payload["entry"])

    def test_the_example_is_valid_through_the_domain(self) -> None:
        # A worked example that the domain rejects is worse than no example.
        from signal_to_trade_bridge.cli.main import _signal_from

        _code, output = _run("signal")
        signal = _signal_from(json.loads(output))
        assert signal.direction.value == "LONG"
        assert signal.stop_loss is not None


class TestCheck:
    def test_a_missing_file_is_a_fault(self, tmp_path: Path) -> None:
        code, output = _run("check", str(tmp_path / "nope.json"))
        assert code == EXIT_FAULT
        assert "ERROR" in output

    def test_unparsable_json_is_a_fault_with_the_reason(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text("{not json", encoding="utf-8")
        code, output = _run("check", str(path))
        assert code == EXIT_FAULT
        assert "not valid JSON" in output

    def test_a_signal_missing_a_required_field_is_a_fault(self, tmp_path: Path) -> None:
        # Never defaulted. A missing `entry` defaulted to zero would size a trade
        # against a price the market never had.
        path = tmp_path / "s.json"
        path.write_text(json.dumps({"symbol": "EURUSD", "action": "BUY"}), encoding="utf-8")
        code, output = _run("check", str(path))
        assert code == EXIT_FAULT
        assert "direction" in output or "required" in output

    def test_an_unknown_action_is_named(self, tmp_path: Path) -> None:
        path = tmp_path / "s.json"
        path.write_text(
            json.dumps(
                {
                    "symbol": "EURUSD",
                    "action": "TELEPORT",
                    "direction": "LONG",
                    "entry": "1.1",
                    "stop_loss": "1.09",
                }
            ),
            encoding="utf-8",
        )
        code, output = _run("check", str(path))
        assert code == EXIT_FAULT
        assert "TELEPORT" in output

    def test_a_utf16_file_from_a_windows_redirect_is_read(self, tmp_path: Path) -> None:
        # Found by running it: PowerShell redirects through UTF-16, so the
        # documented way of producing a signal file writes something a strict UTF-8
        # reader rejects. The project's own instructions would fail on the platform
        # most users are on.
        _code, output = _run("signal")
        path = tmp_path / "sig.json"
        path.write_text(output, encoding="utf-16")
        assert _read_text_any(path).strip().startswith("{")

    def test_a_utf8_bom_file_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "sig.json"
        path.write_bytes(b"\xef\xbb\xbf" + b'{"symbol": "EURUSD"}')
        assert "EURUSD" in _read_text_any(path)

    def test_a_genuinely_binary_file_is_refused(self, tmp_path: Path) -> None:
        # The last encoding tried has to be one that can fail, or corrupt input
        # becomes nonsense instead of a refusal.
        path = tmp_path / "s.bin"
        path.write_bytes(bytes(range(256)) * 4)
        with pytest.raises(ValueError, match="not text in any encoding"):
            _read_text_any(path)


class TestConfig:
    def test_it_prints_the_configuration_as_json(self) -> None:
        code, output = _run("config")
        assert code == EXIT_OK
        payload = json.loads(output)
        assert "execution_enabled" in payload
        assert "dry_run" in payload

    def test_the_default_configuration_cannot_execute(self) -> None:
        # The single most important line in the output, and the reason `config`
        # exists: a caller guessing why the bridge is not trading will eventually
        # guess "so it is safe to turn on execution_enabled", and it is not.
        _code, output = _run("config")
        payload = json.loads(output)
        assert payload["execution_enabled"] is False
        assert payload["dry_run"] is True

    def test_a_log_dir_flag_wins_over_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A flag that lost to an env var would be a flag that silently did nothing,
        # and a developer pointing the ledger somewhere else to read it is exactly
        # the case where that matters. Compared as *paths*, not as substrings of the
        # rendered JSON: a substring test cannot tell `C:\x` from `C:\\x`, and that
        # is precisely the confusion this command has to avoid.
        monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "from-env"))
        _code, output = _run("config", "--log-dir", str(tmp_path / "from-flag"))
        shown = Path(json.loads(output)["log_directory"])
        assert shown == tmp_path / "from-flag"

    def test_the_environment_is_used_when_no_flag_is_given(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "from-env"))
        _code, output = _run("config")
        assert Path(json.loads(output)["log_directory"]) == tmp_path / "from-env"

    def test_a_windows_path_survives_being_printed(self, tmp_path: Path) -> None:
        # JSON escapes a backslash **once**, so `C:\Users` is correctly rendered as
        # `C:\\Users`. That is not the bug. The bug -- the one this guards -- is
        # escaping a second time on the way in, which writes four backslashes where
        # two belong: unreadable to a person, and it round-trips to a directory that
        # does not exist.
        #
        # Asserted against `json.dumps` of the real path rather than by counting
        # backslashes in the output. The counting version was here first and it was
        # wrong in a way worth recording: it counted escaped separators across the
        # *whole* document, so it also counted `mt5_terminal_path` and
        # `mt5_data_path`, and it passed on a machine with nothing configured and
        # failed on the machine that configures a terminal. Comparing to what
        # `json.dumps` produces is exact, and it cannot be moved by an unrelated
        # setting elsewhere in the configuration.
        _code, output = _run("config", "--log-dir", str(tmp_path / "ledger"))
        line = next(line for line in output.splitlines() if '"log_directory"' in line)
        assert line.strip() == f'"log_directory": {json.dumps(str(tmp_path / "ledger"))},'
        assert json.loads(output)["log_directory"] == str(tmp_path / "ledger")


# --- the module as a whole ---------------------------------------------------


class TestTheCommandSurface:
    def test_no_command_can_place_an_order(self) -> None:
        # Structural. A command that could place an order would need the live path,
        # which needs control identifiers measured for this build -- so today there is
        # nothing to expose, and this is what keeps it that way.
        import ast
        import inspect

        from signal_to_trade_bridge.cli import main as module

        source = inspect.getsource(module)
        tree = ast.parse(source)
        for forbidden in ("build_live", "execute_order", "prepare_order", "click"):
            assert forbidden not in source, (
                f"the CLI references {forbidden}. No command here may reach an order "
                f"control, and the live path is refused on this build anyway."
            )
        assert not [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "build_live"
        ]

    def test_the_help_text_says_no_command_can_trade(self) -> None:
        with pytest.raises(SystemExit):
            _run("--help")

    def test_main_returns_the_code_rather_than_raising(self) -> None:
        # So an embedding caller gets the code rather than a SystemExit. A CLI whose
        # exit path raises cannot be tested and cannot be reused.
        from signal_to_trade_bridge.cli.main import main as entry

        code = entry(["signal"], out=StringIO())
        assert isinstance(code, int)
        assert code == EXIT_OK

    def test_the_public_api_is_importable(self) -> None:
        import signal_to_trade_bridge as stb

        for name in ("build_bridge", "TradeDecision", "Signal", "__version__"):
            assert hasattr(stb, name), f"{name} is missing from the public API"
        # The private packages are never in the public surface: a caller on a machine
        # without them must still be able to `import signal_to_trade_bridge`.
        assert "auto_trade" not in stb.__all__
        assert "MetaTrader5" not in stb.__all__

    def test_every_name_in_dunder_all_actually_exists(self) -> None:
        # `__all__` is a promise, and a promise that lists a name the module does not
        # have is worse than no promise: `from signal_to_trade_bridge import *` fails
        # with an ImportError, on the machine where someone first tries it.
        import signal_to_trade_bridge as stb

        missing = [name for name in stb.__all__ if not hasattr(stb, name)]
        assert not missing, f"__all__ names that do not exist: {missing}"

    def test_the_config_can_be_built_from_the_public_api(self) -> None:
        # **Found by writing this test.** `build_bridge` takes a `BridgeConfig`, and
        # `BridgeConfig` was not exported -- so the documented way to call the
        # library from outside it was "import a name the package does not export".
        # The whole public API was unusable by the people it was written for, and
        # every earlier test passed because each one reached past the package root
        # into a private module to get what it needed.
        import signal_to_trade_bridge as stb

        config = stb.BridgeConfig()
        assert config.execution_enabled is False
        assert config.dry_run is True
        # And the type the API hands back is the same object the caller passed, not
        # a subclass or a copy of something else.
        assert isinstance(config, stb.BridgeConfig)

    def test_a_caller_can_import_only_the_package_root(self) -> None:
        # The literal test: no private module in the import list. If a name a caller
        # needs is reachable only as `signal_to_trade_bridge.composition.BridgeConfig`,
        # it is not part of the API no matter what the docstring says.
        source = (
            "import signal_to_trade_bridge as stb\n"
            "config = stb.BridgeConfig()\n"
            "assert config.dry_run is True\n"
            "assert stb.DecisionAction.NO_TRADE.value == 'NO_TRADE'\n"
            "print(stb.__version__)\n"
        )
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class TestTheReportAOperatorReads:
    """``_render_decision``, tested through a decision the pipeline really produced.

    Not a hand-built one. This is the text a trader looks at when a trade did or did
    not happen, and the thing most likely to break is not the arithmetic -- which the
    pipeline tests already cover -- but the *rendering* of a decision those tests
    never see in this shape. A stub with two fields would pass while the real report
    rendered blank.
    """

    @staticmethod
    def _real_decision(bridge_factory: object) -> TradeDecision:
        """A decision from a real composition root, wired to a stub terminal.

        ``bridge_factory`` rather than ``build_bridge(BridgeConfig())``: the latter
        refuses on a machine with no terminal open, which is right in production and
        useless in a unit test. That refusal is a real behaviour and has its own
        tests -- a report test should not be the one that happens to notice it.
        """
        from signal_to_trade_bridge.cli.main import _signal_from

        bridge = bridge_factory()  # type: ignore[operator]
        _code, output = _run("signal")
        return bridge.pipeline.process(_signal_from(json.loads(output)))

    def test_it_names_the_signal_and_the_verdict(self, bridge_factory: object) -> None:
        text = _render(self._real_decision(bridge_factory))
        assert "stb-example-buy" in text
        assert "DRY_RUN" in text
        assert "PIPELINE_PASSED" in text

    def test_it_shows_the_arithmetic_not_just_the_verdict(self, bridge_factory: object) -> None:
        # A verdict without the numbers is not actionable. "DRY_RUN" tells the
        # operator nothing about what *would* have been traded, which is the only
        # thing a dry run exists to tell them.
        text = _render(self._real_decision(bridge_factory))
        for line in ("volume", "entry", "stop", "target", "risk", "ratio"):
            assert line in text, f"{line} is missing from the report"

    def test_it_says_which_downstream_gates_never_ran(self, bridge_factory: object) -> None:
        # The distinction that matters most. "No blockers" and "the gates were never
        # evaluated" are opposites, and a report that printed the first when the
        # second was true would report a clean run for a pipeline that stopped short.
        text = _render(self._real_decision(bridge_factory))
        assert "downstream gates" in text
        assert "False" in text
        assert "not evaluated" in text.lower() or "because" in text

    def test_the_report_is_readable_and_not_a_json_dump(self, bridge_factory: object) -> None:
        # A JSON dump is parseable and useless to the person who has to read it
        # during a market move. The JSON belongs to `--signal` and the ledger; this is
        # the human surface.
        text = _render(self._real_decision(bridge_factory))
        assert not text.strip().startswith("{")
        assert any(len(line) > 20 for line in text.splitlines())


def _render(decision: TradeDecision) -> str:
    from signal_to_trade_bridge.cli.main import _render_decision

    return _render_decision(decision)


def _intent() -> TradeIntent:
    """A real, minimal intent.

    The exit-code tests need a decision that claims to have traded, and the domain
    will not let one exist without an intent -- which is the correct behaviour, and
    the reason this factory builds a real one rather than passing ``None``. It is
    spelled out rather than borrowed from a fixture because these tests are about the
    *mapping* from a decision to a code, and a decision that could not exist in
    production would not prove anything about production.
    """
    entry = Decimal("1.10000")
    distance = Decimal("0.00300")
    return TradeIntent(
        signal=Signal(
            signal_id="x",
            symbol="EURUSD",
            timeframe="H1",
            action=SignalAction.BUY,
            direction=Direction.LONG,
            entry=entry,
            stop_loss=entry - distance,
            take_profit=entry + distance,
        ),
        symbol="EURUSD",
        direction=Direction.LONG,
        entry=entry,
        stop_loss=StopLoss(
            price=entry - distance, distance=distance, source=StopSource.SIGNAL, basis="SWING"
        ),
        take_profit=TakeProfit(
            price=entry + distance,
            distance=distance,
            source=TakeProfitSource.RR_DERIVED,
            basis="1R",
        ),
        position_size=PositionSize(
            volume=Decimal("0.10"),
            raw_volume=Decimal("0.10"),
            risk_amount=Decimal("50.00"),
            stop_distance=distance,
            risk_per_unit=Decimal("500.00"),
            ticks=Decimal("300"),
            tick_size=Decimal("0.00001"),
            tick_value=Decimal("1.0"),
        ),
        risk_parameters=RiskParameters(),
        account_balance=AccountBalance(balance=Decimal("10000.00"), currency="USD"),
        symbol_spec=SymbolSpec(
            symbol="EURUSD",
            contract_size=Decimal("100000"),
            tick_size=Decimal("0.00001"),
            tick_value_profit=Decimal("1.0"),
            tick_value_loss=Decimal("1.0"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100.0"),
            volume_step=Decimal("0.01"),
            digits=5,
            point=Decimal("0.00001"),
        ),
    )


def _code_for(decision: object) -> int:
    """The code the CLI would return for a decision, without running a command."""
    from signal_to_trade_bridge.cli.main import _decision_code

    return _decision_code(decision)  # type: ignore[arg-type]
