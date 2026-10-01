"""The execution adapter, against a stand-in for the private upstream package.

**No test in this file imports `auto_trade`.** The package is a private repository
that is not installed in this test environment, so importing it would make the
whole suite unrunnable here -- and an adapter whose only evidence is a suite
nobody can execute is an adapter whose contract is guesswork.

So the upstream surface is reproduced as doubles. They are shaped like the real
classes because the adapter reads attributes and enum members off them, and a
double that returned a dict would test a code path production never takes. The
shapes came from the upstream source, not from the adapter's expectations of it:

* ``ExecutionWorkflow.__init__`` takes eight arguments and ``execute`` takes one
  positional and returns a result -- and, critically, ``execute`` is **not**
  exception-free: it re-raises ``ExecutionUnknownError`` when a click may have
  been used.
* ``TradeSignal`` validates its id against ``^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$``,
  validates ``confidence`` as ``0..1``, upper-cases ``symbol``, and requires a
  positive volume.
* ``ExecutionStatus`` has **six** members. The sixth, ``CLOSED``, is the one this
  bridge has no answer for and therefore the one worth testing hardest.

What these tests are really for is the four decisions the module docstring claims:
that the evidence score is not laundered into ``confidence``, that ``CLOSED``
becomes ``UNKNOWN``, that an escaping exception becomes ``UNKNOWN`` rather than a
lost record, and that nothing here can reach a clicking adapter. Each has a test
that fails if the decision is reverted.
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from importlib.util import find_spec
from pathlib import Path

import pytest

from signal_to_trade_bridge.adapters.auto_trade import (
    AutoTradeBindings,
    AutoTradeExecutor,
    AutoTradeUnavailable,
    load_bindings,
)
from signal_to_trade_bridge.adapters.auto_trade import bindings as bindings_module
from signal_to_trade_bridge.adapters.auto_trade import executor as executor_module
from signal_to_trade_bridge.adapters.fake import FakeTradeExecutor
from signal_to_trade_bridge.domain.enums import Direction
from signal_to_trade_bridge.domain.models import ExecutionRequest, ExecutionResult
from signal_to_trade_bridge.ports import TradeExecutor

#: Upstream's rule, copied rather than imported. `auto_trade` is private and may
#: be absent; a test that could only run where the package is installed would be a
#: test that does not run in CI.
SIGNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

SOURCE = "signal-to-trade-bridge"

#: The instant every test in this class pretends it is. Pinned because upstream
#: risk-checks a signal's expiry against a clock: a test using the real one would
#: be correct today and wrong the day the fixture date passed it. Found by the
#: test that drives the real workflow, where every signal came back as
#: ``signal is expired`` for exactly this reason.
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# --- doubles shaped like the upstream classes ---------------------------------


class FakeStatus(StrEnum):
    """Six members, exactly as upstream declares them."""

    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    DRY_RUN = "DRY_RUN"
    CLOSED = "CLOSED"


class FakeOrderAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class UpstreamError(Exception):
    """Stands in for ``auto_trade.domain.exceptions.AutoTradeError``."""


class UnknownExecutionError(UpstreamError):
    """The one upstream exception that *escapes* ``execute`` rather than returning."""


@dataclass
class FakeSignal:
    """Records what the adapter sent, having validated it the way upstream does.

    The validation is not decoration. A double that accepted anything would let the
    adapter send a ``confidence`` of ``3.7`` or an id of ``../../x`` and the test
    suite would call it a pass -- and the first of those is the misreading this
    project has refused since Phase 2.
    """

    signal_id: str
    timestamp: datetime
    source: str
    symbol: str
    action: FakeOrderAction
    volume: Decimal
    price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    comment: str = ""
    strategy: str = ""
    confidence: float | None = None
    expiration: datetime | None = None
    metadata: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not SIGNAL_ID_PATTERN.match(self.signal_id):
            raise ValueError(f"signal id rejected by upstream rule: {self.signal_id!r}")
        if self.volume <= 0:
            raise ValueError("volume must be positive")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        self.symbol = self.symbol.upper()
        self.metadata = dict(self.metadata or {})


@dataclass
class FakeResult:
    status: str
    state: str = ""
    message: str = ""
    order_reference: str | None = None
    error: str | None = None
    evidence: object | None = None


class FakeEvidence:
    """Upstream's ``VerificationEvidence``: two snapshot references, no fill price."""

    def __init__(self, baseline: str, observed: str, position_id: str | None = None) -> None:
        self.baseline = baseline
        self.observed = observed
        self.position_id = position_id

    def to_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline,
            "observed": self.observed,
            "position_id": self.position_id,
        }


