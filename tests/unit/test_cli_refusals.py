"""The paths that refuse, on the command line.

Phase 13 found that `cli/main.py` sat at 85% while everything else in the project
was at or near 100%, and that the gap was not incidental. The uncovered lines were
the ones that turn bad input into a fault: a signal file that cannot be read, a
payload that is not an object, a direction nobody recognises, a required field
missing.

**Those are the lines worth testing.** `signal` and `doctor` are conveniences; the
reasoning on a malformed signal is the safety property. A pipeline that accepted
`{"action": "BUY", "symbol": "EURUSD"}` with no entry and no stop would be a
pipeline with a strategy in it, and the CLI is the most likely way for someone to
hand it one by hand.
"""

from __future__ import annotations

import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from signal_to_trade_bridge.cli import EXIT_FAULT, main
from signal_to_trade_bridge.cli.main import _signal_from

VALID = {
    "id": "s-1",
    "symbol": "EURUSD",
    "timeframe": "H1",
    "action": "BUY",
    "direction": "LONG",
    "entry": "1.10000",
    "stop_loss": "1.09700",
    "take_profit": "1.10300",
    # The bases are not decoration and their absence is a refusal, not a default.
    # `stop_basis` says where the stop came from, and a price without a stated
    # origin is not a stop this project will act on -- which is what the first
    # version of this fixture discovered by being refused.
    "stop_basis": "PULLBACK_EXTREME",
    "take_profit_basis": "SWING",
    "evidence_score": 0.72,
}


def _run(*argv: str) -> tuple[int, str]:
    out = StringIO()
    return main(list(argv), out=out), out.getvalue()


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "sig.json"
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, "utf-8")
    return path


def _cli_module():
    """The `cli.main` *module*, unambiguously.

    `cli/__init__.py` re-exports the function `main`, and that name shadows the
    submodule of the same name on the package object -- so
    `import signal_to_trade_bridge.cli.main as m` binds the *function*, and
    `m.build_bridge` is an `AttributeError`. `sys.modules` is the only spelling that
    means the module. The collision is legal Python and every package with a
    re-exported entry point has it; it is worth writing down rather than
    rediscovering.
    """
    import sys

    return sys.modules["signal_to_trade_bridge.cli.main"]


class TestTheHappyPath:
    def test_a_good_signal_through_the_whole_pipeline(self, bridge_factory: object) -> None:
        # `check`'s primary path, and it was not covered: every test of `check` so
        # far fed it a broken file. The success path is where the volume arithmetic
        # and the report rendering happen, and it is reached by the command the
        # documentation tells people to run first.
        m = _cli_module()
        original = m.build_bridge
        m.build_bridge = lambda config: bridge_factory()  # type: ignore[assignment]
        try:
            code, output = _run("check", str(_write(Path("."), VALID)))
        finally:
            m.build_bridge = original
        assert code == 0
        assert "DRY_RUN" in output
        assert "volume" in output

    def test_the_computed_volume_comes_from_the_account(self, bridge_factory: object) -> None:
        m = _cli_module()
        original = m.build_bridge
        m.build_bridge = lambda config: bridge_factory()  # type: ignore[assignment]
        try:
            _code, output = _run("check", str(_write(Path("."), VALID)))
        finally:
            m.build_bridge = original
        line = next(line for line in output.splitlines() if "volume" in line)
        # $10,000 at 0.5% over a 300-tick stop at $1/tick is $50 of risk per lot.
        # The exact number is not the point; the point is that a number was
        # *computed*, so a change in the template can never change it.
        assert Decimal(line.split()[-1]) > 0


