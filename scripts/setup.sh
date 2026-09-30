#!/usr/bin/env bash
#
# One-time setup for the Signal-to-Trade Bridge on Linux or macOS.
#
# The Windows equivalent is scripts/setup.ps1. The two do the same four things:
# locate Python, locate the two upstream checkouts, rewrite the local path
# dependencies in pyproject.toml, and install into a virtual environment.
#
# No path is committed. The locations come from ALBROOKS_PATH and AUTO_TRADE_PATH,
# and that is where a per-machine fact belongs.
#
# Usage:
#   export ALBROOKS_PATH="$HOME/source/al-brooks-price-action-engine"
#   export AUTO_TRADE_PATH="$HOME/source/auto-trade"
#   ./scripts/setup.sh
#
# Note: the MetaTrader 5 adapter needs the official Windows bindings and cannot
# work here at all. The domain, the application layer and the entire unit test
# suite are platform-independent and run fine on Linux -- which is the point of
# keeping the domain free of third-party dependencies.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pyproject="$repo_root/pyproject.toml"

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

# -- 1. Python ----------------------------------------------------------------

step 'Checking Python'

if ! command -v python3 >/dev/null 2>&1; then
    fail 'python3 not found. Install Python 3.11 or newer.'
fi

python_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
printf 'Found python3 (%s)\n' "$python_version"

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    fail "Python 3.11 or newer is required (auto-trade needs >=3.11). Found $python_version."
fi

# -- 2. Upstream projects -----------------------------------------------------

step 'Locating the upstream projects'

albrooks_path="${ALBROOKS_PATH:-$HOME/source/al-brooks-price-action-engine}"
auto_trade_path="${AUTO_TRADE_PATH:-$HOME/source/auto-trade}"

check_upstream() {
    local name="$1" path="$2"
    if [ ! -d "$path" ]; then
        fail "$name not found at '$path'.

  Clone it:
    git clone git@github.com:ybagheri/$name.git \"$path\"

  Then set the location and re-run:
    export $(basename "$name" | tr 'a-z-' 'A-Z_')='<the path you cloned to>'"
    fi
    if [ ! -f "$path/pyproject.toml" ]; then
        fail "'$path' does not look like $name: no pyproject.toml found."
    fi
    printf '  %s: %s\n' "$name" "$path"
}

check_upstream 'al-brooks-price-action-engine' "$albrooks_path"
check_upstream 'auto-trade' "$auto_trade_path"

# -- 3. Rewrite the local path dependencies ------------------------------------

step 'Pointing pyproject.toml at the local checkouts'

# A PEP 508 direct reference needs a file:/// URI. On Windows the path would also
# need forward slashes, which is why setup.ps1 does the same substitution.
albrooks_uri="file://${albrooks_path}"
auto_trade_uri="file://${auto_trade_path}"

if ! grep -q '^albrooks = \[' "$pyproject"; then
    fail 'Could not find the albrooks extra in pyproject.toml to rewrite.'
fi

python3 - "$pyproject" "$albrooks_uri" "$auto_trade_uri" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = re.sub(
    r'(?m)^albrooks = \[.*\]$',
    f'albrooks = ["albrooks @ {sys.argv[2]}"]',
    text,
)
text = re.sub(
    r'(?m)^auto-trade = \[.*\]$',
    f'auto-trade = ["auto-trade @ {sys.argv[3]}"]',
    text,
)
path.write_text(text, encoding="utf-8")
print(f"  albrooks   -> {sys.argv[2]}")
print(f"  auto-trade -> {sys.argv[3]}")
PY

# -- 4. Virtual environment ---------------------------------------------------

step 'Creating the virtual environment'

venv_path="$repo_root/.venv"
if [ -d "$venv_path" ]; then
    printf '  .venv already exists; reusing it.\n'
else
    python3 -m venv "$venv_path"
fi

venv_python="$venv_path/bin/python"
[ -x "$venv_python" ] || fail 'Could not find the virtual environment interpreter.'

# -- 5. Install ---------------------------------------------------------------

step 'Installing the bridge and its development dependencies'
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -e "$repo_root[dev]"

step 'Installing the upstream projects (editable)'
"$venv_python" -m pip install -e "$albrooks_path"
"$venv_python" -m pip install -e "$auto_trade_path"

# -- 6. Verify ----------------------------------------------------------------

step 'Verifying'
"$venv_python" - <<'PY'
import signal_to_trade_bridge as stb
import albrooks
import auto_trade

print(f"  signal-to-trade-bridge {stb.__version__}")
print(f"  albrooks {albrooks.__version__}")
print(f"  auto-trade {auto_trade.__version__}")
PY

"$venv_python" -m pytest -q

printf '\nSetup complete.\n'
printf '\n  Activate:      source .venv/bin/activate\n'
printf '  Run the tests:  .venv/bin/python -m pytest\n'
printf '\n  Note: the MetaTrader 5 adapter needs the official Windows bindings and\n'
printf '  cannot run on this platform. The domain and the unit tests can.\n'