class FakeWorkflow:
    """Eight-argument constructor, one-argument ``execute``, returns a result."""

    def __init__(
        self,
        adapter: object = None,
        risk_engine: object = None,
        profile: object = None,
        policy: object = None,
        kill_switch: object = None,
        audit: object = None,
        now: object = None,
        ledger: object = None,
    ) -> None:
        self.adapter = adapter
        self.risk_engine = risk_engine
        self.profile = profile
        self.policy = policy
        self.kill_switch = kill_switch
        self.audit = audit
        self.now = now
        self.ledger = ledger
        self.received: list[FakeSignal] = []
        self.result: FakeResult | None = None
        self.raises: BaseException | None = None

    def execute(self, signal: FakeSignal) -> FakeResult:
        self.received.append(signal)
        if self.raises is not None:
            raise self.raises
        return self.result or FakeResult(status="ACCEPTED")


def _bindings() -> AutoTradeBindings:
    """A subset object matching what :mod:`load_bindings` promises."""
    return AutoTradeBindings(
        module=object(),
        ExecutionWorkflow=FakeWorkflow,
        TradeSignal=FakeSignal,
        OrderAction=FakeOrderAction,
        ExecutionResult=FakeResult,
        ExecutionStatus=FakeStatus,
        AutoTradeError=UpstreamError,
        KillSwitch=object,
    )


def _request(**overrides: object) -> ExecutionRequest:
    defaults: dict[str, object] = {
        "signal_id": "stb-0123456789abcdef0123456789abcdef",
        "symbol": "EURUSD",
        "direction": Direction.LONG,
        "volume": Decimal("0.12"),
        "entry": Decimal("1.08500"),
        "stop_loss": Decimal("1.08300"),
        "take_profit": Decimal("1.08900"),
        "comment": "pullback long",
        "strategy": "pullback_h#0",
        "evidence_score": 0.62,
        "metadata": {"bar_time": 1727740800.0},
    }
    defaults.update(overrides)
    return ExecutionRequest(**defaults)  # type: ignore[arg-type]


def _executor(workflow: FakeWorkflow | None = None, **kwargs: object) -> AutoTradeExecutor:
    return AutoTradeExecutor(
        workflow or FakeWorkflow(),
        bindings=_bindings(),
        now=lambda: NOW,
        **kwargs,  # type: ignore[arg-type]
    )


class TestTheTranslation:
    """Request to signal: four numbers unchanged, one direction translated."""

    def test_it_satisfies_the_port(self) -> None:
        assert isinstance(_executor(), TradeExecutor)

    def test_the_numbers_cross_unchanged(self) -> None:
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request())
        sent = workflow.received[0]
        assert sent.volume == Decimal("0.12")
        assert sent.price == Decimal("1.08500")
        assert sent.stop_loss == Decimal("1.08300")
        assert sent.take_profit == Decimal("1.08900")

    def test_long_becomes_buy_and_short_becomes_sell(self) -> None:
        workflow = FakeWorkflow()
        executor = _executor(workflow)
        executor.submit(_request(direction=Direction.LONG))
        executor.submit(_request(direction=Direction.SHORT))
        assert [s.action for s in workflow.received] == [FakeOrderAction.BUY, FakeOrderAction.SELL]

    def test_the_sent_id_satisfies_the_upstream_filename_rule(self) -> None:
        # The upstream rule is enforced by the double, so a regression here is a
        # ValueError rather than a test failure -- which is the point: upstream
        # would reject it at runtime too.
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request())
        assert SIGNAL_ID_PATTERN.match(workflow.received[0].signal_id)

    def test_the_timestamp_comes_from_the_injected_clock(self) -> None:
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request())
        assert workflow.received[0].timestamp == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    def test_the_source_names_the_bridge(self) -> None:
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request())
        assert workflow.received[0].source == SOURCE

    def test_the_upstream_decision_rides_along_in_metadata(self) -> None:
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request(metadata={"setup": "pullback"}))
        assert workflow.received[0].metadata["setup"] == "pullback"