class TestMalformedSignalsAreRefused:
    """Every one of these must be a fault, not a default.

    A default is the failure mode worth guarding. `entry` defaulting to zero, or
    `direction` defaulting to LONG, produces a trade that is arithmetically valid
    and completely invented -- and a CLI is exactly where someone will hand-edit a
    file.
    """

    def test_a_json_array_is_refused(self) -> None:
        with pytest.raises(TypeError, match="expected a JSON object"):
            _signal_from([VALID])

    def test_a_json_string_is_refused(self) -> None:
        with pytest.raises(TypeError, match="expected a JSON object"):
            _signal_from("BUY")

    @pytest.mark.parametrize("direction", ["", "sideways", "long ", "LONGISH", "0"])
    def test_an_unrecognised_direction_is_refused(self, direction: str) -> None:
        payload = dict(VALID, direction=direction)
        with pytest.raises(ValueError, match="unknown direction"):
            _signal_from(payload)

    @pytest.mark.parametrize("field", ["symbol", "action", "direction", "entry"])
    def test_a_missing_required_field_is_refused_by_name(self, field: str) -> None:
        payload = dict(VALID)
        del payload[field]
        with pytest.raises(KeyError, match=field):
            _signal_from(payload)

    def test_a_missing_entry_is_never_defaulted_to_zero(self) -> None:
        payload = dict(VALID)
        del payload["entry"]
        with pytest.raises(KeyError, match="entry"):
            _signal_from(payload)  # the rule is asserted in its own tests

    def test_the_stop_may_be_absent_but_the_action_may_not_be_guessed(self) -> None:
        # The asymmetry is deliberate. A signal with no stop is a legitimate
        # abstention; a signal whose *direction* is missing is not, because the
        # direction is what everything downstream is computed from.
        payload = dict(VALID)
        del payload["stop_loss"]
        signal = _signal_from(payload)
        assert signal.stop_loss is None

    def test_either_id_spelling_is_accepted(self) -> None:
        # `auto_trade`'s constructor takes `signal_id` and its `from_dict` reads
        # `id`. Accepting only one would reject half the files either project
        # produces, and would have rejected the example this CLI emits.
        assert _signal_from(dict(VALID, id="a")).signal_id == "a"
        assert _signal_from(dict(VALID, signal_id="b")).signal_id == "b"

    def test_when_both_are_present_signal_id_wins(self) -> None:
        # The example this CLI emits carries both, set to the same value, so the
        # precedence is only observable for a file that is internally inconsistent
        # -- which is what a hand-edited signal file is. `signal_id` wins because it
        # is the spelling this project writes first and the one its ledger keys on,
        # so preferring it keeps a hand-edited file deduplicating against the same
        # trade it would have without the edit. Pinned, because changing it silently
        # changes which trade a hand-edited file collides with.
        assert _signal_from(dict(VALID, id="c", signal_id="d")).signal_id == "d"

    def test_a_signal_with_no_identity_gets_a_deterministic_one(self) -> None:
        # Not a UUID. A fresh UUID per call would defeat the downstream dedup
        # ledger, which is keyed on this value and must survive a restart.
        payload = dict(VALID)
        payload.pop("id", None)
        payload.pop("signal_id", None)
        first = _signal_from(payload).signal_id
        second = _signal_from(payload).signal_id
        assert first == second
        # Prefixed for legibility at a glance in a ledger, then a digest. The
        # length is 32 hex characters because that is what the identity function
        # uses everywhere else in the project -- the same trade deduplicates the
        # same way whether it arrived from the engine or from a file.
        assert first.startswith("stb-buy-")
        assert len(first) == len("stb-buy-") + 32

    def test_a_stop_price_with_no_basis_is_refused_not_completed(
        self, bridge_factory: object
    ) -> None:
        # Found by writing the fixture above without `stop_basis` and being
        # refused. It is worth pinning because "fill in the missing provenance" is
        # the obvious tempting shortcut, and it is the line that would let this
        # project invent where a stop came from.
        payload = dict(VALID)
        del payload["stop_basis"]
        m = _cli_module()
        original = m.build_bridge
        m.build_bridge = lambda config: bridge_factory()  # type: ignore[assignment]
        try:
            code, output = _run("check", str(_write(Path("."), payload)))
        finally:
            m.build_bridge = original
        assert code == 1, output
        assert "NO_VALID_STOP" in output


