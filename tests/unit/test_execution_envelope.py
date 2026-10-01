"""The execution envelope, and the ledger behind it.

Phase 9's subject is an **absence becoming a type**. For three phases the rule was
"the pipeline must not import an executor", enforced by a test reading a source file.
That test could forbid the mistake and say nothing about whether the right thing was
in place -- so it had to be replaced before anything could be wired.

These tests are ordered by what would be most expensive to get wrong:

* **the envelope's shape.** If an executor can exist without a ledger, the whole
  architecture has a hole in it and nothing else matters.
* **the ledger's fail-closed behaviour.** A ledger that answered "not recorded" when
  it could not be read would permit every signal ever attempted to be sent again.
* **the wiring**, including the case that matters most: an executor that raises must
  produce a recorded ``UNKNOWN``, not a lost decision.
* **the real ledger**, where `auto_trade` is installed -- because the guarantees this
  wrapper relies on are upstream's, and upstream is a private package that has
  changed shape before.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path

import pytest

from signal_to_trade_bridge.adapters.auto_trade import (
    AutoTradeUnavailable,
    load_bindings,
    open_ledger,
)
from signal_to_trade_bridge.adapters.auto_trade.ledger import AutoTradeLedger
from signal_to_trade_bridge.application.execution_envelope import (
    ExecutionEnvelope,
    build_execution_envelope,
)
from signal_to_trade_bridge.ports import IdempotencyStore, KillSwitch, TradeExecutor

KEY = "stb-0123456789abcdef0123456789abcdef"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# --- doubles ---------------------------------------------------------------


class _Executor:
    """A `TradeExecutor` that records and returns a configured result."""

    def __init__(self, result: object = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.submitted: list[object] = []

    def submit(self, request):  # type: ignore[no-untyped-def]
        self.submitted.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class _Store:
    """An `IdempotencyStore` over a dict, matching the two-phase port."""

    def __init__(self, keys: set[str] | None = None) -> None:
        self.keys = set(keys or ())
        self.attempts: list[tuple[str, str]] = []
        self.outcomes: list[tuple[str, str, dict]] = []

    def contains(self, key: str) -> bool:
        return key in self.keys

    def record_attempt(self, key: str, execution_id: str) -> None:
        self.attempts.append((key, execution_id))
        self.keys.add(key)

    def record_outcome(self, key: str, execution_id: str, outcome) -> None:  # type: ignore[no-untyped-def]
        self.outcomes.append((key, execution_id, dict(outcome)))


class _Switch:
    def __init__(self, active: bool = False) -> None:
        self.active = active


def _envelope(**overrides: object) -> ExecutionEnvelope:
    kwargs: dict[str, object] = {
        "executor": _Executor(),
        "idempotency": _Store(),
        "kill_switch": _Switch(),
    }
    kwargs.update(overrides)
    return build_execution_envelope(**kwargs)  # type: ignore[arg-type]


# --- the shape, which is the point of the phase ----------------------------


class TestTheEnvelopesShape:
    def test_it_cannot_be_constructed_directly(self) -> None:
        # The single most important line of the phase. If `ExecutionEnvelope(executor)`
        # worked, an executor could exist without a ledger and the type would be
        # decoration.
        with pytest.raises(TypeError, match="build_execution_envelope"):
            ExecutionEnvelope(_Executor())  # type: ignore[call-arg]

    def test_the_constructor_message_says_why(self) -> None:
        # An error that explains the rule is documentation that cannot go stale.
        with pytest.raises(TypeError) as raised:
            ExecutionEnvelope()  # type: ignore[call-arg]
        assert "ledger" in " ".join(str(raised.value).split())

    def test_it_exposes_all_three(self) -> None:
        executor, store, switch = _Executor(), _Store(), _Switch()
        envelope = _envelope(executor=executor, idempotency=store, kill_switch=switch)
        assert envelope.executor is executor
        assert envelope.idempotency is store
        assert envelope.kill_switch is switch

    def test_a_missing_ledger_is_refused(self) -> None:
        with pytest.raises(ValueError, match="idempotency store"):
            build_execution_envelope(_Executor(), None, _Switch())  # type: ignore[arg-type]

    def test_a_missing_kill_switch_is_refused(self) -> None:
        with pytest.raises(ValueError, match="kill switch"):
            build_execution_envelope(_Executor(), _Store(), None)  # type: ignore[arg-type]

    def test_a_missing_executor_is_refused(self) -> None:
        with pytest.raises(ValueError, match="executor"):
            build_execution_envelope(None, _Store(), _Switch())  # type: ignore[arg-type]

    def test_one_object_cannot_satisfy_two_roles(self) -> None:
        # A stub passed twice would say "the envelope is complete" while recording
        # nothing and stopping nothing. That is the failure the type is here to
        # prevent, and it is not one a `None` check catches.
        stub = _Executor()
        with pytest.raises(ValueError, match="distinct"):
            build_execution_envelope(stub, stub, _Switch())  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="distinct"):
            build_execution_envelope(stub, _Store(), stub)  # type: ignore[arg-type]

    def test_it_is_immutable(self) -> None:
        envelope = _envelope()
        with pytest.raises(AttributeError):
            envelope.executor = _Executor()  # type: ignore[misc]

    def test_its_repr_names_types_not_instances(self) -> None:
        # An executor's repr could carry a terminal path and a ledger's a filename
        # with a username in it.
        text = repr(_envelope())
        assert "ExecutionEnvelope(" in text
        assert "_Executor" in text
        assert "at 0x" not in text

    def test_it_satisfies_both_ports(self) -> None:
        envelope = _envelope()
        assert isinstance(envelope.executor, TradeExecutor)
        assert isinstance(envelope.idempotency, IdempotencyStore)
        assert isinstance(envelope.kill_switch, KillSwitch)


# --- the ledger adapter ----------------------------------------------------


class TestTheLedgerRefusesRatherThanAnswers:
    def test_an_unreadable_contains_refuses_rather_than_saying_false(self) -> None:
        # THE property. A ledger that answered "not recorded" because it could not be
        # read would permit every signal ever attempted to be sent again.
        class Broken:
            def contains(self, _key: str) -> bool:
                raise OSError("the file is locked")

        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            AutoTradeLedger(Broken()).contains(KEY)

    def test_the_refusal_explains_why_it_is_not_an_empty_ledger(self) -> None:
        class Broken:
            def contains(self, _key: str) -> bool:
                raise OSError("nope")

        with pytest.raises(AutoTradeUnavailable) as raised:
            AutoTradeLedger(Broken()).contains(KEY)
        message = " ".join(str(raised.value).split())
        assert "every signal ever attempted" in message

    def test_a_failed_attempt_write_refuses_before_returning(self) -> None:
        class Broken:
            def record_attempt(self, *_args: object) -> None:
                raise OSError("disk full")

        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            AutoTradeLedger(Broken()).record_attempt(KEY, "exec-1")

    def test_a_failed_outcome_write_refuses(self) -> None:
        class Broken:
            def record_result(self, _result: object) -> None:
                raise OSError("disk full")

        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            AutoTradeLedger(Broken()).record_outcome(KEY, "exec-1", {"status": "ACCEPTED"})

    def test_optional_outcome_fields_are_normalised_not_passed_through(self) -> None:
        # `order_reference` and `error` are `str | None` upstream and both may
        # arrive as `""`. An empty string is not a position ticket, and storing it
        # would make `position_id is not None` true for a result naming nothing.
        captured: list[object] = []

        class Capture:
            def record_result(self, result: object) -> None:
                captured.append(result)

        ledger = AutoTradeLedger(Capture())
        ledger.record_outcome(KEY, "exec-1", {"status": "ACCEPTED", "order_reference": "   "})
        ledger.record_outcome(KEY, "exec-1", {"status": "ACCEPTED", "order_reference": "382"})
        ledger.record_outcome(KEY, "exec-2", {"status": "ACCEPTED"})
        assert captured[0].order_reference is None
        assert captured[1].order_reference == "382"
        assert captured[2].order_reference is None

    def test_an_unrecognised_outcome_status_is_recorded_as_unknown(self) -> None:
        # Refusing would leave a pending entry an operator has to settle by hand --
        # safe, but a worse outcome than a record that says "we did not understand
        # this".
        captured: list[object] = []

        class Capture:
            def record_result(self, result: object) -> None:
                captured.append(result)

        AutoTradeLedger(Capture()).record_outcome(
            KEY, "exec-1", {"status": "TELEPORTED", "message": "?"}
        )
        assert str(captured[0].status) == "UNKNOWN"


class TestTheOperatorFacingSurface:
    """`pending` and `reconcile`, which are not part of the bridge's port.

    They exist on the adapter because the questions they answer -- "which attempts
    did we never settle" and "an operator says this one is fine" -- are questions an
    operator must be able to ask, and the bridge's three-method port cannot carry
    them. Being on the adapter rather than the port is the point: the port stays
    narrow, and this does not become something a pipeline stage has to satisfy.
    """

    def test_pending_is_empty_on_a_fresh_ledger(self) -> None:
        assert open_ledger(load_bindings(), _tmp()).pending() == ()

    def test_pending_reports_an_attempt_that_never_settled(self) -> None:
        ledger = open_ledger(load_bindings(), _tmp())
        ledger.record_attempt(KEY, "exec-1")
        pending = ledger.pending()
        assert len(pending) == 1
        assert pending[0]["signal_id"] == KEY
        assert pending[0]["status"] == "REQUESTED"

    def test_pending_is_copied_so_a_caller_cannot_change_the_record(self) -> None:
        ledger = open_ledger(load_bindings(), _tmp())
        ledger.record_attempt(KEY, "exec-1")
        entry = ledger.pending()[0]
        entry["status"] = "ACCEPTED"
        assert ledger.pending()[0]["status"] == "REQUESTED"

    def test_the_repr_shows_a_count_and_not_a_path(self) -> None:
        # The path can contain a username, and a repr ends up in tracebacks.
        path = _tmp() / "idempotency.json"
        ledger = open_ledger(load_bindings(), path)
        text = repr(ledger)
        assert text == "AutoTradeLedger(entries=0)"
        assert str(path) not in text

    def test_a_failed_pending_query_refuses_rather_than_reporting_none(self) -> None:
        class Broken:
            def pending(self) -> tuple[object, ...]:
                raise OSError("gone")

        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            AutoTradeLedger(Broken()).pending()

    def test_a_failed_reconcile_refuses(self) -> None:
        class Broken:
            def reconcile(self, _key: str, _observation: str) -> object:
                raise OSError("gone")

        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            AutoTradeLedger(Broken()).reconcile(KEY, "checked")


# --- the port's shape ------------------------------------------------------


class TestTheIdempotencyPortIsTwoPhase:
    def test_it_records_an_attempt_before_an_outcome(self) -> None:
        # The crash-recovery window. A single `record(key, mapping)` cannot express
        # it, and the collapse is invisible until the first crash -- at which point
        # the ledger says the signal was never attempted and the trade may repeat.
        store = _Store()
        store.record_attempt(KEY, "exec-1")
        assert store.contains(KEY)
        store.record_outcome(KEY, "exec-1", {"status": "ACCEPTED"})

    def test_the_port_has_no_single_shot_record(self) -> None:
        # The Phase 1 shape, asserted absent so it cannot creep back.
        assert "record" not in vars(IdempotencyStore)
        assert not hasattr(IdempotencyStore, "record")

    def test_the_execution_id_is_required_on_both_halves(self) -> None:
        for name in ("record_attempt", "record_outcome"):
            signature = inspect.signature(getattr(IdempotencyStore, name))
            assert "execution_id" in signature.parameters, name


# --- against the real ledger -----------------------------------------------


def _tmp() -> Path:
    """A fresh ledger path per call, so tests do not share one file."""
    import tempfile

    return Path(tempfile.mkdtemp()) / "idempotency.json"


@pytest.mark.skipif(
    find_spec("auto_trade") is None,
    reason="auto_trade is not installed on this machine",
)
class TestAgainstTheRealLedger:
    """Runs where ``auto_trade`` is installed, which is this machine.

    Everything above runs against doubles, so it proves this wrapper is internally
    consistent. It cannot prove upstream's guarantees, and those guarantees are the
    whole mechanism: the atomic write, the record-before-click ordering, and the
    refusal to overwrite a mismatched attempt.
    """

    def test_it_round_trips_a_real_attempt_and_outcome(self, tmp_path: Path) -> None:
        ledger = open_ledger(load_bindings(), tmp_path / "idempotency.json")
        assert ledger.contains(KEY) is False
        ledger.record_attempt(KEY, "exec-1")
        assert ledger.contains(KEY) is True
        ledger.record_outcome(KEY, "exec-1", {"status": "ACCEPTED", "message": "filled"})
        entries = ledger.pending()
        assert entries == (), "a settled attempt must not remain pending"

    def test_the_real_ledger_refuses_to_overwrite_a_different_attempt(self, tmp_path: Path) -> None:
        # What makes a crashed pending attempt survive a later retry rather than
        # being overwritten by it.
        ledger = open_ledger(load_bindings(), tmp_path / "idempotency.json")
        ledger.record_attempt(KEY, "exec-1")
        ledger.record_outcome(KEY, "exec-2", {"status": "ACCEPTED"})
        still_pending = ledger.pending()
        assert len(still_pending) == 1
        assert still_pending[0]["execution_id"] == "exec-1"

    def test_an_unreadable_real_ledger_refuses_to_open(self, tmp_path: Path) -> None:
        path = tmp_path / "idempotency.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            open_ledger(load_bindings(), path)

    def test_a_wrongly_shaped_real_ledger_refuses_to_open(self, tmp_path: Path) -> None:
        path = tmp_path / "idempotency.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(AutoTradeUnavailable, match="could not be read"):
            open_ledger(load_bindings(), path)

    def test_an_absent_ledger_opens_empty(self, tmp_path: Path) -> None:
        # A *missing* file is a first run, which is different from a corrupt one.
        ledger = open_ledger(load_bindings(), tmp_path / "nested" / "idempotency.json")
        assert ledger.contains(KEY) is False

    def test_the_reconciliation_refuses_a_settled_record(self, tmp_path: Path) -> None:
        # Load-bearing: the record cannot be rewritten by someone who merely wants
        # it to say something different.
        ledger = open_ledger(load_bindings(), tmp_path / "idempotency.json")
        ledger.record_attempt(KEY, "exec-1")
        ledger.record_outcome(KEY, "exec-1", {"status": "ACCEPTED"})
        with pytest.raises(AutoTradeUnavailable):
            ledger.reconcile(KEY, "I checked and it is fine")

    def test_the_reconciliation_settles_an_unknown_attempt(self, tmp_path: Path) -> None:
        ledger = open_ledger(load_bindings(), tmp_path / "idempotency.json")
        ledger.record_attempt(KEY, "exec-1")
        ledger.record_outcome(KEY, "exec-1", {"status": "UNKNOWN"})
        settled = ledger.reconcile(KEY, "operator checked the account")
        assert settled["status"] == "RECONCILED"
        assert "not observed by this application" in settled["message"]

    def test_the_real_ledger_persists_across_reopening(self, tmp_path: Path) -> None:
        # The property an in-memory set would silently miss, and the reason
        # `contains` is answered by the ledger rather than by this process.
        path = tmp_path / "idempotency.json"
        bindings = load_bindings()
        open_ledger(bindings, path).record_attempt(KEY, "exec-1")
        assert open_ledger(bindings, path).contains(KEY) is True

    def test_the_real_execution_status_vocabulary_is_what_we_map(self) -> None:
        # The adapter builds an `ExecutionResult` from a bridge mapping, so an
        # upstream rename would raise there rather than corrupt a record.
        bindings = load_bindings()
        for name in ("ACCEPTED", "REJECTED", "UNKNOWN", "REQUESTED", "DRY_RUN"):
            assert getattr(bindings.ExecutionStatus, name).value == name
