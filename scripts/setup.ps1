<#
.SYNOPSIS
    One-time setup for the Signal-to-Trade Bridge on Windows.

.DESCRIPTION
    Installs the bridge in editable mode together with its two upstream projects.

    The upstream projects are private Git repositories and are not published to
    any package index, so they are installed from local checkouts as editable
    dependencies. This script reads their locations from the ALBROOKS_PATH and
    AUTO_TRADE_PATH environment variables, rewrites the two extras in
    pyproject.toml to match, and then installs.

    No drive letter is ever committed. The paths live in this script's
    environment and in your machine's .env file, which is exactly where a
    per-machine fact belongs.

.PARAMETER SkipInstall
    Rewrite the dependencies and create the virtual environment, but do not run
    pip install. Useful for inspecting what would change.

.EXAMPLE
    $env:ALBROOKS_PATH = 'E:\al-brooks-price-action-engine'
    $env:AUTO_TRADE_PATH = 'E:\auto-trade'
    .\scripts\setup.ps1

.EXAMPLE
    # Another laptop, nothing on E:
    $env:ALBROOKS_PATH = "$HOME\source\al-brooks-price-action-engine"
    $env:AUTO_TRADE_PATH = "$HOME\source\auto-trade"
    .\scripts\setup.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipWindowsExtras
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$pyproject = Join-Path $repoRoot 'pyproject.toml'

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Fail {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

# -- 1. Locate Python --------------------------------------------------------

Write-Step 'Checking Python'

$python = $null
foreach ($candidate in @('python', 'py')) {
    try {
        $version = & $candidate -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; $pythonVersion = $version; break }
    }
    catch { continue }
}

if (-not $python) {
    Write-Fail 'No Python found on PATH. Install Python 3.11 or newer and try again.'
}

Write-Host "Found $python ($pythonVersion)"

$major, $minor = $pythonVersion -split '\.'
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 11)) {
    Write-Fail "Python 3.11 or newer is required (auto-trade needs >=3.11). Found $pythonVersion."
}

# -- 2. Locate the upstream projects -----------------------------------------

Write-Step 'Locating the upstream projects'

$albrooksPath = $env:ALBROOKS_PATH
$autoTradePath = $env:AUTO_TRADE_PATH

if (-not $albrooksPath) { $albrooksPath = 'E:\al-brooks-price-action-engine' }
if (-not $autoTradePath) { $autoTradePath = 'E:\auto-trade' }

foreach ($pair in @(
        @{ Name = 'albrooks-price-action-engine'; Path = $albrooksPath; Env = 'ALBROOKS_PATH' },
        @{ Name = 'auto-trade'; Path = $autoTradePath; Env = 'AUTO_TRADE_PATH' }
    )) {
    if (-not (Test-Path -LiteralPath $pair.Path)) {
        Write-Fail @"
$($pair.Name) not found at '$($pair.Path)'.

  Clone it:
    git clone git@github.com:ybagheri/$($pair.Name).git "$($pair.Path)"

  Then set the location and re-run:
    `$env:$($pair.Env) = '<the path you cloned to>'
"@
    }
    $pyprojectFile = Join-Path $pair.Path 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $pyprojectFile)) {
        Write-Fail "'$($pair.Path)' does not look like $($pair.Name): no pyproject.toml found."
    }
    Write-Host "  $($pair.Name): $($pair.Path)"
}

# -- 3. Rewrite the local path dependencies -----------------------------------

Write-Step 'Pointing pyproject.toml at the local checkouts'

$content = Get-Content -LiteralPath $pyproject -Raw -Encoding UTF8
$original = $content

# A PEP 508 direct reference on Windows needs a file:/// URI with forward
# slashes, or pip rejects the specifier.
$albrooksUri = 'file:///' + ($albrooksPath -replace '\\', '/')
$autoTradeUri = 'file:///' + ($autoTradePath -replace '\\', '/')

$content = $content -replace '(?m)^albrooks = \[.*\]$', "albrooks = [`"albrooks @ $albrooksUri`"]"
$content = $content -replace '(?m)^auto-trade = \[.*\]$', "auto-trade = [`"auto-trade @ $autoTradeUri`"]"

if ($content -eq $original) {
    Write-Fail 'Could not find the albrooks / auto-trade extras in pyproject.toml to rewrite.'
}

[System.IO.File]::WriteAllText($pyproject, $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  albrooks     -> $albrooksUri"
Write-Host "  auto-trade   -> $autoTradeUri"

# -- 4. Virtual environment ---------------------------------------------------

Write-Step 'Creating the virtual environment'

$venvPath = Join-Path $repoRoot '.venv'
if (Test-Path -LiteralPath $venvPath) {
    Write-Host '  .venv already exists; reusing it.'
}
else {
    & $python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'Failed to create the virtual environment.'
    }
}

$venvPython = Join-Path $venvPath 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $venvPython = Join-Path $venvPath 'bin\python'
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Fail 'Could not find the virtual environment interpreter.'
}

# -- 5. Install ---------------------------------------------------------------

if ($SkipInstall) {
    Write-Step 'Skipping install (-SkipInstall)'
    Write-Host 'Run this when you are ready:'
    Write-Host "  & '$venvPython' -m pip install -e '.[dev]'"
    exit 0
}

Write-Step 'Installing the bridge and its development dependencies'

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e "$repoRoot[dev]"

if ($LASTEXITCODE -ne 0) {
    Write-Fail 'Installing the bridge failed.'
}

# The upstreams are installed separately so that a failure in either is reported
# against the project that caused it, rather than as one opaque resolver error.
Write-Step 'Installing the upstream projects (editable)'
& $venvPython -m pip install -e $albrooksPath
& $venvPython -m pip install -e $autoTradePath
if ($LASTEXITCODE -ne 0) {
    Write-Fail 'Installing an upstream project failed.'
}

if (-not $SkipWindowsExtras) {
    Write-Step 'Installing the Windows-only extras (MetaTrader5, pywinauto)'
    # Optional on purpose. The domain and the whole unit test suite run without
    # these, and a contributor who is not wiring up MetaTrader should not have
    # to install it.
    & $venvPython -m pip install 'MetaTrader5>=5.0.45' 'pywinauto>=0.6.9'
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'WARNING: the Windows extras failed to install.' -ForegroundColor Yellow
        Write-Host 'The unit test suite still runs; only the live MT5 adapter needs these.' -ForegroundColor Yellow
    }
}

# -- 6. Verify ----------------------------------------------------------------

Write-Step 'Verifying'

& $venvPython -c @"
import signal_to_trade_bridge as stb
print(f'  signal-to-trade-bridge {stb.__version__}')
import albrooks
print(f'  albrooks {albrooks.__version__}')
import auto_trade
print(f'  auto-trade {auto_trade.__version__}')
"@

if ($LASTEXITCODE -ne 0) {
    Write-Fail 'The packages did not all import. Check the paths above.'
}

& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) {
    Write-Fail 'The test suite failed.'
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host '  Activate:      .\.venv\Scripts\Activate.ps1'
Write-Host '  Run the tests:  .\.venv\Scripts\python.exe -m pytest'
Write-Host '  Next step:      Phase 2 -- the Al Brooks signal adapter.'
Write-Host ''
Write-Host '  Copy .env.example to .env and fill it in before running anything'
Write-Host '  that touches MetaTrader 5.'
