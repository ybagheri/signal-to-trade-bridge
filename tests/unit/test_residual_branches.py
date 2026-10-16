"""The last uncovered branches, area by area.

Every test here pins one `raise`, one refusal, or one rendering that the rest
of the suite never reaches -- the final line in an error path, the fallback
that only fires for malformed input, the witness branch for a state the happy
path never produces. Each one is written against the smallest seam that proves
the behaviour, and each one says which line it covers so a future reader can
tell a regression from a relocation.
"""

from __future__ import annotations

import logging
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from signal_to_trade_bridge.adapters.albrooks.identity import _render
from signal_to_trade_bridge.adapters.albrooks.mapper import (
    AnalysisOutcome,
    _bar_time,
    _evidence_score,
    map_result_to_signal,
)
from signal_to_trade_bridge.adapters.albrooks.source import (
    _bar_time_at,
    _newest_bar,
    abstention_reason,
)
from signal_to_trade_bridge.domain.models import Signal


class TestIdentityRendering:
    def test_a_decimal_renders_exactly(self) -> None:
        # `str` on a Decimal never uses exponent notation at these magnitudes,
        # while `float` would round. Two prices that differ below float
        # precision must not collapse into one identity key.
        assert _render(Decimal("1.11786")) == "1.11786"

    def test_anything_else_falls_back_to_str(self) -> None:
        # The fallback exists for the non-float, non-Decimal stragglers --
        # `bar_index` is an int and reaches the key through here.
        assert _render(299) == "299"
        assert _render(None) == ""


class TestMapperFallbacks:
    def test_an_outcome_is_truthy_only_with_a_signal(self) -> None:
        assert bool(AnalysisOutcome(signal=None, reason="WAIT")) is False

    def test_a_non_numeric_evidence_value_is_not_a_score(self) -> None:
        # Neither `float("abc")` (ValueError) nor `float(object())`
        # (TypeError) is a concentration of anything. Both must be refused
        # rather than crash the mapping.
        assert _evidence_score({"value": "abc"}) is None
        assert _evidence_score({"value": object()}) is None

    def test_a_bar_time_with_no_matching_index_is_absent(self) -> None:
        # Features exist but none names the analysed bar: the time is unknown,
        # not the last row's. Dating the reading with another bar's time would
        # be a fabrication.
        result = SimpleNamespace(
            last_closed_bar=299,
            bar_features=[{"index": 5, "time": 1727740800.0}],
        )
        assert _bar_time(result) is None

    def test_a_result_with_no_decision_is_malformed_not_a_crash(self) -> None:
        outcome = map_result_to_signal(object())
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_MALFORMED"


