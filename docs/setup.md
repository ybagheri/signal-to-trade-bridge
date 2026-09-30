# Setup

Getting the bridge running on a laptop. Windows first, because that is where
MetaTrader 5 runs; Linux and macOS are supported for everything except live
trading.

---

## 1. Requirements

| Requirement | Why |
|---|---|
| **Python 3.11+** | `auto-trade` requires `>=3.11`. `albrooks` requires only `>=3.10`, so 3.11 is the binding floor and not a preference. |
| **Windows 10/11** | Only for anything touching MetaTrader 5. The domain layer, the application layer and the entire unit test suite are platform-independent. |
| **Git with SSH access** | Both upstream projects are private repositories. |
| **MetaTrader 5 terminal** | Only for Phase 11 onwards. Must already be running and logged in — the bridge never launches it. |

Check your Python:

```powershell
python --version
```

---

## 2. Clone the three repositories

```powershell
git clone git@github.com:ybagheri/signal-to-trade-bridge.git
git clone git@github.com:ybagheri/al-brooks-price-action-engine.git
git clone git@github.com:ybagheri/auto-trade.git
```

Anywhere you like. The bridge does not require them to be siblings, and it does
not require a particular drive.

> **Both upstream repositories are private and are not published to any package
> index.** That is why the setup below installs them from local checkouts. It is
> not a workaround for a broken release process; it is a consequence of them
> being proprietary.

---

## 3. Run the setup script

### Windows

```powershell
cd signal-to-trade-bridge

# Tell it where the upstream checkouts are. There are no sensible defaults that
# are correct on more than one machine, so the script uses these.
$env:ALBROOKS_PATH   = 'E:\al-brooks-price-action-engine'
$env:AUTO_TRADE_PATH = 'E:\auto-trade'

.\scripts\setup.ps1
```

### Linux and macOS

```bash
cd signal-to-trade-bridge

export ALBROOKS_PATH="$HOME/source/al-brooks-price-action-engine"
export AUTO_TRADE_PATH="$HOME/source/auto-trade"

./scripts/setup.sh
```

The script does four things:

1. verifies Python 3.11+
2. checks both upstream checkouts exist and look like the right projects
3. rewrites the two local-path extras in `pyproject.toml` to point at them
4. creates `.venv` and installs everything into it

Use `.\scripts\setup.ps1 -SkipInstall` to see what step 3 would change without
installing.

### On a machine where nothing lives on `E:\`

That is the normal case for every laptop but this one, and it needs nothing
special:

```powershell
$env:ALBROOKS_PATH   = "$HOME\source\al-brooks-price-action-engine"
$env:AUTO_TRADE_PATH = "$HOME\source\auto-trade"
```

**No drive letter is ever committed to the repository.** The paths live in your
shell's environment and in your `.env` file, which is exactly where a
per-machine fact belongs. If you find a hard-coded path in application logic, that
is a bug — report it.

---

## 4. Configure

```powershell
Copy-Item .env.example .env
notepad .env
```

The defaults are safe: the bridge cannot execute and it runs in dry-run mode. You
do not need to change anything to run the tests.

`.env` is gitignored. `.env.example` is committed, so **never put a real secret
in it**. The bridge does not need a broker password — see
[the note at the end of `.env.example](../.env.example).

---

## 5. Run the tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest
```

Or the full quality gate, which is what to run before every commit:

```powershell
.\scripts\test.ps1
```

That runs, in order:

| Step | What it catches |
|---|---|
| `ruff check` | unused imports, shadowed builtins, mutable defaults, unsorted imports |
| `ruff format --check` | formatting drift, which makes diffs unreadable |
| `mypy` | untyped definitions, wrong `Optional` handling, bad return types |
| `pytest` | behaviour |
| domain isolation | a domain module importing an adapter, a port, or a private upstream repository |

**The domain isolation check is the one that matters most architecturally.** Both
upstream projects are private and cannot be installed from an index, so if the
domain layer grew a dependency on either, the test suite would only run on a
machine that happened to have them. That check is what makes the safety net
portable, so it is run as its own step rather than being trusted to one test file.

---

## 6. What works without MetaTrader 5

| Component | Needs MT5? |
|---|---|
| `domain/` | no |
| `application/` | no |
| `ports/` | no |
| `infrastructure/logging/` | no |
| `configuration/` | no |
| `adapters/fake/` | no |
| `adapters/albrooks/` | no — needs `albrooks`, which is a plain Python package |
| `adapters/auto_trade/` | no to import; yes to actually place an order |
| `adapters/mt5/` | yes |

So a contributor without MetaTrader 5 installed can run the entire unit test
suite. Only the live adapter and Phase 11 need the terminal.

---

## 7. Troubleshooting

**`scripts/setup.ps1` says an upstream project was not found.**
Set `ALBROOKS_PATH` and `AUTO_TRADE_PATH` to wherever you cloned them. The
defaults are this machine's paths, which are correct nowhere else.

**`pip install albrooks[pythonpath]` fails trying to fetch a package named `src`.**
Do not install that extra. It is a defect in the upstream `pyproject.toml`: the
`pythonpath = ["src"]` key sits at the top level rather than inside
`[tool.pytest.ini_options]`, so setuptools parses it as an extra requirement
group. `scripts/setup.ps1` installs `albrooks` plainly, which is correct. See
`docs/integration.md` §1.13.

**`python -m venv` fails on an embeddable Python build.**
The embeddable distribution does not include `ensurepip`. Install a normal
Python release, or use an existing environment. Note that the embeddable build
also cannot be used to create a virtual environment reliably, which is a
property of that distribution rather than of this project.

**`MetaTrader5` will not install.**
It is a Windows-only wheel and needs a matching architecture. Skip it with
`.\scripts\setup.ps1 -SkipWindowsExtras` — the unit tests do not need it.

**The terminal is running but the bridge cannot read the account.**
Check in this order: the terminal is logged in (not just running); the data
directory in `.env` matches that terminal; the symbol is in Market Watch. The
bridge never launches or authenticates the terminal, by design.

**A test passes alone and fails in the suite.**
Almost always environment leakage. The configuration tests use the
`clean_environment` fixture, which snapshots and restores `os.environ`; a new
test that sets an environment variable without it will leak into whatever runs
next.