class TestTheEvidenceScoreIsNotAConfidence:
    """The single most consequential mapping decision, pinned hard.

    ``TradeSignal.confidence`` is typed and validated as a probability in ``0..1``.
    The bridge's ``evidence_score`` is explicitly **not** a probability -- Phase 2
    established that, and the docstring says it again. Putting one in the other's
    field is the misreading rule 9 exists to prevent, committed silently in the
    adapter that touches both.
    """

    def test_confidence_is_left_unset(self) -> None:
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request(evidence_score=0.62))
        assert workflow.received[0].confidence is None

    def test_the_score_travels_under_its_own_name(self) -> None:
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request(evidence_score=0.62))
        assert workflow.received[0].metadata["bridge_evidence_score"] == 0.62

    def test_an_out_of_range_score_is_still_not_a_confidence(self) -> None:
        # The strongest form of the test. A score of 4.2 would be rejected outright
        # if it were sent as `confidence` -- so if it reaches the double's 0..1
        # check the adapter has started laundering it, and the ValueError names the
        # regression.
        workflow = FakeWorkflow()
        _executor(workflow).submit(_request(evidence_score=4.2))
        assert workflow.received[0].metadata["bridge_evidence_score"] == 4.2
        assert workflow.received[0].confidence is None


class TestTheStatusMapping:
    def test_the_five_known_statuses_pass_through_by_name(self) -> None:
        workflow = FakeWorkflow()
        executor = _executor(workflow)
        for name in sorted(ExecutionResult.KNOWN_STATUSES):
            workflow.result = FakeResult(status=name)
            assert executor.submit(_request()).status == name

    def test_closed_becomes_unknown_and_never_rejected(self) -> None:
        # Upstream has six statuses; the bridge knows five. CLOSED means a position
        # existed and was closed -- so the trade *happened*, and re-sending is the
        # duplicate rule 5 forbids. REJECTED would say "definitely not placed",
        # which is a licence to retry. UNKNOWN is the only honest answer.
        workflow = FakeWorkflow()
        workflow.result = FakeResult(status="CLOSED", message="position closed")
        result = _executor(workflow).submit(_request())
        assert result.status == ExecutionResult.STATUS_UNKNOWN
        assert result.is_unknown
        assert not result.is_rejected
        assert "CLOSED" in result.message

    def test_an_unrecognised_status_becomes_unknown_and_is_recorded(self) -> None:
        workflow = FakeWorkflow()
        workflow.result = FakeResult(status="TELEPORTED")
        result = _executor(workflow).submit(_request())
        assert result.status == ExecutionResult.STATUS_UNKNOWN
        assert result.evidence["upstream_status"] == "TELEPORTED"

    def test_the_upstream_state_is_kept_in_the_evidence(self) -> None:
        workflow = FakeWorkflow()
        workflow.result = FakeResult(status="REJECTED", state="ORDER_REJECTED")
        assert _executor(workflow).submit(_request()).evidence["upstream_state"] == "ORDER_REJECTED"

    def test_the_verification_evidence_is_carried_through(self) -> None:
        workflow = FakeWorkflow()
        workflow.result = FakeResult(
            status="ACCEPTED",
            state="SUCCESS",
            order_reference="382363348",
            evidence=FakeEvidence("before.json", "after.json", "382363348"),
        )
        result = _executor(workflow).submit(_request())
        assert result.is_accepted
        assert result.position_id == "382363348"
        assert result.evidence["verification"]["position_id"] == "382363348"

    def test_executed_price_is_never_invented(self) -> None:
        # Upstream's evidence carries two snapshot *references* and no fill price.
        # A click is not a fill, so there was never one to report, and putting the
        # requested price there would be the most confident-looking fabrication in
        # the adapter.
        workflow = FakeWorkflow()
        workflow.result = FakeResult(status="ACCEPTED", evidence=FakeEvidence("b", "o"))
        result = _executor(workflow).submit(_request(entry=Decimal("1.08500")))
        assert result.executed_price is None
        assert result.requested_price == Decimal("1.08500")