class TestSourceErrorPaths:
    def _source(self, **kwargs: Any) -> Any:
        from signal_to_trade_bridge.adapters.albrooks.source import (
            AlBrooksSignalSource,
        )
        from tests.stubs import StubAnalyzer, StubMarketData

        return AlBrooksSignalSource(
            StubMarketData(),
            analyzer=StubAnalyzer(**kwargs),  # type: ignore[arg-type]
        )

    def test_an_engine_error_marked_invalid_is_reraised_not_wrapped(self) -> None:
        # `InvalidSignalError` from the engine is already the bridge's own
        # vocabulary: wrapping it would add a frame and lose the cause.
        from signal_to_trade_bridge.domain.errors import InvalidSignalError

        source = self._source(error=InvalidSignalError("the engine declined"))
        with pytest.raises(InvalidSignalError, match="the engine declined"):
            source.latest_signal("EURUSD", "H1")

    def test_a_signal_free_outcome_is_logged_as_a_rejection(self) -> None:
        # An outcome with no signal and no degenerate flag: malformed, not
        # quiet. The branch logs it as rejected so the log distinguishes it
        # from an abstention, which is a considered decision.
        source = self._source(result=object())
        outcome = source.analyze("EURUSD", "H1")
        assert outcome.signal is None
        assert outcome.reason == "SIGNAL_MALFORMED"

    def test_the_default_analyzer_is_the_real_engine_or_a_clean_refusal(
        self,
    ) -> None:
        # With no analyzer supplied the source resolves the real engine. On a
        # machine without `albrooks` that is a refusal naming the setup step,
        # not an ImportError from deep inside the call.
        import importlib.util

        from signal_to_trade_bridge.adapters.albrooks.source import (
            AlBrooksSignalSource,
        )
        from signal_to_trade_bridge.domain.errors import InvalidSignalError

        source = AlBrooksSignalSource.__new__(AlBrooksSignalSource)
        source._analyzer = None
        if importlib.util.find_spec("albrooks") is None:
            with pytest.raises(InvalidSignalError, match="albrooks package"):
                source._resolve_analyzer()
        else:
            analyzer = source._resolve_analyzer()
            assert callable(getattr(analyzer, "analyze", None))

    def test_bar_time_guards_reject_what_is_not_a_bar_index(self) -> None:
        bars = [{"time": 1.0}, {"time": 2.0}]
        # A string is not an int, a bool is not an index, and neither is a
        # negative or an out-of-range one. All four are "no time known",
        # never a neighbouring bar's.
        assert _bar_time_at(bars, "299") is None  # type: ignore[arg-type]
        assert _bar_time_at(bars, True) is None
        assert _bar_time_at(bars, -1) is None
        assert _bar_time_at(bars, 7) is None

    def test_bar_time_at_a_raising_series_is_absent(self) -> None:
        # A series whose indexing fails is not a series that can date a
        # reading. Refused as unknown rather than crashing the guard.
        class _Raising:
            def __len__(self) -> int:
                return 3

            def __getitem__(self, index: int) -> Any:
                raise IndexError("gone")

        assert _bar_time_at(_Raising(), 1) is None

    def test_newest_bar_of_a_raising_series_is_absent(self) -> None:
        class _Raising:
            def __len__(self) -> int:
                return 3

            def __getitem__(self, index: int) -> Any:
                raise IndexError("gone")

        assert _newest_bar(_Raising()) == (None, None)


class TestAbstentionReasonNeedsNoReasonForATrade:
    def test_a_tradable_signal_has_no_abstention_reason(self, buy_signal: Signal) -> None:
        # `abstention_reason` answers "why is this not a trade". A signal that
        # *is* a trade reaches the end of the function, and the answer is
        # nothing -- which is a return value, not a missing branch.
        assert abstention_reason(buy_signal) == ""


class TestExecutionModeNoLevels:
    def test_levels_absent_means_nothing_to_lose(self) -> None:
        # Only orders that carry a level are refused in one-click mode. An
        # order with neither has nothing the mode would lose, and refusing
        # those too would be a different bug -- a bridge that cannot trade at
        # all on a machine with Algo Trading off.
        from signal_to_trade_bridge.adapters.mt5.execution_mode import (
            refuse_one_click_order,
        )

        request = SimpleNamespace(stop_loss=None, take_profit=None)
        assert refuse_one_click_order(request, one_click=True) == ""


class TestExecutorUnreadableMode:
    def test_an_unreadable_terminal_is_a_refusal_not_a_guess(self) -> None:
        # The terminal answers, but reading the answer raises: the mode cannot
        # be established, so the order's stop cannot be confirmed as one that
        # would actually be sent. Failing the other way opens an unprotected
        # position.
        from signal_to_trade_bridge.adapters.auto_trade.executor import (
            AutoTradeExecutor,
        )
        from signal_to_trade_bridge.domain.enums import Direction
        from signal_to_trade_bridge.domain.models import ExecutionRequest

        class _Exploding:
            def __getattr__(self, name: str) -> Any:
                raise RuntimeError("the terminal stopped answering")

        executor = AutoTradeExecutor.__new__(AutoTradeExecutor)
        executor._mt5_bindings = SimpleNamespace(terminal_info=lambda: _Exploding())
        request = ExecutionRequest(
            signal_id="x",
            symbol="EURUSD",
            direction=Direction.LONG,
            volume=Decimal("0.10"),
            entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09700"),
        )
        message = executor._refuse_for_execution_mode(request)
        assert "could not be read" in message
        assert "no stop" in message


