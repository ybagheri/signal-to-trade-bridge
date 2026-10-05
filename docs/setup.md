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
BRIDGE_MT5_TERMINAL_PATH=C:\Program Files\<Broker> MT5\terminal64.exe
BRIDGE_MT5_DATA_PATH=C:\Users\You\AppData\Roaming\MetaQuotes\Terminal\0123456789ABCDEF0123456789ABCDEF
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

  MetaTrader5        ok
  auto_trade         ok
  terminal           C:\Program Files\<Broker> MT5\terminal64.exe
  data directory     C:\Users\You\AppData\Roaming\MetaQuotes\Terminal\0123456789ABCDEF...
  control ids        ok
    build            6230 (ids measured on 6230)
```

The `cannot run here` block is the answer when there is one, and it comes first on
purpose — the `ok` / `REFUSED` marks below it are corroboration rather than the only
signal. If a path you configured is wrong, it is named there:

```
  cannot run here
    - no terminal at C:\Program Files\<Broker> MT5\terminal64.exe
```

**`control ids: ok` means the identifiers were measured on this build**, not that
they are probably fine. A control identifier is a position in a window, and MT5
moves controls between builds, so the number in brackets is the only thing that
makes them usable. §8 explains how it got there and how to refresh it.

Note that `control ids: ok` does **not** mean the bridge can trade. It is one gate
of several; `config` shows the two that matter most:

```json
"dry_run": true,
"execution_enabled": false
```

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

### What is skipped, and why

On a machine without the private projects or a terminal, the default run reports
something like `1184 passed, 84 skipped`. That is the expected, clean state, not a
partial failure:

| Skipped because | Mechanism | To run them |
|---|---|---|
| the private `auto_trade` package is not importable | `@pytest.mark.requires_auto_trade` | set `AUTO_TRADE_PATH`, run `scripts/setup.*` |
| `albrooks` is not installed | `pytest.importorskip` | set `ALBROOKS_PATH`, run `scripts/setup.*` |
| the live MT5 tests are opt-in | `BRIDGE_ALLOW_MT5_TESTS=1` | also set the `BRIDGE_TEST_*` variables in `.env.example` |

Every skip carries its reason in the `-rs` summary. The live tests read the terminal
path, data folder and demo login from the environment and **never from the
repository**: an earlier revision hard-coded one machine's install path, profile name
and demo account, which made those tests fail everywhere else and put personal
details in version control.

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

## 8. The control identifiers, and why they are measured

To place an order, `auto-trade` drives the MetaTrader order dialog by clicking
buttons. It therefore needs to *find* those buttons, and it does so by an identifier
that belongs to one build of one terminal on one display.

That is not a stable name. MT5 adds and removes controls between builds, so an
identifier measured on one build can address a different control, or nothing, on
another. Upstream's own rule:

> Never substitute a control identifier you have not measured. A control found once
> is a control whose behaviour is not established. If a build presents something
> different, refuse and report it — do not wire it up.

So the bridge reads the terminal's build from two independent sources, requires them
to agree, and refuses unless the build is the one the identifiers were measured on.

```powershell
python scripts/control_ids.py
```

```
Known Issue 5 -- control identifiers and the terminal build

  terminal64.exe FileVersion         6230
  published position snapshot        6230

  build confirmed as 6230 from 2 independent source(s)

  OK -- the build matches.
```

**If the build changes, that command exits 1 and explains the gap.** That refusal is
the system working: a build that silently accepted a stale identifier would be a
system that clicks a position-size field and does not know it.

### Re-measuring after an MT5 update

The measurement is `auto-trade terminal-check`, which is upstream's own read-only
probe. It opens the order dialog, reads the control tree, reports every expected
identifier as OK / DRIFTED / MISSING, and closes the dialog again. Nothing is typed
into it and no final control — Buy, Sell, OK, Close — is ever clicked.

```powershell
auto-trade terminal-check
```

```
  "verdict": "OK",
  "checked": 13,
  "drifted": [],
  "missing": [],
```

The report is written to `logs/terminal_check.json`, and `auto-trade
terminal-check --compare` will later tell you whether a verdict ever changed, which
is the question an update actually raises.

**Only move the measured build if that report says OK for every control.** On this
machine it did, on build 6230, which is why `control ids: ok` appears in §5:

| control | measured | found |
|---|---|---|
| `symbol` | 10325 | 10325 |
| `volume` | 10333 | 10333 |
| `stop_loss` | 10334 | 10334 |
| `take_profit` | 10336 | 10336 |
| `final_control_buy` | 10408 | 10408 |
| `final_control_sell` | 10409 | 10409 |
| `trade_grid` | 10328 | 10328 |

A **DRIFTED** or **MISSING** row means a control is no longer where it was measured,
and the constant must not move. `tests/unit/test_control_id_evidence.py` enforces
this: it fails if `MEASURED_ON_BUILD` names a build that no recorded probe reports,
or if the latest recorded probe found anything drifted or missing. **The number
cannot be edited without producing the evidence for it**, which is the difference
between a measurement and a constant somebody changed to quiet a warning.

`scripts/control_ids.py` reads `AUTO_TRADE_PATH`, `BRIDGE_MT5_TERMINAL_PATH` and
`BRIDGE_MT5_DATA_PATH`; if any is unset it exits `2` and names it, rather than
guessing a path.

### The terminal's trading mode is a gate too

Matching control identifiers say the order dialog is where it was measured. They do
**not** say which values a click will send. With **Algo Trading off**, the terminal
sends the Toolbox *Trade panel's* volume and no stop loss or take profit, whatever the
order ticket holds. The executor therefore reads `terminal_info().trade_allowed`
before it clicks and **refuses any order carrying a stop or target** unless that flag
is exactly `True`; an unreadable terminal reads as "not allowed". Turning Algo
Trading on is your decision, taken by hand in the toolbar, and nothing in this
project does it for you. (`account_info().trade_mode` is not used: it is the account
*type* -- demo, contest, real -- and says nothing about Algo Trading.)

### This gate is not the only thing between you and an order

`control ids: ok` means the machine passed *one* gate. The default configuration
still cannot trade, and cannot be made to by this section:

| gate | default |
|---|---|
| control identifiers match the build | checked by `doctor` |
| `BRIDGE_EXECUTION_ENABLED` | `false` |
| `BRIDGE_DRY_RUN` | `true` |
| the ledger is readable | checked when the live side is assembled |
| the account is `DEMO` | checked when the live side is assembled |
| the kill switch is clear | checked when the live side is assembled |
| the terminal allows algo trading (`trade_allowed`) | checked by the executor immediately before the click |

And the one that is a code path rather than a setting: the default composition root,
`build_bridge()`, returns `can_execute=False` with no executor attached. Reaching an
order takes a deliberate `build_live()` call as well as the two variables.

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