class TestTheEscapingException:
    """``execute`` is not exception-free, and that is the whole reason for this.

    Upstream deliberately re-raises ``ExecutionUnknownError`` when a click may have
    been used, because a returned rejection would invite a retry. An adapter that
    assumed ``execute`` returns would crash on exactly the outcome that most needs
    a record -- and the crash would take the record with it.
    """

    def test_an_escaping_upstream_exception_becomes_unknown(self) -> None:
        workflow = FakeWorkflow()
        workflow.raises = UnknownExecutionError("the final control may have been used")
        result = _executor(workflow).submit(_request())
        assert result.status == ExecutionResult.STATUS_UNKNOWN
        assert result.is_unknown

    def test_the_error_is_recorded_rather_than_swallowed(self) -> None:
        workflow = FakeWorkflow()
        workflow.raises = UnknownExecutionError("may have been used")
        result = _executor(workflow).submit(_request())
        assert "may have been used" in (result.error or "")

    def test_an_unknown_result_is_not_retryable(self) -> None:
        workflow = FakeWorkflow()
        workflow.raises = UnknownExecutionError("may have been used")
        assert _executor(workflow).submit(_request()).is_retryable is False

    def test_even_an_unexpected_exception_becomes_unknown(self) -> None:
        # Not just upstream's own error type: a bug in the adapter itself must not
        # escape as a traceback, because the caller has no way to record a crash.
        workflow = FakeWorkflow()
        workflow.raises = ZeroDivisionError("a bug, not a refusal")
        result = _executor(workflow).submit(_request())
        assert result.status == ExecutionResult.STATUS_UNKNOWN
        assert "ZeroDivisionError" in (result.error or "")

    def test_an_untranslatable_request_becomes_unknown(self) -> None:
        # A direction upstream has no word for must not become a ValueError three
        # frames down; and it must not become a rejection either, because nothing
        # was decided about the order.
        executor = AutoTradeExecutor(
            FakeWorkflow(),
            bindings=_bindings(),
            now=lambda: datetime(2026, 10, 1, tzinfo=UTC),
        )
        request = _request()
        object.__setattr__(request, "direction", Direction.FLAT)
        result = executor.submit(request)
        assert result.status == ExecutionResult.STATUS_UNKNOWN

    def test_an_empty_order_reference_is_treated_as_absent(self) -> None:
        # An empty string is not a ticket, and storing it would make
        # `position_id is not None` -- the check a caller would naturally write --
        # true for a result that names nothing.
        workflow = FakeWorkflow()
        workflow.result = FakeResult(status="ACCEPTED", order_reference="   ")
        assert _executor(workflow).submit(_request()).position_id is None