class TestConfigFloatGuard:
    def test_a_non_numeric_float_setting_refuses_to_start(
        self, clean_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import os

        from signal_to_trade_bridge.configuration.config import config_from_env
        from signal_to_trade_bridge.domain.errors import ConfigurationError

        monkeypatch.chdir(tmp_path)
        os.environ["BRIDGE_MINIMUM_EVIDENCE_SCORE"] = "a lot"
        with pytest.raises(ConfigurationError, match="is not a number"):
            config_from_env(apply=False)


class TestEnvFileUnreadable:
    def test_an_unreadable_file_is_a_configuration_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from signal_to_trade_bridge.configuration import config as config_module
        from signal_to_trade_bridge.domain.errors import ConfigurationError

        path = tmp_path / ".env"
        path.write_text("A=1\n", encoding="utf-8")

        def _denied(self: Path, *args: Any, **kwargs: Any) -> str:
            raise OSError("denied")

        monkeypatch.setattr(Path, "read_text", _denied)
        with pytest.raises(ConfigurationError, match="could not read"):
            config_module.load_dotenv(path)


class TestFormattersRenderTracebacks:
    def _record_with_exc(self) -> Any:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            return logging.LogRecord("test", logging.ERROR, __file__, 1, "msg", (), sys.exc_info())

    def test_json_carries_the_traceback(self) -> None:
        import json

        from signal_to_trade_bridge.infrastructure.logging.structured import (
            _JsonFormatter,
        )

        payload = json.loads(_JsonFormatter().format(self._record_with_exc()))
        assert "Traceback" in payload["exception"]
        assert "boom" in payload["exception"]

    def test_text_appends_the_traceback(self) -> None:
        from signal_to_trade_bridge.infrastructure.logging.structured import (
            _TextFormatter,
        )

        text = _TextFormatter().format(self._record_with_exc())
        assert "Traceback" in text
        assert "boom" in text


class TestLiveSideRefusals:
    @pytest.mark.requires_auto_trade
    def test_a_missing_audit_log_refuses_not_silences(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No audit sink means an order with no record of what it placed, so the
        # live side refuses rather than assembling without one. The import is
        # removed rather than the file, because the failure is "the execution
        # project has no audit log", not "the filesystem is broken".
        import signal_to_trade_bridge.live as live_module
        from signal_to_trade_bridge.composition import CompositionRefusal
        from signal_to_trade_bridge.configuration.config import BridgeConfig

        monkeypatch.delattr("auto_trade.infrastructure.logging.audit.AuditLogger", raising=False)
        config = BridgeConfig(log_directory=tmp_path / "logs")
        with pytest.raises(CompositionRefusal, match="audit log"):
            live_module._live_side(object(), config, object())

    def test_an_unreadable_snapshot_build_is_no_build(self, tmp_path: Path) -> None:
        # A snapshot file that cannot be parsed is skipped, not trusted: the
        # build gate compares two independent sources, and a corrupt read is
        # not a source.
        from signal_to_trade_bridge.live import _build_from_snapshot

        files = tmp_path / "MQL5" / "Files"
        files.mkdir(parents=True)
        (files / "auto_trade_positions_a.json").write_text("{not json", encoding="utf-8")
        assert _build_from_snapshot(tmp_path) is None

    def test_an_unreadable_snapshot_path_is_no_build(self, tmp_path: Path) -> None:
        # A directory where the file should be: `read_text` raises `OSError`,
        # which takes the same skip path as corrupt content.
        from signal_to_trade_bridge.live import _build_from_snapshot

        files = tmp_path / "MQL5" / "Files"
        files.mkdir(parents=True)
        (files / "auto_trade_positions_a.json").mkdir()
        assert _build_from_snapshot(tmp_path) is None