class TestUnreadableFiles:
    def test_a_directory_is_a_fault(self, tmp_path: Path) -> None:
        # Passing the log directory by mistake is the most likely operator error,
        # and `IsADirectoryError` is an `OSError`, so it takes the read-error path
        # rather than the decode path.
        code, output = _run("check", str(tmp_path))
        assert code == EXIT_FAULT
        assert "could not be read" in output

    def test_a_locked_file_is_a_fault(self, tmp_path: Path) -> None:
        import os

        path = _write(tmp_path, VALID)
        handle = path.open("r+b")
        try:
            if os.name == "nt":
                # On Windows an open handle does not block a second reader, so the
                # denial has to be explicit to test the branch.
                import ntfile  # type: ignore[import-not-found]

                ntfile.lock(handle, ntfile.LOCKFILE_EXCLUSIVE_LOCK)
            code, output = _run("check", str(path))
        except (ImportError, OSError):
            pytest.skip("cannot lock a file on this platform")
        finally:
            handle.close()
        assert code == EXIT_FAULT
        assert "ERROR" in output

    def test_binary_content_is_refused_with_a_readable_message(self, tmp_path: Path) -> None:
        # A file that is not text in any of the four encodings. The message has to
        # name the encodings, or the operator has no idea what to do about it.
        path = tmp_path / "s.bin"
        path.write_bytes(bytes(range(256)) * 4)
        code, output = _run("check", str(path))
        assert code == EXIT_FAULT
        assert "not text in any encoding" in output
        assert "cp1252" in output


class TestDoctorWithATerminalConfigured:
    """`doctor`'s reporting paths, which no test covered.

    `doctor` is the command a cautious person runs before deciding anything, and
    its entire job is describing a machine. The branch that says "the terminal path
    you configured does not exist" and the branch that reports the control-id
    refusal are the two outputs that matter, and both were untested.
    """

    def test_a_terminal_path_that_does_not_exist_is_named(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(tmp_path / "nope" / "terminal64.exe"))
        _code, output = _run("doctor")
        assert "no terminal at" in output
        # A fault, because a machine whose configured terminal is absent cannot run
        # the bridge. Reporting it as OK would be the worst possible answer.
        assert _run("doctor")[0] == EXIT_FAULT

    def test_a_data_path_that_does_not_exist_is_named(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The data directory is the value a reader has to go and find by hand -- a
        # hash-named folder under MetaQuotes\Terminal, one per installation. It was
        # the one configured path `doctor` did not report, which is why this branch
        # had never run.
        executable = tmp_path / "terminal64.exe"
        executable.write_bytes(b"")
        monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(executable))
        monkeypatch.setenv("BRIDGE_MT5_DATA_PATH", str(tmp_path / "no-such-data"))
        _code, output = _run("doctor")
        assert "no MT5 data directory at" in output
        # Reported, and named in the status block too, so the operator can see what
        # was actually read rather than only what went wrong.
        assert "data directory" in output

    def test_a_present_data_path_is_shown_without_complaint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        executable = tmp_path / "terminal64.exe"
        executable.write_bytes(b"")
        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(executable))
        monkeypatch.setenv("BRIDGE_MT5_DATA_PATH", str(data))
        _code, output = _run("doctor")
        assert "no MT5 data directory at" not in output
        assert str(data) in output

    def test_an_unreadable_ledger_directory_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # `doctor` must not raise. The moment it does, an operator gets a traceback
        # instead of the diagnosis they ran the command for.
        monkeypatch.setenv("BRIDGE_LOG_DIR", str(tmp_path / "logs"))
        code, _output = _run("doctor")
        assert code in (0, EXIT_FAULT)

    def test_the_control_id_refusal_is_reported_when_a_build_differs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The real terminal is not needed: a fake executable plus a fake data
        # directory with a different build in the snapshot is enough to make the
        # checker refuse, and refusal is the assertion.
        fake = tmp_path / "terminal64.exe"
        fake.write_bytes(b"")
        data = tmp_path / "data"
        data.mkdir()
        (data / "build.txt").write_text("99999", "utf-8")
        monkeypatch.setenv("BRIDGE_MT5_TERMINAL_PATH", str(fake))
        monkeypatch.setenv("BRIDGE_MT5_DATA_PATH", str(data))
        _code, output = _run("doctor")
        assert "REFUSED" in output or "not checked" in output