class TestTheLazyBindingsLoad:
    """The path taken when the adapter is wired without explicit bindings.

    Stronger than it looks: with no bindings injected, the adapter loads the **real**
    ``auto_trade`` and builds a **real** ``TradeSignal``, which validates the id
    against its own regex, the volume against ``> 0`` and the symbol by upper-casing
    it. A `FakeWorkflow` receives the result. So a green run here means the adapter's
    output passed upstream's own constructor -- on the machine where upstream is
    installed. Where it is not installed the status is ``UNKNOWN`` with an actionable
    message, and the first assertion covers that too.
    """

    def test_the_bindings_load_on_first_use_not_at_construction(self) -> None:
        # Constructing an executor on a machine without the checkout must not fail.
        # Only using it should. A constructor that loaded eagerly would make the
        # composition root machine-dependent for no benefit.
        executor = AutoTradeExecutor(FakeWorkflow())

        result = executor.submit(_request())

        assert isinstance(result.status, str)
        assert result.status in {
            ExecutionResult.STATUS_UNKNOWN,
            "DRY_RUN",
            ExecutionResult.STATUS_ACCEPTED,
        }
        # Whatever happened, it was a result and not an exception: the trade record
        # must survive a missing dependency.
        assert result.signal_id == _request().signal_id

    def test_a_missing_package_is_recorded_rather_than_raised(self, monkeypatch) -> None:
        # Pinned separately, because the machine running this may well have the
        # package installed and the branch would otherwise never execute.
        import signal_to_trade_bridge.adapters.auto_trade.executor as module

        def refuse() -> object:
            raise AutoTradeUnavailable(
                "the execution project could not be imported. ... from a local checkout"
            )

        monkeypatch.setattr(module, "load_bindings", refuse)
        result = AutoTradeExecutor(FakeWorkflow()).submit(_request())
        assert result.status == ExecutionResult.STATUS_UNKNOWN
        assert "local checkout" in " ".join((result.error or "").split())

    def test_an_engaged_switch_refuses_before_the_workflow_is_called(self) -> None:
        class Engaged:
            @property
            def active(self) -> bool:
                return True

        workflow = FakeWorkflow()
        result = _executor(workflow, kill_switch=Engaged()).submit(_request())
        assert result.status == ExecutionResult.STATUS_REJECTED
        assert workflow.received == []

    def test_the_refusal_is_a_clean_rejection_not_an_unknown(self) -> None:
        # A kill switch that has *prevented* the call knows the order was not sent,
        # so REJECTED is the truthful answer and a retry is safe.
        class Engaged:
            @property
            def active(self) -> bool:
                return True

        result = _executor(kill_switch=Engaged()).submit(_request())
        assert result.is_rejected
        assert result.is_retryable

    def test_a_clear_switch_lets_the_call_through(self) -> None:
        class Clear:
            @property
            def active(self) -> bool:
                return False

        workflow = FakeWorkflow()
        _executor(workflow, kill_switch=Clear()).submit(_request())
        assert len(workflow.received) == 1


