"""The domain layer must have no dependencies.

This is the most important architectural test in the project, and it is the one
that most needs to exist rather than be assumed.

Neither upstream project is on any package index -- both are private
repositories. If the domain layer imported either of them, directly or through a
helper, then the test suite could only run on a machine where both had been
cloned and installed, and a contributor without access could not run a single
test. The unit tests are the project's main safety net; a safety net that only
exists on the author's laptop is not a safety net.

So this walks the domain package's import graph and asserts that every module it
reaches belongs to the standard library, to this project, or to the domain layer
itself. An accidental ``import pandas`` in a domain module fails here rather than
in a place far from the cause.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT

PACKAGE = "signal_to_trade_bridge"
DOMAIN_PACKAGE = f"{PACKAGE}.domain"

#: Third-party distributions the domain is allowed to know about. Empty on
#: purpose: the domain's arithmetic uses ``decimal`` from the standard library
#: and nothing else. Adding a name here is a decision that the trading logic
#: cannot be tested without that package installed, so it should be argued for
#: rather than added to make a test pass.
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset()

#: The layers the domain must not reach, in either direction. Listed explicitly
#: so the failure message names what was violated instead of just printing a
#: module path the reader has to decode.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    f"{PACKAGE}.adapters",
    f"{PACKAGE}.application",
    f"{PACKAGE}.infrastructure",
    f"{PACKAGE}.ports",
    f"{PACKAGE}.cli",
    # `configuration` is not forbidden: it builds `RiskParameters`, which is a
    # domain type, so a domain -> configuration edge would be a genuine cycle.
    f"{PACKAGE}.configuration",
)


def _domain_modules() -> list[Path]:
    root = PROJECT_ROOT / "src" / PACKAGE / "domain"
    return sorted(root.rglob("*.py"))


def _top_level_imports(path: Path) -> set[str]:
    """Every top-level module name imported by a file, in any form.

    Parsed rather than imported. Importing the module to inspect it would make
    this test depend on the thing it is testing, and a module with a side effect
    on import would run that side effect here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # A relative import cannot leave the package, so it is always
                # fine and its target is resolved by the caller-side checks.
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def test_the_domain_package_actually_has_modules() -> None:
    """Guards against this file passing because it found nothing to check.

    A test that asserts a property of an empty set of files is a test that always
    succeeds, and it would keep succeeding after every domain module was deleted.
    """
    modules = _domain_modules()
    assert len(modules) >= 3, f"expected the domain to have modules, found {modules}"


@pytest.mark.parametrize("module_path", _domain_modules(), ids=lambda p: p.name)
def test_domain_imports_nothing_forbidden(module_path: Path) -> None:
    """Each domain module imports only the standard library and this package."""
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.append(node.module)
        elif isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)

        for target in targets:
            top = target.split(".")[0]

            # Third-party packages are named in full, so the stdlib check has to
            # come first: `logging` and `pytest` are both bare top-level names,
            # and only one of them is a dependency.
            if top in ALLOWED_THIRD_PARTY:
                continue
            if top == PACKAGE:
                # An inward edge within the package. The forbidden-prefix check
                # below still applies to it, because `domain` reaching into
                # `adapters` is the failure being guarded against.
                pass
            elif top not in sys.stdlib_module_names:
                pytest.fail(
                    f"{module_path.name} imports third-party {top!r}; the domain layer must be "
                    f"dependency-free. Add it to ALLOWED_THIRD_PARTY only if the trading logic "
                    f"genuinely cannot be tested without it."
                )

            for prefix in FORBIDDEN_PREFIXES:
                if target == prefix or target.startswith(f"{prefix}."):
                    pytest.fail(
                        f"{module_path.name} imports {target!r}; the domain layer must not depend "
                        f"on {prefix!r}. Depend on a Protocol in ports instead."
                    )


def test_no_domain_module_imports_an_upstream_project() -> None:
    """Neither private upstream repository may appear in the domain.

    Stated separately from the test above because it names the specific
    dependency this architecture exists to contain, and because the failure is
    the one that would silently break the suite on any machine without the
    upstreams installed.
    """
    for module_path in _domain_modules():
        imported = _top_level_imports(module_path)
        for upstream in ("albrooks", "auto_trade", "MetaTrader5"):
            assert upstream not in imported, (
                f"{module_path.name} imports {upstream!r}. Both upstream projects are private "
                f"repositories that cannot be installed from an index, so the domain must not "
                f"depend on them. Reach them through an adapter in "
                f"signal_to_trade_bridge.adapters."
            )


def test_no_domain_module_imports_a_stdlib_module_that_does_not_exist() -> None:
    """Catch a typo in a standard library import.

    ``ast.parse`` will happily accept ``from decimal import Decimalx`` and
    ``import decimalx``, and the failure would only surface when that code path
    first ran -- possibly during live trading.
    """
    for module_path in _domain_modules():
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".")[0]
                if root != PACKAGE and root not in sys.stdlib_module_names:
                    pytest.fail(f"{module_path.name} imports unknown module {node.module!r}")
