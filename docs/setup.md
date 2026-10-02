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

## 5. Use it from the command line

Installing the package puts one command on your `PATH`:

```powershell
signal-to-trade-bridge --version
```

The name comes from `[project.scripts]` in `pyproject.toml` and is the full one.
`python -m signal_to_trade_bridge` also works and needs no install, which is the
form to use when you are debugging the entry point itself.

### The four commands

| Command | What it does |
|---|---|
| `doctor` | Whether *this machine* can run the bridge, and which part is missing |
| `signal` | Prints a worked example signal, in the shape `auto-trade` reads |
| `check FILE` | Runs one signal file through the whole pipeline and prints the decision |
| `config` | Prints the effective configuration as JSON |

**No command here can place an order.** That is not a missing feature — the live
path needs control identifiers measured against your terminal's exact build, and
that is the subject of §9. `check` will happily size a trade; it will not send one.

Start with `doctor`, because it is the only command that is safe to run against a
machine you have not set up yet:

```powershell
signal-to-trade-bridge doctor
```

```
signal-to-trade-bridge
  version            0.1.0

  MetaTrader5        ok
  auto_trade         ok
  terminal           not configured
  control ids        not checked
```

It reports on the terminal without touching it. It never launches MetaTrader, never
reads the account, and never moves a mouse.

**If it cannot run, it says so first, before the detail.** That block is the answer;
the `ok` and `REFUSED` marks underneath are there so you can see which part of the
machine is the problem:

### The exit codes are the contract

This is the part worth reading, because it is what lets a shell script drive the
bridge without parsing prose:

| Code | Meaning |
|---|---|
| `0` | It did what it was asked. Includes a `DRY_RUN` — a sized trade that was not sent. |
| `1` | A refusal. The bridge worked and declined to trade. |
| `2` | A fault. Unreachable terminal, unreadable file, refused configuration. |
| `3` | `UNKNOWN` — and **must not be retried automatically**. |

`3` is separate from `1` on purpose. The bridge could not determine whether a
position exists. A caller that reads "refused" as "safe to try again" would resend
it, and the resend may open a second position. So a caller keying retry logic on
"did it work?" must treat `3` as terminal and page someone.

### A complete dry run

```powershell
signal-to-trade-bridge signal > sig.json
signal-to-trade-bridge check sig.json
```

```
signal   stb-example-buy
action   DRY_RUN
reason   PIPELINE_PASSED
because  every check passed and the trade is fully sized, and nothing was sent...

  symbol     EURUSD LONG
  volume     1.66
  entry      1.10000
  stop       1.09700  (0.00300)
  target     1.10300
  risk       499.9998
  planned    498.000
  ratio      1

  downstream gates
    evaluated  False
    accepted   False
    because    no downstream risk engine is wired into this dry run
    blockers   execution is not enabled (BRIDGE_EXECUTION_ENABLED); dry-run mode is on...
```

Two things in that output are worth pausing on.

**`downstream gates: evaluated False`** is not a clean run. The gates that decide
whether a sized trade may be sent have not run, because nothing is wired to them
yet. "No blockers" and "the blockers were never evaluated" are opposites, and the
report says which one you have.

**The `volume` is not a template number.** It was computed from your account
balance and the symbol's contract. That is why `signal` prints no volume and no
price: those are the bridge's to derive, and a plausible-looking `0.10` in a
template would be a number nobody computed.

> **Redirecting on Windows.** `signal > sig.json` from PowerShell writes UTF-16, not
> UTF-8, and a strict reader rejects it with `'utf-8' codec can't decode byte 0xff
> in position 0`. `check` reads UTF-8, UTF-8-with-BOM, UTF-16 and cp1252, so the
> documented command works as written on PowerShell. If you pipe the output into
> anything else, write it explicitly:
>
> ```powershell
> signal-to-trade-bridge signal | Out-File -Encoding utf8 sig.json
> ```

### Pointing it at your terminal

`doctor` reporting `terminal not configured` is the normal state on a fresh
install. Set the two paths in `.env`:

```dotenv
BRIDGE_MT5_TERMINAL_PATH=C:\Program Files\Alpari MT5_4\terminal64.exe
BRIDGE_MT5_DATA_PATH=C:\Users\You\AppData\Roaming\MetaQuotes\Terminal\1D9617E1A6A4352DBDC25D08FEC12BD2
```

`BRIDGE_MT5_DATA_PATH` is the terminal's data directory, not its install directory.
It is the long hex-named folder under `MetaQuotes\Terminal`, and it is not
guessable — a terminal has one per installation, and the name is a hash. The
bridge reads it rather than starting the terminal, so it has to be told.

Then:

```powershell
signal-to-trade-bridge doctor
```

```
signal-to-trade-bridge
  version            0.1.0

  cannot run here
    - the measured control identifiers do not belong to this terminal's build. The
      live path is refused, and no configuration clears it -- run
      python scripts/control_ids.py for the details.

  MetaTrader5        ok
  auto_trade         ok
  terminal           C:\Program Files\Alpari MT5_4\terminal64.exe
  data directory     C:\Users\You\AppData\Roaming\MetaQuotes\Terminal\1D9617E1A6A4352D...
  control ids        REFUSED
    build            6230 (ids measured on 6184)
    The measured control identifiers belong to build 6184 and this terminal is
    build 6230 -- a gap of 46 builds. Upstream's rule is explicit: never
    substitute a control identifier you have not measured, and if a build presents
    something different, refuse and report it.
```

The `cannot run here` block is the answer, and it comes first on purpose — the
`ok` / `REFUSED` marks below it are corroboration rather than the only signal. If a
path you configured is wrong, it is named there:

```
  cannot run here
    - no terminal at C:\Program Files\Alpari MT5_4\termnal64.exe
```

That `REFUSED` is the correct answer, and §8 explains why it cannot be configured
away.

### Configuration

```powershell
signal-to-trade-bridge config
```

Two lines in that output are the safety properties, and they are what the defaults
are for:

```json
"dry_run": true,
"execution_enabled": false
```

Nothing here is redacted, because nothing in `BridgeConfig` is a secret. A
redaction heuristic would have to guess which values look like credentials, and it
would either over-redact a risk percentage or under-redact a token — the second of
which is the failure that matters. If a secret is ever added to the configuration,
it is redacted **by field name**, at the point it is added.

A `--log-dir` flag overrides `BRIDGE_LOG_DIR` for one invocation, which is the
form to use when you want the ledger somewhere you can read it:

```powershell
signal-to-trade-bridge check sig.json --log-dir .\scratch-ledger
```

---

## 6. Run the tests

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

## 7. What works without MetaTrader 5

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

## 8. The control-identifier refusal

This is the one thing in the setup that no configuration fixes, so it gets its own
section rather than a line in the troubleshooting list.

To place an order, \uto-trade\ drives the MetaTrader order dialog by clicking
buttons. That means it needs the *screen coordinates* of those buttons. Screen
coordinates are not a property of the software; they are a property of one
installation, one display, one resolution, one window position and one build.

So the identifiers are **measured**, not derived. \scripts/control_ids.py\ measures
them by taking a screenshot of the real dialog and asking a human to confirm each
point. It then records the terminal build it measured against.

If the build changes, every measurement is void:

\\powershell
python scripts/control_ids.py
\
\REFUSED
  build    6230 (ids measured on 6184)
  The measured control identifiers belong to build 6184 and this terminal is
  build 6230 -- a gap of 46 builds. Upstream's rule is explicit: never substitute
  a control identifier you have not measured, and if a build presents something
  different, refuse and report it.
\
That refusal is the system working. A build that silently accepted a stale
identifier would be a system that clicks a position-size field and does not know
it.

**To clear it**, a human re-measures on the current build, at the current display
resolution, with the dialog open at the position it will be used from. It cannot be
done from a script, and that is the point — the alternative is guessing where a
button is before spending someone's money.

Until then \uild_live()\ refuses, \check\ still runs the full pipeline in dry
run, and nothing can be sent.

---

## 9. Troubleshooting

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