class TestTheStructuralBoundaries:
    """Constraints on the source, because behaviour tests cannot see them."""

    def test_the_package_never_imports_auto_trade_at_module_level(self) -> None:
        # Only *module-level* imports are forbidden. The import inside
        # load_bindings() is the whole design, so a naive walk of every ImportFrom
        # node would fail on the correct code -- and a structural test that fires
        # on correct code gets deleted rather than fixed.
        for node in _module_level_nodes(bindings_module):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "auto_trade"
            ):
                pytest.fail(
                    f"bindings.py imports auto_trade at module level (line {node.lineno}). The "
                    f"import must stay inside load_bindings() or this package stops importing "
                    f"on a machine without the checkout, and with it the whole test suite."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "auto_trade":
                        pytest.fail(f"bindings.py imports {alias.name} at module level")

    def test_the_lazy_import_is_the_only_one(self) -> None:
        # The positive form of the constraint above: the import must still be there.
        # Without this the previous test would also pass on a file that deleted it.
        source = inspect.getsource(bindings_module)
        assert "from auto_trade.domain.models import ExecutionResult, TradeSignal" in source

    def test_the_executor_cannot_reach_a_clicking_adapter(self) -> None:
        # `MT5DesktopAdapter` is the class that clicks, and `ExecutionGate` is what
        # decides whether it may. If the executor could reach either, it could build
        # a terminal adapter with the gate enabled -- and those two independent
        # guards, the workflow's `dry_run` policy and the adapter's gate, are what
        # together make a dry run provably unable to reach a final control.
        #
        # Checked over the AST rather than the raw text, because both names appear
        # in this file's prose explaining precisely why they must not be used. A
        # text search would fail on the documentation of the rule it enforces.
        forbidden = {"MT5DesktopAdapter", "ExecutionGate", "CloseGate"}
        for module in (bindings_module, executor_module):
            used = {
                node.id
                for node in ast.walk(ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.Name)
            }
            leaked = used & forbidden
            assert not leaked, (
                f"{module.__name__} references {sorted(leaked)}. The adapter must not be able "
                f"to construct the thing that clicks, or to reach the gate that stops it."
            )

    def test_the_executor_does_not_build_its_own_workflow(self) -> None:
        # Positional or keyword *construction*, not the bare name -- the docstring
        # names the class, and it should be allowed to.
        calls = [
            node
            for node in ast.walk(ast.parse(inspect.getsource(executor_module)))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert not [c for c in calls if c.func.id == "ExecutionWorkflow"], (
            "the executor constructs its own workflow. The kill switch, ledger, audit log and "
            "terminal adapter are the safety envelope; an adapter that assembles its own could "
            "assemble one without them."
        )

    def test_the_pipeline_still_imports_no_executor(self) -> None:
        # Deliberate and temporary. Execution arrives with the ledger and the kill
        # switch around it, not before, and wiring an executor in while the
        # idempotency store is absent is the failure the architecture prevents.
        from signal_to_trade_bridge.application import process_signal as module

        forbidden = {"TradeExecutor", "IdempotencyStore", "KillSwitch"}
        for node in _module_level_nodes(module):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                names = [alias.name.split(".")[-1] for alias in node.names]
            for name in names:
                assert name not in forbidden, f"process_signal.py imports {name!r}"


def _module_level_nodes(module: object) -> list[ast.stmt]:
    """Every statement that is not inside a function or class body."""
    tree = ast.parse(inspect.getsource(module))
    nested: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested.update(ast.walk(node))
    return [node for node in tree.body if node not in nested]


class TestTheBindings:
    def test_a_missing_upstream_name_is_reported_by_name(self) -> None:
        # A private upstream package that has changed shape must fail with a sentence
        # naming the member, not with an AttributeError from three frames into the
        # adapter, where the cause reads as a mistake rather than an upstream rename.
        bindings = replace(_bindings(), REQUIRED=("domain.models.NoSuchClass",))
        with pytest.raises(AutoTradeUnavailable, match="NoSuchClass"):
            bindings.verify()

    def test_verification_returns_the_bindings_when_the_surface_is_intact(self) -> None:
        # Against the real package this is the load-time gate; here, with doubles
        # standing in for it, it is the same check running over the same list.
        bindings = replace(_bindings(), REQUIRED=())
        assert bindings.verify() is bindings

    def test_an_unknown_order_action_names_the_bridge_vocabulary(self) -> None:
        with pytest.raises(AutoTradeUnavailable, match="CLOSE_POSITION"):
            _bindings().order_action("CLOSE_POSITION")

    def test_order_action_lookup_is_case_insensitive(self) -> None:
        assert _bindings().order_action("buy") is FakeOrderAction.BUY

    def test_loading_without_the_package_explains_how_to_install_it(self, monkeypatch) -> None:
        # `auto_trade` is installed on this machine, so the absent-package branch is
        # simulated rather than relied upon -- a test that only passes because a
        # dependency is missing is a test that stops testing the moment somebody
        # installs it. The message matters because the package is private and not on
        # PyPI: "No module named auto_trade" is not something an operator can act on.
        import builtins

        real_import = builtins.__import__

        def refuse(name: str, *args: object, **kwargs: object) -> object:
            if name == "auto_trade" or name.startswith("auto_trade."):
                raise ImportError("No module named 'auto_trade'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", refuse)
        with pytest.raises(AutoTradeUnavailable) as raised:
            load_bindings()
        assert "checkout" in " ".join(str(raised.value).split())

    def test_the_subset_is_checked_against_the_upstream_surface(self) -> None:
        # The eight bound names, with the modules they genuinely live in. Upstream
        # has no top-level __all__ and re-exports nothing from its root, so every
        # name has to come from the subpackage that defines it.
        assert set(_bindings().REQUIRED) == {
            "application.workflow.ExecutionWorkflow",
            "domain.models.TradeSignal",
            "domain.models.OrderRequest",
            "domain.enums.OrderAction",
            "domain.models.ExecutionResult",
            "domain.enums.ExecutionStatus",
            "domain.exceptions.AutoTradeError",
            "domain.protocols.KillSwitch",
        }

    def test_bindings_cannot_be_built_partially(self) -> None:
        with pytest.raises(TypeError):
            AutoTradeBindings(module=object())  # type: ignore[call-arg]


def _as_kwargs(bindings: AutoTradeBindings) -> dict[str, object]:
    return {
        "module": bindings.module,
        "ExecutionWorkflow": bindings.ExecutionWorkflow,
        "TradeSignal": bindings.TradeSignal,
        "OrderAction": bindings.OrderAction,
        "ExecutionResult": bindings.ExecutionResult,
        "ExecutionStatus": bindings.ExecutionStatus,
        "AutoTradeError": bindings.AutoTradeError,
        "KillSwitch": bindings.KillSwitch,
        "REQUIRED": bindings.REQUIRED,
    }


class TestTheFakeExecutor:
    """The other half of the port, and the reason the port is worth having."""

    def test_it_is_interchangeable_with_the_real_adapter(self) -> None:
        assert isinstance(FakeTradeExecutor(), TradeExecutor)

    def test_it_records_what_it_was_asked(self) -> None:
        fake = FakeTradeExecutor()
        request = _request()
        fake.submit(request)
        assert fake.submitted == [request]

    def test_it_defaults_to_a_dry_run(self) -> None:
        # The safe direction. A fake that answered ACCEPTED unless configured would
        # make a suite in which somebody forgot to configure something look green.
        assert FakeTradeExecutor().submit(_request()).is_dry_run

    def test_it_can_be_told_to_accept(self) -> None:
        fake = FakeTradeExecutor(
            result=ExecutionResult(signal_id="x", status=ExecutionResult.STATUS_ACCEPTED)
        )
        assert fake.submit(_request()).is_accepted

    def test_it_can_be_told_to_fail(self) -> None:
        # The path that is easy to leave untested: the only place the pipeline meets
        # an adapter that does not behave.
        fake = FakeTradeExecutor(error=UnknownExecutionError("boom"))
        with pytest.raises(UnknownExecutionError):
            fake.submit(_request())
        assert fake.submitted == []


class TestTheRealPackageIfItIsInstalled:
    """Runs only where `auto_trade` is installed -- which, with the checkout
    editable-installed, is this machine.

    **This is the most valuable test in the file**, and it is worth being precise
    about why. Everything above it runs against doubles *shaped like* the upstream
    classes, so it proves the adapter is internally consistent. It cannot prove the
    shapes are right. This class can, because it calls the real
    `ExecutionWorkflow`, the real `RiskEngine` and a real
    `DryRunTerminalAdapter` -- and a dry run means no terminal is opened and no
    final control can be reached, which is asserted here rather than assumed,
    because that assumption *is* the project's safety model.

    So the earlier class that claims an id `../../x` would be rejected is not
    checked against the real `validate_signal_id` unless this runs. Both are
    kept deliberately: the doubles pin behaviour everywhere, and this confirms the
    doubles were shaped correctly.
    """

    @staticmethod
    def _workflow(bindings: object, ledger_path: Path, *, max_volume: str = "0.10") -> object:
        from auto_trade.adapters.terminal import DryRunTerminalAdapter
        from auto_trade.application.ledger import JsonExecutionLedger
        from auto_trade.application.risk import RiskEngine
        from auto_trade.domain.models import ExecutionPolicy, RiskLimits

        return bindings.ExecutionWorkflow(  # type: ignore[attr-defined]
            adapter=DryRunTerminalAdapter(),
            risk_engine=RiskEngine(RiskLimits({"EURUSD"}, Decimal(max_volume), 5, 10)),
            profile=executor_module.SENTINEL_PROFILE,
            policy=ExecutionPolicy(dry_run=True),
            kill_switch=bindings.KillSwitch(),  # type: ignore[attr-defined]
            audit=lambda event: None,
            ledger=JsonExecutionLedger(ledger_path),
            # `now` is required for a test to be deterministic. Upstream's risk
            # engine compares the signal's expiration against the wall clock, so a
            # workflow built without one judges the injected-clock signal from
            # 2026-10-01 against today's date and refuses it as expired.
            #
            # Found by this file, and it is a composition-root fact rather than an
            # adapter bug: `now` is one of the eight constructor arguments and the
            # adapter deliberately does not build the workflow, so wiring it is
            # whoever wires it.
            now=lambda: NOW,
        )

    def _bindings_or_skip(self) -> object:
        if find_spec("auto_trade") is None:
            pytest.skip("auto_trade is not installed on this machine")
        return load_bindings()

    def test_the_adapter_drives_the_real_workflow(self, tmp_path: Path) -> None:
        bindings = self._bindings_or_skip()
        executor = AutoTradeExecutor(
            self._workflow(bindings, tmp_path / "idempotency.json"),
            bindings=bindings,  # type: ignore[arg-type]
            now=lambda: NOW,
        )
        # Volume 0.10 is the limit, not over it: the real RiskEngine refuses
        # anything larger, and that refusal is asserted separately below rather than
        # being allowed to spoil this one.
        result = executor.submit(_request(volume=Decimal("0.10")))

        assert result.is_dry_run
        assert result.status == "DRY_RUN"
        assert "final execution control not used" in result.message
        assert result.evidence["upstream_status"] == "DRY_RUN"

    def test_the_real_risk_engine_refuses_an_oversized_order(self, tmp_path: Path) -> None:
        # The safety envelope is live, not decorative. If this ever stops refusing,
        # the bridge's own sizing is being checked by something that is not there.
        bindings = self._bindings_or_skip()
        executor = AutoTradeExecutor(
            self._workflow(bindings, tmp_path / "idempotency.json"),
            bindings=bindings,  # type: ignore[arg-type]
            now=lambda: NOW,
        )
        result = executor.submit(_request(volume=Decimal("0.12")))
        assert result.is_rejected
        assert "volume exceeds configured limit" in result.message

    def test_the_real_workflow_refuses_a_second_identical_signal(self, tmp_path: Path) -> None:
        # Idempotency, upstream's half of it. The bridge will add its own ledger in
        # Phase 9; this proves the upstream one is already in the path the adapter
        # calls, so the two will not be two separate stories.
        bindings = self._bindings_or_skip()
        executor = AutoTradeExecutor(
            self._workflow(bindings, tmp_path / "idempotency.json"),
            bindings=bindings,  # type: ignore[arg-type]
            now=lambda: NOW,
        )
        first = executor.submit(_request(volume=Decimal("0.10")))
        second = executor.submit(_request(volume=Decimal("0.10")))
        assert first.is_dry_run
        assert second.is_rejected
        assert "duplicate signal id" in second.message

    def test_the_real_signal_id_validator_accepts_what_the_bridge_sends(self) -> None:
        # The claim in rule 9's neighbourhood: stb-<32 hex> satisfies upstream's
        # filename rule. Asserted against the real validator, not the copied pattern.
        self._bindings_or_skip()
        from auto_trade.domain.models import validate_signal_id

        signal_id = _request().signal_id
        assert validate_signal_id(signal_id) == signal_id
        # And the rule it exists for still bites.
        with pytest.raises(Exception, match="file name"):
            validate_signal_id("../../x")

    def test_the_real_closed_status_is_mapped_to_unknown(self) -> None:
        # Upstream's sixth status, with the real enum member rather than a stand-in.
        bindings = self._bindings_or_skip()
        closed = bindings.ExecutionStatus.CLOSED  # type: ignore[attr-defined]
        assert str(closed) == "CLOSED"
        assert closed.value not in ExecutionResult.KNOWN_STATUSES

    def test_the_real_bindings_surface_is_verified(self) -> None:
        bindings = self._bindings_or_skip()
        assert bindings.verify() is bindings
