"""The whole layering, checked -- not just the domain's half of it.

`test_domain_isolation.py` is the most important test in this project and it checks
exactly one rule: the domain imports nothing from any other layer. That rule is the
one that would break the test suite on a machine without the private upstream
repositories, so it is the one worth being certain about.

**Everything else was documented and unenforced.** `docs/architecture.md` §5.1 says
"dependency direction is strictly inward" and then draws a diagram listing five
layers. The code has eight. `configuration/`, `infrastructure/logging/`,
`composition.py`, `live.py` and `cli/` are all absent from that diagram, and three
of the edges that do exist were never in anyone's head -- including
`application -> infrastructure.logging`, which is a real dependency on a concrete
implementation and is discussed below.

This module makes the diagram true, or makes the diagram change. It is the Phase 13
deliverable that matters, because a written rule nobody checks is a comment, and
this project's own experience in Phase 12 was that five defects survived 989 passing
tests because the tests were asserting the wrong things.

The one deliberate exception is named in :data:`LAYERS` and explained in
:func:`test_the_logging_exception_is_kept_small`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT

PACKAGE = "signal_to_trade_bridge"
PACKAGE_ROOT = PROJECT_ROOT / "src" / PACKAGE

#: The layers as built, and what each may import. Read this as a table of
#: permissions, not a suggestion: a layer absent from this mapping may not be
#: imported by anything, which is what makes adding a layer a deliberate act.
#:
#: The permissions are not symmetric and not "inward". They are what the code
#: actually does, checked so that it keeps doing it.
LAYERS: dict[str, frozenset[str]] = {
    # The pure core. Imports nothing from this project at all -- enforced by
    # test_domain_isolation.py, restated here so this table is readable alone.
    "domain": frozenset(),
    # Protocols. Knows the domain's value objects, because a Protocol that
    # returned a dict would push the translation into every implementation.
    "ports": frozenset({"domain"}),
    # Configuration. Knows the domain because `BridgeConfig` builds
    # `RiskParameters`; the alternative is a config that cannot be typed.
    "configuration": frozenset({"domain"}),
    # The orchestrator. Talks to the outside world only through ports.
    #
    # **`infrastructure` is the one deliberate exception, and it is listed here
    # rather than in a branch inside the test** so that reading this table is enough
    # to know every direction the code is allowed to take. The reasoning is in
    # TestTheOneDeliberateException below; the short version is that logging is a
    # vocabulary, not a capability, and a Protocol with one implementation and no
    # second candidate is ceremony rather than inversion.
    "application": frozenset({"domain", "ports", "configuration", "application", "infrastructure"}),
    # Cross-cutting. Knows only itself.
    "infrastructure": frozenset(),
    # Adapters implement ports and translate. May reach the application layer for
    # the dry-run report type, which is the one direction that looks wrong and is
    # not: the report is a value the preflight returns to the pipeline, not a
    # behaviour it invokes.
    "adapters": frozenset(
        {
            "domain",
            "ports",
            "application",
            "configuration",
            "adapters",
            # Same logging exception as `application`, for the same reason: an
            # adapter that translates somebody else's plan needs to be able to say
            # what it did, and `Event` is a vocabulary rather than a service.
            "infrastructure",
        }
    ),
    # The CLI. Talks to the composition root, not to adapters directly, so that
    # `doctor` and `check` exercise the same wiring a library consumer gets.
    "cli": frozenset({"domain", "ports", "application", "configuration", "cli"}),
}

#: The package root, where the composition root and the live assembly live. These
#: two are *supposed* to see everything: their entire job is to know every
#: collaborator. They are the reason the graph is not a strict lattice.
ROOT_LAYER = "(root)"

#: Modules at the root that are allowed to be imported by something other than the
#: CLI and the package's own ``__init__``.
#:
#: **`composition` is the important one.** If `application.process_signal` imported
#: it, the pipeline would be assembling its own collaborators -- the dependency
#: inversion this whole architecture exists to prevent -- and every other check in
#: this file would still pass, because the offending edge points *outward* and the
#: domain rule does not care. It is listed here rather than left to code review
#: because it is the single most consequential direction in the project.
ENTRY_POINTS: frozenset[str] = frozenset({"composition", "live"})


def _module_name(path: Path) -> str:
    """`.../domain/models.py` -> `domain.models`, and `.../domain/__init__.py` -> `domain`."""
    rel = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer_of(module: str) -> str:
    if not module:
        return ROOT_LAYER
    top = module.split(".")[0]
    return top if top in LAYERS else ROOT_LAYER


def _package_modules() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py"))


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """Absolute imports of this project's own package, as ``(lineno, target)``.

    Relative imports are resolved against the importing module rather than skipped.
    Skipping them would leave a hole: a relative import is the shortest way to reach
    a sibling module, and a test that only understood absolute imports would call
    this layer clean while `from ..ports import TradeExecutor` went unnoticed.
    """
    module = _module_name(path)
    package = module.rsplit(".", 1)[0] if "." in module else ""
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                # level 1 is the current package, 2 its parent, and so on.
                anchor = package.split(".")
                up = node.level - 1
                base = ".".join(anchor[: len(anchor) - up]) if up else anchor
                if node.module:
                    base = [*base, node.module]
                found.append((node.lineno, ".".join(base)))
            elif node.module and node.module.startswith(PACKAGE):
                found.append((node.lineno, node.module[len(PACKAGE) + 1 :]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE + "."):
                    found.append((node.lineno, alias.name[len(PACKAGE) + 1 :]))
    return found


def _known_modules() -> set[str]:
    names = {_module_name(p) for p in _package_modules()}
    return {n for n in names if n}


def _edges() -> list[tuple[str, int, str]]:
    """Every ``(source module, lineno, target module)`` edge inside the package."""
    known = _known_modules()
    out: list[tuple[str, int, str]] = []
    for path in _package_modules():
        source = _module_name(path)
        for lineno, target in _imported_modules(path):
            # An `import a.b.C` names a.b.C, and `from a.b import C` names a.b.
            # Walk up until something real matches.
            parts = target.split(".")
            resolved = None
            while parts:
                candidate = ".".join(parts)
                if candidate in known:
                    resolved = candidate
                    break
                parts = parts[:-1]
            if resolved is not None and resolved != source:
                out.append((source, lineno, resolved))
    return out


EDGES = _edges()


class TestTheLayering:
    def test_the_graph_was_actually_built(self) -> None:
        # **The check that everything else here depends on, and the one that was
        # missing when this file was first written.** The script this module came
        # from keyed modules without the package prefix while the imports carried
        # it, so nothing resolved, the edge list came out empty, and the violation
        # report printed "none" -- a clean bill of health for a graph that had
        # never been built. A test that cannot fail is indistinguishable from a test
        # that passes, and an architecture test that cannot fail is worse than no
        # architecture test, because it is trusted.
        assert len(EDGES) > 50, (
            f"only {len(EDGES)} import edges were found. Either the package moved or "
            f"the edge builder is broken, and every assertion below is vacuous "
            f"without edges to check."
        )
        # And the graph must be non-trivial in *shape*, not just in size.
        layers_reached = {_layer_of(t) for _s, _l, t in EDGES}
        assert len(layers_reached) >= 5, f"only reached {layers_reached}"

    @pytest.mark.parametrize(
        ("source", "lineno", "target"),
        EDGES,
        ids=[f"{s.split('.')[-1]}:{ln}->{t.split('.')[-1]}" for s, ln, t in EDGES],
    )
    def test_no_layer_imports_one_it_may_not(self, source: str, lineno: int, target: str) -> None:
        here = _layer_of(source)
        there = _layer_of(target)
        if here == ROOT_LAYER or there == ROOT_LAYER:
            return  # covered by the two tests below, which are about root modules
        allowed = LAYERS[here] | {here}
        if there in allowed:
            return
        path = next(p for p in _package_modules() if _module_name(p) == source)
        pytest.fail(
            f"{path.relative_to(PROJECT_ROOT)}:{lineno} imports {target}\n"
            f"  {here} may import {sorted(allowed)}, and {there} is not among them.\n"
            f"  If this dependency is genuinely required, it is an architecture change: "
            f"add the layer to LAYERS in this file and to the diagram in "
            f"docs/architecture.md 5.1, and say why in the commit message."
        )

    def test_only_entry_points_import_the_composition_root(self) -> None:
        # The one that would be most damaging and is easiest to do by accident: an
        # adapter or the pipeline importing `composition` to get a collaborator.
        offenders: list[str] = []
        for source, lineno, target in EDGES:
            if target not in ENTRY_POINTS:
                continue
            if _layer_of(source) in {ROOT_LAYER, "cli"}:
                continue
            offenders.append(f"{source}:{lineno} -> {target}")
        assert not offenders, (
            "only the CLI and the package root may import the composition root or "
            f"the live assembly. Found: {offenders}"
        )

    def test_the_domain_layer_is_checked_here_too(self) -> None:
        # `test_domain_isolation.py` is the real check and runs first. This one
        # exists so that this table cannot be edited to permit a domain import
        # without the change being obvious at the point of the edit.
        assert LAYERS["domain"] == frozenset()
        offenders = [
            f"{s}:{ln} -> {t}"
            for s, ln, t in EDGES
            if _layer_of(s) == "domain" and _layer_of(t) != "domain"
        ]
        assert not offenders, offenders


class TestTheOneDeliberateException:
    """`application -> infrastructure.logging`, named rather than hidden.

    The application layer imports `Event`, `StructuredLogger` and `get_logger` from
    `infrastructure.logging`. That is a dependency on a **concrete implementation**
    from the orchestrator, which is exactly what the port pattern exists to avoid,
    and `ports/` has no logging Protocol to make it legitimate.

    It is being kept, and the reason is worth stating rather than discovering later:
    logging is not a business capability. `Event` is a vocabulary, not a service --
    the same category as `RejectionReason`, which the domain is perfectly entitled to
    define. Putting a Protocol around it would add an interface with one
    implementation and no second candidate, which is the dependency-inversion
    equivalent of ceremony.

    What is **not** acceptable is the same import used to reach
    `configure_logging`, which mutates global handler state. Application code that
    reconfigures logging is the leak this exception must not become, so it is
    forbidden by name.
    """

    FORBIDDEN_FROM_ABOVE = frozenset({"configure_logging", "REDACTED", "SENSITIVE_KEYS"})

    def test_the_logging_exception_is_kept_small(self) -> None:
        offenders: list[str] = []
        for source, lineno, target in EDGES:
            if _layer_of(source) in {ROOT_LAYER, "infrastructure"}:
                continue
            if _layer_of(target) != "infrastructure":
                continue
            for name in self.FORBIDDEN_FROM_ABOVE:
                if _imports_name(source, lineno, target, name):
                    offenders.append(f"{source}:{lineno} imports {name}")
        assert not offenders, (
            "application and adapter code may log, but they may not configure logging "
            f"or reach into the redaction internals: {offenders}"
        )

    def test_the_logging_layer_itself_imports_nothing_out(self) -> None:
        # The other half of keeping the exception honest. If `infrastructure` grew a
        # dependency on an adapter, the exception would be a doorway rather than a
        # hole punched in one wall.
        offenders = [
            f"{s}:{ln} -> {t}"
            for s, ln, t in EDGES
            if _layer_of(s) == "infrastructure"
            and _layer_of(t) not in {ROOT_LAYER, "infrastructure"}
        ]
        assert not offenders, offenders


def _imports_name(source: str, lineno: int, target: str, name: str) -> bool:
    """Whether the statement at ``lineno`` in ``source`` pulls ``name`` from ``target``."""
    path = next(p for p in _package_modules() if _module_name(p) == source)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.lineno == lineno:
            module = node.module or ""
            if not module.startswith(PACKAGE):
                continue
            tail = module[len(PACKAGE) + 1 :]
            if tail == target or target.startswith(f"{tail}."):
                return any(alias.name == name for alias in node.names)
    return False


class TestEveryLayerIsReachable:
    def test_no_module_declares_a_layer_that_does_not_exist(self) -> None:
        # A layer in LAYERS with no module under it is a permission granted to
        # nothing, which is a way to spell a rule without enforcing it.
        present = {_layer_of(_module_name(p)) for p in _package_modules()}
        for layer in LAYERS:
            assert layer in present, f"LAYERS names {layer}, which has no modules"

    def test_every_layer_on_disk_is_in_the_table(self) -> None:
        # The other direction, and the one that catches drift. A new top-level
        # package that nobody added to LAYERS is a layer whose imports are
        # unchecked, which is the same as no rule at all.
        on_disk = {
            p.relative_to(PACKAGE_ROOT).parts[0]
            for p in _package_modules()
            if len(p.relative_to(PACKAGE_ROOT).parts) > 1
        }
        undeclared = on_disk - set(LAYERS)
        assert not undeclared, (
            f"{undeclared} exist on disk but are not in LAYERS, so nothing checks what "
            f"they may import. Add them with their permissions, or move them under a "
            f"layer that is already declared."
        )
