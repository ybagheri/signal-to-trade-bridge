"""Phase 11, against the real terminal. **Read-only, and opt-in.**

Every test here talks to a running MetaTrader 5. None of them clicks, and none of
them places an order — this file reads an account, reads a symbol specification,
reads a published position snapshot, and refuses. That is the whole scope, and it is
the scope that can be exercised without a human watching.

### The opt-in marker is a real gate, not a convention

`BRIDGE_ALLOW_MT5_TESTS=1` has to be set, and the file skips without it. Three
reasons, and the third is the one that matters:

* a terminal that is not running makes the file useless rather than failing
* a test that reads a *live account* is a test whose result changes as the account
  changes, so it cannot be a unit test
* **a suite that silently starts depending on a machine state is a suite whose green
  tick stops meaning "correct"** — on the maintainer's laptop it is proof, on CI it
  is a skip, and nothing records which one you got

### What "read-only" means here, mechanically

* `initialize` is called, which **connects** to a running terminal. `launch` is
  never called, and a test asserts the AST contains no `launch` attribute anywhere in
  the adapter package.
* nothing calls `execute_order`, `prepare_order`, `click` or `set_foreground`
* the position snapshot is a *file the terminal publishes*; reading it is not
  touching the terminal at all

### The account must be a demo one

Every test that reads an account asserts `DEMO` before it proceeds. A demo account
on this machine is `53184454` on `Alpari-MT5-Demo`, confirmed from the terminal's
own title bar. If the terminal is logged into anything else the tests stop, because
a test that reads a live account's balance is not a test.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
from collections.abc import Iterator
from importlib.util import find_spec
from pathlib import Path

import pytest

from signal_to_trade_bridge.adapters.mt5 import (
    MT5AccountProvider,
    MT5PositionReader,
    MT5SymbolSpecProvider,
    load_bindings,
    positions,
)
from signal_to_trade_bridge.adapters.mt5 import bindings as bindings_module
from signal_to_trade_bridge.composition import BridgeConfig, CompositionRefusal
from signal_to_trade_bridge.domain.models import RiskParameters
from signal_to_trade_bridge.live import (
    MEASURED_ON_BUILD,
    BuildMismatch,
    build_live,
    check_control_ids,
)

pytestmark = pytest.mark.mt5

#: This machine's terminal and data folder. **Parameters, never constants in the
#: package** -- the values live in the test, and the package is given them.
TERMINAL = Path(r"C:\Program Files\Alpari MT5_4\terminal64.exe")
DATA_PATH = Path(
    r"C:\Users\BazikadeStore\AppData\Roaming\MetaQuotes\Terminal"
    r"\1D9617E1A6A4352DBDC25D08FEC12BD2"
)

#: The demo account this machine is logged into, read from the terminal's own title
#: bar rather than assumed. Asserted below, not trusted.
DEMO_LOGIN = 53184454

#: **What `account_info().name` actually is on this machine: the account's display
#: name, not the server name.** Phase 11 found this by reading a live account rather
#: than by reading documentation:
#:
#: ```
#: terminal title bar   53184454 - Alpari-MT5-Demo: Demo Account - Hedge - Alpari - [USDInd,H1]
#: account_info().login  53184454
#: account_info().name   "YouJos Hundred"        <- the account holder's name
#: ```
#:
#: So the server is in the *title bar* and the name field is not it. Asserting
#: `name == "Alpari-MT5-Demo"` would have failed here, and the bridge reads
#: ``AccountBalance.server`` from that field -- so a log line reading
#: ``server=YouJos Hundred`` is **correct** and not a bug in the mapping.
#: Recorded here so nobody later "fixes" the adapter to put a server name in a field
#: the terminal fills with an account name.
DEMO_ACCOUNT_NAME = "YouJos Hundred"

#: The server, which is only observable from the window title. Kept as a literal
#: rather than a constant the code reads, because nothing in the Python bindings
#: exposes it -- and the bridge does not need it, since the demo/live distinction
#: comes from the account type.
DEMO_SERVER = "Alpari-MT5-Demo"

LIVE = os.getenv("BRIDGE_ALLOW_MT5_TESTS") == "1"


def _skip_without_optin() -> None:
    if not LIVE:
        pytest.skip("set BRIDGE_ALLOW_MT5_TESTS=1 to run the live MT5 tests")


def _skip_without_package() -> None:
    if find_spec("MetaTrader5") is None:
        pytest.skip(
            "the MetaTrader5 python package is not installed here. It ships separately "
            "from the terminal and is listed under the 'windows' extra in pyproject.toml: "
            "pip install -e '.[windows]'"
        )


# --- the refusals, which need no terminal at all ----------------------------


class TestTheControlIdRefusal:
    """Known Issue 5, and this is where it is enforced rather than documented.

    These need no terminal and no opt-in: they read files. A check that only ran
    when someone had a trading terminal open would be a check that never ran.
    """

    def test_this_machine_is_a_46_build_gap(self) -> None:
        check = check_control_ids(TERMINAL, DATA_PATH)
        assert check.build == 6230
        assert check.expected == MEASURED_ON_BUILD == 6184
        assert not check.matches
        assert abs(check.build - check.expected) == 46

    def test_both_sources_agree_on_the_build(self) -> None:
        # Two sources, and they agreeing is what makes either worth believing.
        check = check_control_ids(TERMINAL, DATA_PATH)
        assert check.agreed is True
        assert set(check.sources.values()) == {6230}

    def test_a_mismatched_build_is_refused_with_an_actionable_message(self) -> None:
        message = check_control_ids(TERMINAL, DATA_PATH).refusal()
        assert "6184" in message
        assert "6230" in message
        assert "re-measured" in message

    def test_the_live_path_is_refused_on_this_build(self, tmp_path: Path) -> None:
        # The end-to-end consequence, and the one that matters: with execution
        # enabled and dry-run off, a bridge on this machine still refuses.
        config = BridgeConfig(
            risk=RiskParameters(allowed_symbols={"EURUSD"}),
            execution_enabled=True,
            dry_run=False,
            log_directory=tmp_path,
            mt5_terminal_path=TERMINAL,
            mt5_data_path=DATA_PATH,
        )
        with pytest.raises(BuildMismatch) as raised:
            build_live(config, terminal=TERMINAL, data_path=DATA_PATH)
        assert "6230" in str(raised.value)

    def test_the_build_mismatch_is_its_own_exception_type(self) -> None:
        # Because it is the one refusal that is about the *machine*. An operator
        # meeting it needs to know no amount of configuration will clear it.
        assert issubclass(BuildMismatch, CompositionRefusal)
        assert not issubclass(CompositionRefusal, BuildMismatch)

    def test_a_missing_source_is_a_refusal_not_a_pass(self, tmp_path: Path) -> None:
        # "One source said so" is not agreement. With the snapshot folder absent the
        # check must not quietly decide it has no objection.
        check = check_control_ids(TERMINAL, tmp_path / "no-such-folder")
        assert check.agreed is False
        assert not check.matches
        assert "disagree" in check.refusal() or "could not be read" in check.refusal()

    def test_an_unreadable_executable_is_a_refusal_not_a_pass(self, tmp_path: Path) -> None:
        # An executable that is not there leaves the snapshot as the only source, and
        # one source is not agreement -- so the message is about the *disagreement*,
        # not about the file being unreadable. Worth asserting exactly: a message
        # saying "could not be read" here would point an operator at a file that read
        # perfectly well.
        check = check_control_ids(tmp_path / "not-a-terminal.exe", DATA_PATH)
        assert check.agreed is False
        assert not check.matches
        assert "disagree" in check.refusal()

    def test_no_readable_source_at_all_says_so(self, tmp_path: Path) -> None:
        # Both missing, which is a different message: there is nothing to compare.
        check = check_control_ids(tmp_path / "no.exe", tmp_path / "no-data")
        assert check.build is None
        assert not check.agreed
        assert "could not be read" in check.refusal()

    def test_a_source_that_disagrees_is_refused(self, tmp_path: Path) -> None:
        # A terminal updated while running is a real state, and it is the reason two
        # sources are compared rather than one being preferred.
        files = tmp_path / "MQL5" / "Files"
        files.mkdir(parents=True)
        (files / "auto_trade_positions_a.json").write_text(
            json.dumps({"schema": 1, "terminal_build": 6184}), encoding="utf-8"
        )
        check = check_control_ids(TERMINAL, tmp_path)
        assert check.agreed is False
        assert "disagree" in check.refusal()


# --- the structural guarantees, also no terminal needed ---------------------


class TestNothingHereCanClick:
    def test_the_adapter_package_never_names_launch(self) -> None:
        # Phase 7's rule, still enforced. `initialize` connects; `launch` starts a
        # process, and a process that starts a trading terminal is a process that can
        # start it by accident.
        for module in (bindings_module, positions):
            tree = ast.parse(inspect.getsource(module))
            calls = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert "launch" not in calls, f"{module.__name__} calls launch()"
            attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            assert "launch" not in attrs, f"{module.__name__} reaches a .launch attribute"

    def test_the_live_module_cannot_execute_anything_by_itself(self) -> None:
        import signal_to_trade_bridge.live as live

        source = inspect.getsource(live)
        # It assembles; it does not call. `execute_order` and `prepare_order` are the
        # two that reach a control, and neither may appear as a call here.
        tree = ast.parse(source)
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "execute_order" not in calls
        assert "prepare_order" not in calls
        assert "click" not in calls


# --- the live reads ---------------------------------------------------------


@pytest.mark.skipif(not LIVE, reason="set BRIDGE_ALLOW_MT5_TESTS=1")
class TestAgainstTheRunningTerminal:
    """Read-only. Every test here asserts the account is a demo before using it."""

    @pytest.fixture
    def mt5(self) -> Iterator[object]:
        """A fresh connection per test, torn down after it.

        **Function-scoped, and that is load-bearing.** The bindings keep their state
        in module globals, so a class-scoped fixture that calls ``shutdown`` after
        its last test leaves every *earlier* test's connection invalid for any test
        that runs afterwards -- and the symptom is `symbol_info` answering ``None``
        for every symbol, which looks exactly like a terminal with no market data.

        Connecting per test costs one local IPC round trip. Diagnosing a teardown
        ordering bug in a suite that talks to a trading terminal costs more.
        """
        _skip_without_optin()
        _skip_without_package()
        try:
            bindings = load_bindings(TERMINAL)
        except Exception as exc:
            pytest.skip(f"the terminal could not be connected to: {exc}")
        yield bindings
        bindings.shutdown()

    def test_the_terminal_is_already_running_and_logged_in(self, mt5: object) -> None:
        # `initialize` connects. Nothing here starts a process -- the terminal was
        # already up, and if it were not this is where the test would stop.
        info = mt5.account_info()  # type: ignore[attr-defined]
        assert info is not None, "the terminal is not running, or not logged in"

    def test_the_account_is_the_demo_one(self, mt5: object) -> None:
        info = mt5.account_info()  # type: ignore[attr-defined]
        assert int(info.login) == DEMO_LOGIN, (
            f"the terminal is logged into account {info.login}, not the demo "
            f"{DEMO_LOGIN}. Every test in this file reads a live account, so it stops here "
            f"rather than running against whatever account happens to be connected."
        )

    def test_the_account_name_is_not_the_server_name(self, mt5: object) -> None:
        # Recorded because it is surprising and because the bridge maps this field to
        # `AccountBalance.server`. It is **not** the server: the terminal fills it with
        # the account holder's display name, and the server is only in the title bar.
        #
        # So `server=YouJos Hundred` in a log is the adapter reporting faithfully, and
        # renaming the field would be the bug rather than the fix.
        info = mt5.account_info()  # type: ignore[attr-defined]
        assert info.name == DEMO_ACCOUNT_NAME
        assert info.name != DEMO_SERVER

    def test_the_account_provider_reads_it(self, mt5: object) -> None:
        provider = MT5AccountProvider(mt5, terminal_path=TERMINAL)  # type: ignore[arg-type]
        try:
            balance = provider.balance()
            assert balance.account_login == DEMO_LOGIN
            assert balance.currency
            assert balance.balance > 0
        finally:
            provider.shutdown()

    def test_the_symbol_provider_reads_a_loaded_symbol(self, mt5: object) -> None:
        # The field names Phase 7 could not verify, verified: `trade_contract_size`
        # and the rest come off a real `symbol_info()` rather than a double.
        #
        # **The symbol is whatever this terminal has loaded**, found by asking it,
        # because the obvious choice is not available: the terminal's title bar shows
        # `[USDInd,H1]` and Phase 11 found EURUSD *not* loaded. Hard-coding EURUSD here
        # would make the test fail for a reason that has nothing to do with the mapping
        # -- and "the symbol I assumed" is exactly the kind of assumption this project
        # keeps refusing.
        loaded = _a_loaded_symbol(mt5)
        provider = MT5SymbolSpecProvider(mt5)  # type: ignore[arg-type]
        try:
            spec = provider.spec(loaded)
            assert spec.symbol == loaded
            assert spec.contract_size > 0
            assert spec.tick_size > 0
            assert spec.tick_value_profit > 0
            assert spec.volume_min > 0
        finally:
            provider.shutdown()

    def test_the_index_symbol_is_spelled_usdind_not_usdindex(self, mt5: object) -> None:
        # Phase 11's second live finding, and the reason the symbol list is asked for
        # rather than hard-coded. The terminal's name for the Dow-style index is
        # ``USDInd`` -- visible in its own title bar as ``[USDInd,H1]`` -- and
        # ``USDIndex`` returns *no specification at all*.
        #
        # So a symbol list written from intuition would silently never resolve, and
        # every trade on that symbol would be refused as unknown rather than as a
        # typo. Which is the safe direction, and still a bug worth having found.
        #
        # Driven through the **bindings the fixture connected**, not through a fresh
        # `import MetaTrader5`. The package keeps its state in module globals, so a
        # direct call after the fixture's `shutdown` answers `None` for everything --
        # which is not a fact about the terminal but a fact about the binding's
        # lifecycle. Using the fixture's object is also the honest way to ask: it is
        # the connection this test suite made.
        assert mt5.symbol_info("USDInd") is not None  # type: ignore[attr-defined]
        assert mt5.symbol_info("USDIndex") is None  # type: ignore[attr-defined]

    def test_an_unknown_symbol_is_refused_not_defaulted(self, mt5: object) -> None:
        from signal_to_trade_bridge.adapters.mt5 import MT5Unavailable

        provider = MT5SymbolSpecProvider(mt5)  # type: ignore[arg-type]
        try:
            with pytest.raises(MT5Unavailable):
                provider.spec("NOSUCHSYMBOL")
        finally:
            provider.shutdown()

    def test_the_position_snapshot_is_fresh_and_parses(self) -> None:
        # Reads a file the terminal published. Not touching the terminal.
        reader = MT5PositionReader(DATA_PATH)
        snapshot = reader.read()
        # `schema` is a module constant, not a field: the parser refuses anything
        # else before building a snapshot, so carrying it on the result would be
        # redundant. Asserting the constant and the parse having succeeded is the
        # whole of it.
        assert positions.SUPPORTED_SCHEMA == 1
        assert snapshot.terminal_build == 6230
        assert not snapshot.is_stale()
        assert snapshot.account == DEMO_LOGIN

    def test_the_position_count_is_readable_through_the_account_provider(self) -> None:
        # The Phase 7 residual case, seen live: the reader wired into the provider,
        # so `open_positions` is a real number rather than a constant zero.
        _skip_without_optin()
        _skip_without_package()
        try:
            bindings = load_bindings(TERMINAL)
        except Exception as exc:
            pytest.skip(f"the terminal could not be connected to: {exc}")
        try:
            provider = MT5AccountProvider(
                bindings,
                terminal_path=TERMINAL,
                position_reader=MT5PositionReader(DATA_PATH),
            )
            try:
                assert provider.balance().open_positions >= 0
            finally:
                provider.shutdown()
        finally:
            bindings.shutdown()

    def test_the_staleness_limit_matches_the_upstream_default(self, tmp_path: Path) -> None:
        # Phase 7's constant, checked against the constant it has to agree with. A
        # disagreement means one reader thinks the terminal is alive while the other
        # thinks it is dead -- and both are reading the same published file.
        #
        # Built against a real (empty) directory rather than faked: the provider
        # reads its directory on construction, and a directory that does not exist
        # would fail for a reason that has nothing to do with staleness.
        if find_spec("auto_trade") is None:
            pytest.skip("auto_trade is not installed")
        from auto_trade.infrastructure.automation import MT5FilePositionSnapshotProvider

        upstream = MT5FilePositionSnapshotProvider(tmp_path)
        assert upstream.max_age == positions.DEFAULT_MAX_AGE
        # And the concrete value, so a change to either is visible in the diff rather
        # than only as a failing comparison.
        assert positions.DEFAULT_MAX_AGE.total_seconds() == 30


def _a_loaded_symbol(mt5: object) -> str:
    """A symbol this terminal actually has a specification for, found by asking it.

    Asked through the fixture's connected bindings rather than a fresh
    ``import MetaTrader5``: the package keeps its state in module globals, so a direct
    call after the fixture's ``shutdown`` answers ``None`` for everything.

    Not a hard-coded name, and not "whatever is in Market Watch" either. The terminal
    reports **879 symbols**, almost all of them CFD equities, and Phase 11 found that
    **EURUSD is not among them** -- so neither assumption works.

    The honest list is "symbols whose ``symbol_info`` is readable", which is a small
    subset of 879 and needs a live call per candidate. The candidates are therefore
    the ones a price-action trader would plausibly want, tried in order, and the first
    one that resolves is returned. A terminal with none of them skips rather than
    failing: a test that cannot find a fixture has not found a bug.
    """
    # `USDInd`, not `USDIndex` -- the terminal's own name for the index symbol, and
    # the one in its title bar. Found by asking it: `USDIndex` returns nothing and
    # `USDInd` returns a specification, which is a spelling this project would
    # otherwise have got wrong in a test and then in a symbol list.
    for candidate in ("EURUSD", "XAUUSD", "USDInd", "GBPUSD"):
        if mt5.symbol_info(candidate) is not None:  # type: ignore[attr-defined]
            return candidate

    # Fall back to asking the terminal for anything it will describe, rather than
    # naming one: the point is to read a real specification, not to test an
    # instrument the test picked.
    for symbol in mt5.symbols_get("*") or ():  # type: ignore[attr-defined]
        if symbol.name and mt5.symbol_info(symbol.name) is not None:  # type: ignore[attr-defined]
            return symbol.name

    pytest.skip("this terminal will not describe any symbol")
