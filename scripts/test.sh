#!/usr/bin/env bash
#
# The full quality gate. Run this before every commit.
#
# The Windows equivalent is scripts/test.ps1. Both do the same five things.
#
# The domain isolation check is listed separately because it is the project's most
# important architectural invariant and the only gate that fails when someone adds
# an import rather than when they add a bug. A trade-decision layer that quietly
# grew a dependency on a private repository would still pass every other check
# here while making the test suite unrunnable on any other machine.
#
# Usage:
#   ./scripts/test.sh
#   ./scripts/test.sh --coverage

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'FAILED: %s\n' "$1" >&2; exit 1; }

# Prefer the project virtual environment, so the gate runs against the same
# interpreter the project was installed into. Falling back keeps the gate runnable
# on a machine that has not run setup yet, which beats a gate that cannot run.
if [ -x "$repo_root/.venv/bin/python" ]; then
    python_bin="$repo_root/.venv/bin/python"
else
    if ! command -v python3 >/dev/null 2>&1; then
        fail 'no Python found. Run scripts/setup.sh first.'
    fi
    python_bin="python3"
    printf 'Note: no .venv found, using the system Python.\n'
fi

cd "$repo_root"

step 'ruff check'
"$python_bin" -m ruff check . || fail 'ruff check'

step 'ruff format --check'
"$python_bin" -m ruff format --check . || fail 'ruff format --check'

step 'mypy'
"$python_bin" -m mypy || fail 'mypy'

if [ "${1:-}" = '--coverage' ]; then
    step 'pytest with coverage'
    "$python_bin" -m pytest --cov=signal_to_trade_bridge --cov-report=term-missing \
        || fail 'pytest'
else
    step 'pytest'
    "$python_bin" -m pytest -q || fail 'pytest'
fi

step 'domain isolation'
"$python_bin" -m pytest tests/unit/test_domain_isolation.py -v --no-header \
    || fail 'domain isolation'

printf '\nAll checks passed.\n'
